"""Helpers for auth UI tests that need a CSRF token."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi.testclient import TestClient


def read_csrf(client: TestClient) -> str:
    """Read the session CSRF token from ``GET /v1/me``.

    Args:
        client: Signed-in test client.

    Returns:
        The session CSRF token.
    """
    me = client.get("/v1/me")
    assert me.status_code == 200, me.text
    token = me.json().get("csrf")
    assert isinstance(token, str), "csrf missing from /v1/me"
    assert token, "csrf empty on /v1/me"
    return token


def ui_form(
    client: TestClient,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a form body that includes a fresh CSRF token.

    Args:
        client: Signed-in test client.
        data: Other form fields.

    Returns:
        A dict safe to pass as ``data=`` to ``client.post``.
    """
    body = dict(data or {})
    body["csrf"] = read_csrf(client)
    return body
