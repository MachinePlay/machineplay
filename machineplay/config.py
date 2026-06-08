import os
import ssl
from uuid import uuid4

import certifi

RUNNER_ID = uuid4()
BACKEND_URL = os.environ.get("BACKEND_URL", "wss://api.machineplay.org/ws")
MAX_GAMES = int(os.environ.get("MAX_GAMES") or (os.cpu_count() or 1))
FASTCHESS_PATH = os.environ.get("FASTCHESS_PATH", "fastchess")

# REST API base for the CLI dev tool (login/upload). The website the user logs
# into to copy a token.
API_BASE_URL = os.environ.get("MACHINEPLAY_API_URL", "https://api.machineplay.org")
WEB_URL = os.environ.get("MACHINEPLAY_WEB_URL", "https://machineplay.org")

# Docker registry engines are pushed to. `machineplay login` runs `docker login`
# against this host with the API token, and `machineplay upload` tags/pushes
# images here as `<host>/<login>/<engine>:<version>`.
REGISTRY_HOST = os.environ.get("MACHINEPLAY_REGISTRY", "registry.machineplay.org")

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
