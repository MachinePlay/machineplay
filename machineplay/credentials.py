"""The API token `machineplay login` saves, mode 0600."""

import json
import os
from pathlib import Path
from typing import NamedTuple


class Credentials(NamedTuple):
    token: str
    login: str


def config_dir() -> Path:
    """``${XDG_CONFIG_HOME:-~/.config}/machineplay``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "machineplay"


def _path() -> Path:
    return config_dir() / "credentials.json"


def load() -> Credentials | None:
    try:
        data = json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    token = data.get("token")
    if not token:
        return None
    return Credentials(token=token, login=data.get("login", ""))


def save(creds: Credentials) -> Path:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": creds.token, "login": creds.login}))
    path.chmod(0o600)
    return path


def clear() -> bool:
    """True if a file was removed."""
    try:
        _path().unlink()
        return True
    except FileNotFoundError:
        return False
