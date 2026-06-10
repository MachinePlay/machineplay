"""`machineplay upload`: build the engine's image and push it to the registry.

Flow:
  1. require a saved token (`machineplay login`)
  2. `docker build` the Dockerfile in the current directory
  3. smoke-test the image: send `uci`, expect an `id name` reply
  4. ask for the engine name (default: current directory name) and a version
     (default: timestamp). The name groups versions: uploading under the same
     name again adds a version to the same engine, so keep it stable — don't
     bake a release number into it.
  5. tag the image as `<registry>/<login>/<slug>:<version>`, `docker push` it,
     then tell the API to record the engine version (repository + digest)

Authentication for the push itself comes from `docker login` (run for you by
`machineplay login`); the registry's token endpoint only grants push under your
own namespace.
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from machineplay import api, credentials
from machineplay.config import REGISTRY_HOST, WEB_URL

LOCAL_TAG = "machineplay-local:latest"
_ID_NAME_RE = re.compile(r"^id name (.+)$", re.MULTILINE)
_TAG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _slugify(name: str) -> str:
    """Turn a display name into a docker repository path component."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "engine"


def _sanitize_tag(version: str) -> str:
    """Coerce a version string into a valid docker tag."""
    tag = _TAG_RE.sub("-", version).strip("-._")
    return (tag or "latest")[:128]


def docker_login(token: str, login: str) -> bool:
    """`docker login` the registry with the API token. Returns True on success."""
    user = login or "machineplay"
    proc = subprocess.run(
        ["docker", "login", REGISTRY_HOST, "-u", user, "--password-stdin"],
        input=token,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"docker login failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return False
    return True


def _read_engine_name() -> str:
    """Run the built image and parse the UCI `id name` reply.

    A smoke test that the image actually speaks UCI over stdio; the reply is
    only shown as info. It is NOT used as the engine name — it usually embeds
    a release number ("Stockfish 17.1"), which would split every release into
    a separate engine instead of versions of one.
    """
    proc = subprocess.run(
        ["docker", "run", "--rm", "-i", LOCAL_TAG],
        input="uci\nquit\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = _ID_NAME_RE.search(proc.stdout)
    if not match:
        _fail(
            "could not read 'id name' from the engine's UCI response.\n"
            f"--- engine output ---\n{proc.stdout}{proc.stderr}"
        )
    return match.group(1).strip()  # type: ignore[union-attr]


def _image_size() -> int:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.Size}}", LOCAL_TAG],
        capture_output=True,
        text=True,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def _read_pushed_digest(ref: str, repository: str) -> str:
    """Read the registry digest docker recorded for `ref` after a push.

    `RepoDigests` holds `<registry>/<repo>@sha256:…` entries; pick the one for
    our repository so a multi-tagged image can't return a stray digest.
    """
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{json .RepoDigests}}", ref],
        capture_output=True,
        text=True,
    )
    prefix = f"{REGISTRY_HOST}/{repository}@"
    try:
        for entry in json.loads(proc.stdout or "[]"):
            if entry.startswith(prefix):
                return entry[len(prefix) :]
    except json.JSONDecodeError:
        pass
    _fail("could not read the pushed image digest from `docker inspect`.")
    raise AssertionError  # _fail raises; keeps the type checker happy


def do_upload() -> None:
    creds = credentials.load()
    if creds is None:
        _fail("not logged in. Run `machineplay login` first.")
    assert creds is not None  # for type-checkers
    if not creds.login:
        _fail("missing login in credentials. Run `machineplay login` again.")

    if not Path("Dockerfile").is_file():
        _fail("no Dockerfile in the current directory. cd into your engine folder.")

    print(f"building {LOCAL_TAG} …")
    if subprocess.run(["docker", "build", "-t", LOCAL_TAG, "."]).returncode != 0:
        _fail("docker build failed.")

    uci_name = _read_engine_name()
    print(f"UCI id name: {uci_name}")

    # Slugified: the backend requires URL-safe lowercase names — they live at
    # machineplay.org/{login}/{engine}.
    default_name = _slugify(Path.cwd().name)
    name = input(f"engine name [{default_name}]: ").strip() or default_name

    default_version = datetime.now().strftime("%Y-%m-%d-%H-%M")
    version = input(f"version [{default_version}]: ").strip() or default_version
    tag = _sanitize_tag(version)

    repository = f"{creds.login.lower()}/{_slugify(name)}"
    ref = f"{REGISTRY_HOST}/{repository}:{tag}"

    if subprocess.run(["docker", "tag", LOCAL_TAG, ref]).returncode != 0:
        _fail("docker tag failed.")

    print(f"pushing {ref} …")
    # Stream push progress straight to the terminal (large images take a while).
    if subprocess.run(["docker", "push", ref]).returncode != 0:
        _fail(
            "docker push failed. Make sure docker is running and you've run "
            "`machineplay login`."
        )

    digest = _read_pushed_digest(ref, repository)

    print(f"recording {repository}:{tag} ({digest[:19]}…) …")
    try:
        result = api.register_engine(
            creds.token, name, version, repository, digest, _image_size()
        )
    except api.ApiError as exc:
        _fail(str(exc))

    print(f"done → {result.get('url', WEB_URL)}")
