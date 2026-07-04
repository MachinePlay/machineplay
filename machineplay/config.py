import json
import os
import ssl
from uuid import UUID, uuid4

import certifi

from machineplay.credentials import config_dir


def _load_or_create_runner_id() -> UUID:
    """The runner's stable id, persisted so it survives restarts.

    A fresh uuid4 every start would register a brand-new runner in the backend on
    each restart, so we persist it to ``~/.config/machineplay/runner.json`` and
    reuse it. Set ``RUNNER_ID`` in the environment to pin it explicitly (the
    production runner does this declaratively via its systemd unit)."""
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

# How often (seconds) the runner samples and reports CPU/RAM utilization.
TELEMETRY_INTERVAL = float(os.environ.get("TELEMETRY_INTERVAL", "2.0"))
FASTCHESS_PATH = os.environ.get("FASTCHESS_PATH", "fastchess")

# REST API base for the CLI dev tool (login/upload). The website the user logs
# into to copy a token.
API_BASE_URL = os.environ.get("MACHINEPLAY_API_URL", "https://api.machineplay.org")
WEB_URL = os.environ.get("MACHINEPLAY_WEB_URL", "https://machineplay.org")

# Docker registry engines are pushed to and pulled from. `machineplay login`
# runs `docker login` against this host with the API token, `machineplay upload`
# tags/pushes images here as `<host>/<login>/<engine>:<version>`, and the runner
# pulls `<host>/<repository>@<digest>` to play them. A co-located runner can
# point this at the local registry (e.g. `127.0.0.1:5000`); a remote runner pulls
# over the public host (pulls are public, no auth needed).
REGISTRY_HOST = os.environ.get("MACHINEPLAY_REGISTRY", "registry.machineplay.org")

# Per-engine container resource limits, passed to `docker run`. Each engine
# plays in its own sandboxed container; these cap what one can consume.
ENGINE_MEMORY = os.environ.get("ENGINE_MEMORY", "512m")
ENGINE_CPUS = os.environ.get("ENGINE_CPUS", "1")


def _available_cpus() -> list[int]:
    """Host CPUs game slots are pinned to (slot i → AVAILABLE_CPUS[i mod n]).

    Respects the runner process's own affinity mask where the platform exposes
    it, so a systemd `CPUAffinity=` on the unit also confines the engine
    containers."""
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


AVAILABLE_CPUS = _available_cpus()

# Safety-net timeouts (seconds). A wedged `docker pull` or a hung fastchess
# would otherwise hold a game slot forever; the wallclock cap for a game is
# derived from its time control in game.py, PULL_TIMEOUT applies per image.
PULL_TIMEOUT = float(os.environ.get("PULL_TIMEOUT", "600"))


def pull_ref(repository: str, digest: str) -> str:
    """Fully-qualified, digest-pinned image reference the runner pulls/runs."""
    return f"{REGISTRY_HOST}/{repository}@{digest}"


# Reconnect backoff (seconds). Full jitter: sleep ~ U(0, delay), delay doubles
# up to RECONNECT_MAX. A session that stayed up at least RECONNECT_RESET_AFTER
# is considered healthy, so its drop (e.g. a backend hot-reload) resets backoff.
RECONNECT_BASE = float(os.environ.get("RECONNECT_BASE", "0.5"))
RECONNECT_MAX = float(os.environ.get("RECONNECT_MAX", "30.0"))
RECONNECT_RESET_AFTER = float(os.environ.get("RECONNECT_RESET_AFTER", "5.0"))


def make_ssl_context() -> ssl.SSLContext | None:
    """TLS context for wss:// backends; None for plain ws://."""
    if BACKEND_URL.startswith("wss://"):
        return ssl.create_default_context(cafile=certifi.where())
    return None
