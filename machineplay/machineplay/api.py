"""Thin HTTP client for the machineplay REST API (CLI login/upload)."""

from pathlib import Path
from typing import Any

import httpx

from machineplay.config import API_BASE_URL


class ApiError(Exception):
    """A non-2xx response from the API, with a human-readable message."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    message = f"HTTP {resp.status_code}"
    try:
        body = resp.json()
        # AppException handler shape: {"error": {"message": ...}}
        message = body.get("error", {}).get("message") or body.get("detail") or message
    except Exception:
        pass
    raise ApiError(message)


def get_me(token: str) -> dict[str, Any]:
    """Fetch the authenticated user; raises ApiError on an invalid token."""
    resp = httpx.get(
        f"{API_BASE_URL}/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    _raise_for_status(resp)
    return resp.json()


def upload_engine(
    token: str, name: str, version: str, tar_path: Path
) -> dict[str, Any]:
    """Upload a `docker save` tarball as engine `<name>` version `version`."""
    with tar_path.open("rb") as f:
        resp = httpx.post(
            f"{API_BASE_URL}/engine/upload",
            headers={"Authorization": f"Bearer {token}"},
            data={"name": name, "version": version},
            files={"image": (tar_path.name, f, "application/x-tar")},
            timeout=None,
        )
    _raise_for_status(resp)
    return resp.json()
