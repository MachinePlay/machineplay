"""`machineplay upload`: build the engine's image and push it to the registry.

Flow:
  1. require a saved token (`machineplay login`)
  2. `docker build` the Dockerfile in the current directory
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
import re
import subprocess
from datetime import datetime
from pathlib import Path

from machineplay import api, credentials, log, proc
from machineplay.config import REGISTRY_HOST, WEB_URL

LOCAL_TAG = "machineplay-local:latest"
_ID_NAME_RE = re.compile(r"^id name (.+)$", re.MULTILINE)
_TAG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
# Mirrors ENGINE_NAME_RE in the backend (app/engines.py). Checked here so a bad
# name fails before a multi-hundred-megabyte push, not after it.
_ENGINE_NAME_RE = re.compile(r"^[a-z0-9](?:[._-]?[a-z0-9]){0,63}$")
_UCI_TIMEOUT = 30.0


def _slugify(name: str) -> str:
    """Turn a display name into a valid engine name / docker path component."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64].strip("-") or "engine"


def _sanitize_tag(version: str) -> str:
    """Coerce a version string into a valid docker tag."""
    tag = _TAG_RE.sub("-", version).strip("-._")
    return (tag or "latest")[:128]


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
    try:
        result = proc.run(
            ["docker", "run", "--rm", "-i", LOCAL_TAG],
            stdin_text="uci\nquit\n",
            capture=True,
            timeout=_UCI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.die(
            f"the engine did not answer `uci` within {_UCI_TIMEOUT:.0f}s.",
            "a UCI engine must print `id name …` and `uciok` right after `uci`",
            "check that the image's CMD/ENTRYPOINT starts the engine on stdio",
        )
    match = _ID_NAME_RE.search(result.stdout)
    if match:
        return match.group(1).strip()
    log.die(
        "could not read `id name` from the engine's UCI response.",
        "the container must read UCI commands on stdin and reply on stdout",
        f"reproduce with: docker run --rm -i {LOCAL_TAG}",
    )


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
        for entry in json.loads(result.stdout or "[]"):
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
    if proc.run(["docker", "build", "-t", LOCAL_TAG, "."]).returncode != 0:
        log.die("docker build failed — see the build output above.")

    log.info(f"UCI id name: {_read_engine_name()}")

    # Engine names are URL slugs: they live at machineplay.org/{login}/{engine}.
    name = _ask_engine_name(_slugify(Path.cwd().name))

    default_version = datetime.now().strftime("%Y-%m-%d-%H-%M")
    version = log.prompt("version", default_version)
    tag = _sanitize_tag(version)
    if tag != version:
        log.warn(f"version tagged as '{tag}' (docker tags can't hold '{version}')")

    repository = f"{creds.login.lower()}/{name}"
    ref = f"{REGISTRY_HOST}/{repository}:{tag}"

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

    log.info(f"registering {repository}:{tag} ({digest[:19]}…)")
    try:
        result = api.register_engine(
            creds.token, name, version, repository, digest, _image_size()
        )
    except api.ApiError as exc:
        log.die(f"the API rejected the upload: {exc}")

    log.info(f"done → {result.get('url', WEB_URL)}")
