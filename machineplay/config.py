import json
import os
import ssl
from uuid import UUID, uuid4

import certifi

from machineplay.credentials import config_dir


def _load_or_create_runner_id() -> UUID:
    """Persisted so a restart isn't registered as a new runner."""
    path = config_dir() / "runner.json"
    try:
        return UUID(json.loads(path.read_text())["runner_id"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        runner_id = uuid4()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"runner_id": str(runner_id)}))
        return runner_id


_env_runner_id = os.environ.get("RUNNER_ID")
RUNNER_ID = UUID(_env_runner_id) if _env_runner_id else _load_or_create_runner_id()
BACKEND_URL = os.environ.get("BACKEND_URL", "wss://api.machineplay.org/ws")
MAX_GAMES = int(os.environ.get("MAX_GAMES") or (os.cpu_count() or 1))

TELEMETRY_INTERVAL = float(os.environ.get("TELEMETRY_INTERVAL", "2.0"))
FASTCHESS_PATH = os.environ.get("FASTCHESS_PATH", "fastchess")

API_BASE_URL = os.environ.get("MACHINEPLAY_API_URL", "https://api.machineplay.org")
WEB_URL = os.environ.get("MACHINEPLAY_WEB_URL", "https://machineplay.org")

# Pulls are anonymous; a co-located runner can use the local `127.0.0.1:5000`.
REGISTRY_HOST = os.environ.get("MACHINEPLAY_REGISTRY", "registry.machineplay.org")

# Runners are amd64, and a digest-pinned pull skips docker's platform check, so a
# foreign image would only fail at game start with `exec format error`.
ENGINE_PLATFORM = os.environ.get("MACHINEPLAY_PLATFORM", "linux/amd64")

# Seconds `upload` waits for `uci`; None picks a default, longer when emulated.
_uci_timeout = os.environ.get("MACHINEPLAY_UCI_TIMEOUT")
UCI_TIMEOUT = float(_uci_timeout) if _uci_timeout else None

ENGINE_MEMORY = os.environ.get("ENGINE_MEMORY", "512m")
ENGINE_CPUS = os.environ.get("ENGINE_CPUS", "1")


def _available_cpus() -> list[int]:
    """Host CPUs game slots are pinned to. Respects the process's affinity, so
    a systemd `CPUAffinity=` also confines the engine containers."""
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


AVAILABLE_CPUS = _available_cpus()

# Per image, so a wedged pull can't hold a slot forever.
PULL_TIMEOUT = float(os.environ.get("PULL_TIMEOUT", "600"))


def pull_ref(repository: str, digest: str) -> str:
    return f"{REGISTRY_HOST}/{repository}@{digest}"


# Full-jitter backoff. A session that lasted RECONNECT_RESET_AFTER resets it,
# so a backend hot-reload reconnects quickly.
RECONNECT_BASE = float(os.environ.get("RECONNECT_BASE", "0.5"))
RECONNECT_MAX = float(os.environ.get("RECONNECT_MAX", "30.0"))
RECONNECT_RESET_AFTER = float(os.environ.get("RECONNECT_RESET_AFTER", "5.0"))


def make_ssl_context() -> ssl.SSLContext | None:
    """TLS context for wss:// backends; None for plain ws://."""
    if BACKEND_URL.startswith("wss://"):
        return ssl.create_default_context(cafile=certifi.where())
    return None
