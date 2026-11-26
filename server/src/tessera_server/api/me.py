"""Authenticated admin identity."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request

from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.oidc import oidc_configured
from tessera_server.services.secrets import parse_secret, secret_matches
from tessera_server.services.sessions import resolve_admin_session
from tessera_server.ui.auth import SESSION_COOKIE

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_Method = Literal["key", "oidc", "dev"]


def me_router() -> APIRouter:
    """Build the caller-identity route.

    Returns:
        A router with `GET /v1/me`.
    """
    router = APIRouter(tags=["admin"])

    @router.get("/v1/me", summary="Return the authenticated admin")
    def read_me(request: Request) -> dict[str, object]:
        """Return who the caller is authenticated as.

        Accepts a bearer admin key or a UI session cookie. Development
        loopback without a credential reports `method` `dev`.

        Args:
            request: Current HTTP request.

        Returns:
            `actor`, `method` (`key`, `oidc`, or `dev`), and `admin_key_id`
            (null for SSO and development loopback). Cookie sessions also
            include `csrf` for console mutating calls. When
            `TESSERA_SERVER_TRACE_URL` is set, the response includes
            `trace_url` (a template with `{trace_id}` / `{span_id}`).
        """
        return _identity(request)

    return router


def _identity(request: Request) -> dict[str, object]:
    """Resolve the caller to an actor document.

    Args:
        request: Current HTTP request.

    Returns:
        Identity fields for `/v1/me`.

    Raises:
        ApiError: Status 401 when no usable credential is present.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() == "bearer" and credential:
        parsed = parse_secret(credential, prefix="tsa")
        if parsed is not None:
            key_id, secret = parsed
            with request.app.state.sessions() as session:
                row = session.get(AdminKey, key_id)
                live = (
                    row is not None
                    and not row.revoked
                    and secret_matches(row.secret_sha256, secret)
                )
                if live:
                    return _with_trace_url(
                        {
                            "actor": f"admin:{row.id}",
                            "method": "key",
                            "admin_key_id": row.id,
                        },
                        request,
                    )
            raise ApiError(401, "invalid_token", "admin key is required")

    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie:
        settings = request.app.state.settings
        with request.app.state.sessions() as session:
            signed_in = resolve_admin_session(session, cookie, idle=settings.session_idle)
        if signed_in is not None:
            is_oidc = signed_in.actor.startswith("oidc:")
            if signed_in.admin_key is None and not (is_oidc and oidc_configured(settings)):
                raise ApiError(401, "invalid_token", "admin key is required")
            method: _Method = "oidc" if is_oidc else "key"
            admin_key_id = signed_in.admin_key.id if signed_in.admin_key is not None else None
            return _with_trace_url(
                {
                    "actor": signed_in.actor,
                    "method": method,
                    "admin_key_id": admin_key_id,
                    "csrf": signed_in.csrf,
                },
                request,
            )

    client = request.client.host if request.client is not None else ""
    if request.app.state.settings.dev and client in _LOOPBACK:
        return _with_trace_url(
            {"actor": "dev", "method": "dev", "admin_key_id": None},
            request,
        )

    raise ApiError(401, "invalid_token", "admin key is required")


def _with_trace_url(body: dict[str, object], request: Request) -> dict[str, object]:
    """Attach `trace_url` when the server is configured with a template.

    Args:
        body: Identity document already filled.
        request: Current request (reads `settings.trace_url`).

    Returns:
        The same document, plus `trace_url` when set.
    """
    template = request.app.state.settings.trace_url
    if template:
        body["trace_url"] = template
    return body


__all__ = ["me_router"]
