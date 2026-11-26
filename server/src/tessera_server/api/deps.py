"""Authentication dependencies."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.oidc import oidc_configured
from tessera_server.services.secrets import parse_secret, secret_matches
from tessera_server.services.sessions import resolve_admin_session
from tessera_server.ui.auth import SESSION_COOKIE, safe_next_path

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_OIDC_PREFIX = "oidc:"


def require_admin(request: Request) -> AdminKey | None:
    """Require an admin credential for a `/v1` route.

    Order: `Authorization: Bearer tsa_…`, then the `tessera_admin` session
    cookie, then development loopback without a credential. Cookie callers
    must send a matching `X-CSRF-Token` on non-GET/HEAD requests.

    Args:
        request: Current HTTP request.

    Returns:
        The admin key row when the caller used a key (bearer or key session),
        or `None` for SSO sessions and development loopback.

    Raises:
        ApiError: Status 401 when no usable credential is present, or 403
            `csrf_failed` when a cookie caller omits or mismatches CSRF.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() == "bearer" and credential:
        return _admin_from_bearer(request, credential)

    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie:
        return _admin_from_cookie(request, cookie)

    client = request.client.host if request.client is not None else ""
    if request.app.state.settings.dev and client in _LOOPBACK:
        request.state.admin_actor = "dev"
        return None
    raise ApiError(401, "invalid_token", "admin key is required")


def get_session(request: Request) -> Iterator[Session]:
    """Open a database session for the request.

    Args:
        request: Current HTTP request.

    Yields:
        A session closed when the request finishes.
    """
    with request.app.state.sessions() as session:
        yield session


def _admin_from_bearer(request: Request, credential: str) -> AdminKey | None:
    """Authenticate with a bearer admin key.

    Args:
        request: Current HTTP request.
        credential: Token after `Bearer `.

    Returns:
        The admin key row.

    Raises:
        ApiError: Status 401 when the key is unusable.
    """
    parsed = parse_secret(credential, prefix="tsa")
    if parsed is None:
        raise ApiError(401, "invalid_token", "admin key is required")
    key_id, secret = parsed
    with request.app.state.sessions() as session:
        row = cast("AdminKey | None", session.get(AdminKey, key_id))
        if row is None or row.revoked or not secret_matches(row.secret_sha256, secret):
            raise ApiError(401, "invalid_token", "admin key is required")
        row.last_used_at = datetime.now(UTC)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        request.state.admin_actor = f"admin:{row.id}"
        return row


def _admin_from_cookie(request: Request, cookie: str) -> AdminKey | None:
    """Authenticate with a UI session cookie and enforce CSRF on writes.

    Args:
        request: Current HTTP request.
        cookie: Raw `tessera_admin` cookie value.

    Returns:
        The session's admin key when the session was created from a key,
        otherwise `None`.

    Raises:
        ApiError: Status 401 when the session is unusable, or 403
            `csrf_failed` when CSRF fails on a mutating method.
    """
    settings = request.app.state.settings
    with request.app.state.sessions() as session:
        signed_in = resolve_admin_session(session, cookie, idle=settings.session_idle)
        if signed_in is None:
            raise ApiError(401, "invalid_token", "admin key is required")
        key = signed_in.admin_key
        csrf = signed_in.csrf
        actor = signed_in.actor
    if key is None and not (actor.startswith(_OIDC_PREFIX) and oidc_configured(settings)):
        raise ApiError(401, "invalid_token", "admin key is required")
    request.state.admin_actor = actor
    request.state.csrf = csrf
    if request.method.upper() not in _SAFE_METHODS:
        token = request.headers.get(_CSRF_HEADER, "").strip()
        if not token or not secrets.compare_digest(token, csrf):
            raise ApiError(403, "csrf_failed", "csrf token is required")
    return key


__all__ = ["get_session", "require_admin", "safe_next_path"]
