"""`machineplay upload`: build the engine's image and push it to the registry.

Flow:
  1. require a saved token (`machineplay login`)
  2. `docker build` the Dockerfile in the current directory for the platform
     runners can exec (`ENGINE_PLATFORM`), then check the image really came out
     that way
  3. smoke-test the image: send `uci`, expect an `id name` reply
  4. ask for the engine name (default: current directory name) and a version
     (default: timestamp). The name groups versions: uploading under the same
     name again adds a version to the same engine, so keep it stable — don't
     bake a release number into it.
  5. tag the image as `<registry>/<login>/<name>:<version>`, `docker push` it,
     then tell the API to record the engine version (repository + digest)

Every docker invocation is echoed (`> docker build …`) before it runs — see
:mod:`machineplay.log`.

Authentication for the push itself comes from `docker login` (run for you by
`machineplay login`); the registry's token endpoint only grants push under your
own namespace.
"""

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
# Mirror ENGINE_NAME_RE / ENGINE_VERSION_RE in the backend (app/engines.py).
# Checked here so a bad name or version fails before a multi-hundred-megabyte
# push, not after it.
_ENGINE_NAME_RE = re.compile(r"^[a-z0-9](?:[._-]?[a-z0-9]){0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
# `docker build` and `docker run` both default to the host's architecture, so
# every invocation that produces or executes the image says which platform it
# means. See ENGINE_PLATFORM in config.py for why it matters.
_PLATFORM_ARGS = ["--platform", ENGINE_PLATFORM]
# Emulated builds run an engine through Rosetta/qemu, where answering `uci` can
# take a lot longer than it does natively.
_UCI_TIMEOUT = 30.0
_UCI_TIMEOUT_EMULATED = 120.0
# `uname -m` and docker disagree on how to spell an architecture.
_ARCH_ALIASES = {"x86_64": "amd64", "aarch64": "arm64", "armv8": "arm64"}


def _arch(name: str) -> str:
    """Docker's spelling of an architecture name."""
    arch = name.strip().lower()
    return _ARCH_ALIASES.get(arch, arch)


def _platform_id(value: str) -> str:
    """`os/arch` from a platform string, dropping any variant (`linux/arm64/v8`)."""
    os_name, _, rest = value.strip().lower().partition("/")
    return f"{os_name}/{_arch(rest.partition('/')[0])}"


def _emulated() -> bool:
    """Does docker have to emulate ENGINE_PLATFORM on this machine?

    Containers are always Linux — on macOS and Windows docker runs its own
    Linux VM — so only the architecture decides this.
    """
    return _platform_id(ENGINE_PLATFORM).partition("/")[2] != _arch(platform.machine())


def _check_platform() -> None:
    """Fail before the push if the image isn't stamped for ENGINE_PLATFORM.

    An assertion that `--platform` on the build actually took effect: docker
    normally honours it, but the platform is what the runner's `docker pull`
    will trust, and an image the runner can't exec is worth catching here
    rather than as an aborted game with nothing useful to show for it.

    This reads the platform docker recorded, so it can't see *inside* the
    image — a Dockerfile whose `FROM --platform=…` pins a foreign base still
    gets stamped with the build's target platform. The UCI smoke test is what
    catches that one.
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
    """The last few lines of a child's output, for a one-line-ish hint.

    (game.py has its own copy for the runner's byte streams; importing it here
    would pull asyncio and python-chess into every CLI command's startup.)
    """
    return " / ".join(output.strip().splitlines()[-lines:])


def _slugify(name: str) -> str:
    """Turn a display name into a valid engine name / docker path component."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64].strip("-") or "engine"


def _sanitize_tag(version: str) -> str:
    """Coerce a version string into one that satisfies _VERSION_RE."""
    tag = _TAG_RE.sub("-", version).strip("-._")
    return (tag or "latest")[:64]


def docker_login(token: str, login: str) -> bool:
    """`docker login` the registry with the API token. Returns True on success."""
    user = login or "machineplay"
    log.info(
        f"authenticating docker with {REGISTRY_HOST} "
        f"(this writes a credential entry to ~/.docker/config.json)"
    )
    # The token goes in over stdin, so the echoed command line holds no secret.
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
    """`docker logout` the registry. Returns True on success."""
    result = proc.run(["docker", "logout", REGISTRY_HOST], capture=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        log.warn(f"docker logout failed: {detail or 'no output'}")
        return False
    return True


def _read_engine_name() -> str:
    """Run the built image and parse the UCI `id name` reply.

    A smoke test that the image actually speaks UCI over stdio; the reply is
    only shown as info. It is NOT used as the engine name — it usually embeds
    a release number ("Stockfish 17.1"), which would split every release into
    a separate engine instead of versions of one.
    """
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
    # Whatever the container said is the only evidence of why it said nothing
    # useful — an engine that crashed on startup prints its traceback here, and
    # a `FROM --platform=…` line pinning a foreign base shows up as
    # `exec format error` (docker stamps the image with the build's target
    # platform, so _check_platform can't see that one).
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
    """Read the registry digest docker recorded for `ref` after a push.

    `RepoDigests` holds `<registry>/<repo>@sha256:…` entries; pick the one for
    our repository so a multi-tagged image can't return a stray digest.
    """
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
    """Prompt until we have a name the backend will accept."""
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
    """Prompt until we have a version the backend will accept.

    The version doubles as the pushed image's docker tag, so it has to satisfy
    both; _sanitize_tag turns whatever was typed into a usable suggestion.
    """
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

    # Engine names are URL slugs: they live at machineplay.org/{login}/{engine}.
    name = _ask_engine_name(_slugify(Path.cwd().name))

    version = _ask_version(datetime.now().strftime("%Y-%m-%d-%H-%M"))

    repository = f"{creds.login.lower()}/{name}"
    ref = f"{REGISTRY_HOST}/{repository}:{version}"

    if proc.run(["docker", "tag", LOCAL_TAG, ref]).returncode != 0:
        log.die(f"docker tag {LOCAL_TAG} → {ref} failed.")

    log.info(f"pushing {ref}")
    # Stream push progress straight to the terminal (large images take a while).
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
