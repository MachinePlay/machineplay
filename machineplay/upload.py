"""`machineplay upload`: build the Dockerfile here for `ENGINE_PLATFORM`,
smoke-test that it speaks UCI, push it as `<registry>/<login>/<name>:<version>`
and register the version with the API. The push uses the `docker login` that
`machineplay login` ran."""

import json
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

from machineplay import api, credentials, log, proc
from machineplay.config import ENGINE_PLATFORM, REGISTRY_HOST, UCI_TIMEOUT, WEB_URL

LOCAL_TAG = "machineplay-local:latest"
_ID_NAME_RE = re.compile(r"^id name (.+)$", re.MULTILINE)
_TAG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
# Mirror the backend's app/engines.py, so a bad name fails before a long push.
_ENGINE_NAME_RE = re.compile(r"^[a-z0-9](?:[._-]?[a-z0-9]){0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
# `docker build` and `docker run` both default to the host's architecture.
_PLATFORM_ARGS = ["--platform", ENGINE_PLATFORM]
_UCI_TIMEOUT = 30.0
_UCI_TIMEOUT_EMULATED = 120.0
# `uname -m` and docker disagree on how to spell an architecture.
_ARCH_ALIASES = {"x86_64": "amd64", "aarch64": "arm64", "armv8": "arm64"}


def _arch(name: str) -> str:
    arch = name.strip().lower()
    return _ARCH_ALIASES.get(arch, arch)


def _platform_id(value: str) -> str:
    """`os/arch` from a platform string, dropping any variant (`linux/arm64/v8`)."""
    os_name, _, rest = value.strip().lower().partition("/")
    return f"{os_name}/{_arch(rest.partition('/')[0])}"


def _emulated() -> bool:
    """Containers are always Linux (docker runs a VM elsewhere), so only the
    architecture decides."""
    return _platform_id(ENGINE_PLATFORM).partition("/")[2] != _arch(platform.machine())


def _check_platform() -> None:
    """Fail before the push if the image isn't stamped for ENGINE_PLATFORM.

    Only reads the recorded platform: a `FROM --platform=…` foreign base still
    gets the build's stamp, and the UCI smoke test catches that instead.
    """
    result = proc.run(
        ["docker", "inspect", "-f", "{{.Os}}/{{.Architecture}}", LOCAL_TAG],
        capture=True,
    )
    built = result.stdout.strip()
    if not built:
        log.warn("could not read the built image's platform from `docker inspect`")
        return
    if _platform_id(built) != _platform_id(ENGINE_PLATFORM):
        log.die(
            f"the image was built for {built}, but machineplay runners are "
            f"{ENGINE_PLATFORM}.",
            "an engine of the wrong architecture cannot start on a runner",
            "if MACHINEPLAY_PLATFORM is set in your environment, unset it",
        )


def _tail(output: str, lines: int = 3) -> str:
    """The last few lines of output, for a hint. game.py has its own copy, to
    keep python-chess out of CLI startup."""
    return " / ".join(output.strip().splitlines()[-lines:])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64].strip("-") or "engine"


def _sanitize_tag(version: str) -> str:
    tag = _TAG_RE.sub("-", version).strip("-._")
    return (tag or "latest")[:64]


def docker_login(token: str, login: str) -> bool:
    user = login or "machineplay"
    log.info(
        f"authenticating docker with {REGISTRY_HOST} "
        f"(this writes a credential entry to ~/.docker/config.json)"
    )
    # Over stdin, so the echoed command holds no secret.
    proc_result = proc.run(
        ["docker", "login", REGISTRY_HOST, "-u", user, "--password-stdin"],
        stdin_text=token,
        capture=True,
    )
    if proc_result.returncode != 0:
        detail = proc_result.stderr.strip() or proc_result.stdout.strip()
        log.error(f"docker login failed: {detail or 'no output'}")
        return False
    return True


def docker_logout() -> bool:
    result = proc.run(["docker", "logout", REGISTRY_HOST], capture=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        log.warn(f"docker logout failed: {detail or 'no output'}")
        return False
    return True


def _read_engine_name() -> str:
    """Smoke-test the image over UCI and return its `id name`. Not used as the
    engine name: it usually embeds a release number ("Stockfish 17.1")."""
    emulated = _emulated()
    timeout = UCI_TIMEOUT or (_UCI_TIMEOUT_EMULATED if emulated else _UCI_TIMEOUT)
    try:
        result = proc.run(
            ["docker", "run", "--rm", "-i", *_PLATFORM_ARGS, LOCAL_TAG],
            stdin_text="uci\nquit\n",
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        hints = [
            "a UCI engine must print `id name …` and `uciok` right after `uci`",
            "check that the image's CMD/ENTRYPOINT starts the engine on stdio",
        ]
        if emulated:
            hints.append(
                f"this ran under emulation ({ENGINE_PLATFORM} on "
                f"{platform.machine()}), which is slow — a heavier engine may "
                "need MACHINEPLAY_UCI_TIMEOUT raised"
            )
        log.die(f"the engine did not answer `uci` within {timeout:.0f}s.", *hints)
    match = _ID_NAME_RE.search(result.stdout)
    if match:
        return match.group(1).strip()
    hints = [
        "the container must read UCI commands on stdin and reply on stdout",
        f"reproduce with: docker run --rm -i {' '.join(_PLATFORM_ARGS)} {LOCAL_TAG}",
    ]
    # A startup crash prints its traceback here, and a foreign `FROM
    # --platform=…` base shows up as `exec format error`.
    if noise := _tail(result.stderr or result.stdout):
        hints.append(f"the container said: {noise}")
        if "exec format error" in result.stderr:
            hints.append(
                f"that means the executable inside the image isn't "
                f"{ENGINE_PLATFORM} — check for a `FROM --platform=…` line in "
                "your Dockerfile pinning a different architecture"
            )
    log.die("could not read `id name` from the engine's UCI response.", *hints)


def _image_size() -> int:
    result = proc.run(
        ["docker", "inspect", "-f", "{{.Size}}", LOCAL_TAG],
        capture=True,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        log.warn("could not read the image size from `docker inspect`; reporting 0")
        return 0


def _read_pushed_digest(ref: str, repository: str) -> str:
    """The pushed digest, matched on our repository so a multi-tagged image
    can't return a stray one."""
    result = proc.run(
        ["docker", "inspect", "-f", "{{json .RepoDigests}}", ref],
        capture=True,
    )
    prefix = f"{REGISTRY_HOST}/{repository}@"
    try:
        entries: list[str] = json.loads(result.stdout or "[]")
        for entry in entries:
            if entry.startswith(prefix):
                return entry[len(prefix) :]
    except json.JSONDecodeError:
        pass
    log.die(
        f"could not read the pushed digest for {ref} from `docker inspect`.",
        "the push may have gone to a different registry — check the output above",
    )


def _ask_engine_name(default: str) -> str:
    while True:
        name = log.prompt("engine name", default)
        if _ENGINE_NAME_RE.fullmatch(name):
            return name
        log.error(
            f"'{name}' is not a valid engine name: 1-64 characters of a-z, 0-9 "
            "and single interior separators (. _ -)"
        )
        log.info(f"suggestion: {_slugify(name)}")


def _ask_version(default: str) -> str:
    while True:
        version = log.prompt("version", default)
        if _VERSION_RE.fullmatch(version):
            return version
        log.error(
            f"'{version}' is not a valid version: 1-64 characters of letters, "
            "digits, '.', '_' and '-', starting with a letter, digit or '_'"
        )
        log.info(f"suggestion: {_sanitize_tag(version)}")


def do_upload() -> None:
    creds = credentials.load()
    if creds is None:
        log.die("not logged in.", "run `machineplay login` first")
    if not creds.login:
        log.die(
            "the saved credentials have no login.",
            "run `machineplay login` again to refresh them",
        )

    if not Path("Dockerfile").is_file():
        log.die(
            f"no Dockerfile in {Path.cwd()}.",
            "cd into your engine folder (the one with the Dockerfile) and retry",
        )

    log.info(f"building {LOCAL_TAG} from {Path.cwd()}/Dockerfile")
    if _emulated():
        log.info(
            f"this machine is {platform.machine()}, engines run on "
            f"{ENGINE_PLATFORM} — docker will emulate, so the build and the "
            "UCI check take longer than they would natively"
        )
    build = ["docker", "build", *_PLATFORM_ARGS, "-t", LOCAL_TAG, "."]
    if proc.run(build).returncode != 0:
        log.die(
            "docker build failed — see the build output above.",
            f"the image is built for {ENGINE_PLATFORM}, which is what "
            "machineplay runners are; if a base image has no build for that "
            "platform, pick one that does",
        )

    _check_platform()
    log.info(f"UCI id name: {_read_engine_name()}")

    name = _ask_engine_name(_slugify(Path.cwd().name))

    version = _ask_version(datetime.now().strftime("%Y-%m-%d-%H-%M"))

    repository = f"{creds.login.lower()}/{name}"
    ref = f"{REGISTRY_HOST}/{repository}:{version}"

    if proc.run(["docker", "tag", LOCAL_TAG, ref]).returncode != 0:
        log.die(f"docker tag {LOCAL_TAG} → {ref} failed.")

    log.info(f"pushing {ref}")
    if proc.run(["docker", "push", ref]).returncode != 0:
        log.die(
            f"docker push {ref} failed — see the output above.",
            "if it says 'unauthorized', run `machineplay login` again",
            "if it says 'connection refused', check that docker is running",
        )

    digest = _read_pushed_digest(ref, repository)

    log.info(f"registering {repository}:{version} ({digest[:19]}…)")
    try:
        result = api.register_engine(
            creds.token, name, version, repository, digest, _image_size()
        )
    except api.ApiError as exc:
        log.die(f"the API rejected the upload: {exc}")

    log.info(f"done → {result.get('url', WEB_URL)}")
