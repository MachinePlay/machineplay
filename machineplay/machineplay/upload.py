"""`machineplay upload`: build the engine's Dockerfile and upload the image.

Flow:
  1. require a saved token (`machineplay login`)
  2. `docker build` the Dockerfile in the current directory
  3. read the engine name from the UCI `id name` reply
  4. ask for a version (default: timestamp)
  5. `docker save` the image and POST it to the API
"""

import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from machineplay import api, credentials
from machineplay.config import WEB_URL

LOCAL_TAG = "machineplay-local:latest"
_ID_NAME_RE = re.compile(r"^id name (.+)$", re.MULTILINE)


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _read_engine_name() -> str:
    """Run the built image and parse the UCI `id name` reply."""
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


def do_upload() -> None:
    creds = credentials.load()
    if creds is None:
        _fail("not logged in. Run `machineplay login` first.")
    assert creds is not None  # for type-checkers

    if not Path("Dockerfile").is_file():
        _fail("no Dockerfile in the current directory. cd into your engine folder.")

    print(f"building {LOCAL_TAG} …")
    if subprocess.run(["docker", "build", "-t", LOCAL_TAG, "."]).returncode != 0:
        _fail("docker build failed.")

    name = _read_engine_name()
    print(f"engine name (from UCI id): {name}")

    default_version = datetime.now().strftime("%Y-%m-%d-%H-%M")
    version = input(f"version [{default_version}]: ").strip() or default_version

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "engine.tar"
        print("saving image …")
        if (
            subprocess.run(
                ["docker", "save", "-o", str(tar_path), LOCAL_TAG]
            ).returncode
            != 0
        ):
            _fail("docker save failed.")

        size_mb = tar_path.stat().st_size / 1024 / 1024
        print(f"uploading {size_mb:.1f} MB as {creds.login}/{name}:{version} …")
        try:
            result = api.upload_engine(creds.token, name, version, tar_path)
        except api.ApiError as exc:
            _fail(str(exc))

    print(f"done → {result.get('url', WEB_URL)}")
