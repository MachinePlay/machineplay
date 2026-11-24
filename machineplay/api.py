"""Thin HTTP client for the machineplay REST API (CLI login/upload)."""

from typing import Any, cast

import httpx

from machineplay.config import API_BASE_URL


class ApiError(Exception):
    """A non-2xx response or transport failure, with a readable message."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    message = f"HTTP {resp.status_code}"
    try:
        body = resp.json()
        message = body.get("error", {}).get("message") or body.get("detail") or message
    except Exception:
        pass
    if resp.status_code in (401, 403):
        message = f"{message} — the API token was rejected; run `machineplay login`"
    raise ApiError(message)


def _request(method: str, path: str, token: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = httpx.request(
            method,
            f"{API_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise ApiError(
            f"could not reach the API at {API_BASE_URL} ({type(exc).__name__}: {exc}). "
            "Check your connection, or MACHINEPLAY_API_URL if you set it."
        ) from exc
    _raise_for_status(resp)
    return cast(dict[str, Any], resp.json())


def get_me(token: str) -> dict[str, Any]:
    return _request("GET", "/me", token, timeout=15.0)


def register_engine(
    token: str,
    name: str,
    version: str,
    repository: str,
    digest: str,
    size_bytes: int,
) -> dict[str, Any]:
    return _request(
        "POST",
        "/engine/register",
        token,
        json={
            "name": name,
            "version": version,
            "repository": repository,
            "digest": digest,
            "size_bytes": size_bytes,
        },
        timeout=30.0,
    )
