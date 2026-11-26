"""CSRF checks for admin UI POST requests."""

from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from tessera_server.config import Settings
from tessera_server.services.sessions import resolve_admin_session
from tessera_server.ui.auth import SESSION_COOKIE

_HEADER = "x-csrf-token"
_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_MESSAGE = "csrf token is required"


class CsrfFailedError(Exception):
    """The CSRF token is missing or does not match the session."""


async def require_csrf(
    request: Request,
    csrf: Annotated[str, Form()] = "",
) -> None:
    """Reject a mutating UI request whose CSRF token does not match the session.

    Development loopback stays exempt. Callers without a session are left for
    the login redirect. The token may arrive as the form field `csrf` or as the
    `X-CSRF-Token` header (htmx).

    Args:
        request: Current HTTP request.
        csrf: Hidden form field value.

    Raises:
        CsrfFailedError: When a signed-in session is present and the token is
            wrong or missing.
    """
    settings = cast("Settings", request.app.state.settings)
    client = request.client.host if request.client is not None else ""
    if settings.dev and client in _LOOPBACK:
        return
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw:
        return
    with request.app.state.sessions() as session:
        admin = resolve_admin_session(session, raw, idle=settings.session_idle)
    if admin is None:
        return
    request.state.csrf = admin.csrf
    header = request.headers.get(_HEADER, "").strip()
    token = header or csrf.strip()
    if not token or not secrets.compare_digest(token, admin.csrf):
        raise CsrfFailedError


CSRF_DEPENDENCY = Depends(require_csrf)


def install_csrf(app: FastAPI) -> None:
    """Register the 403 handler for failed CSRF checks.

    Args:
        app: Application that serves `/ui`.
    """

    @app.exception_handler(CsrfFailedError)
    async def render_csrf(_request: Request, _exc: CsrfFailedError) -> HTMLResponse:
        """Show a fixed 403 page when a handler raises `CsrfFailedError`.

        Args:
            _request: Current HTTP request.
            _exc: CSRF failure.

        Returns:
            The error page.
        """
        body = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8" />'
            f"<title>Error</title></head><body><h1>Error</h1>"
            f'<p role="alert">{_MESSAGE}</p></body></html>'
        )
        return HTMLResponse(body, status_code=403)


__all__ = ["CSRF_DEPENDENCY", "CsrfFailedError", "install_csrf", "require_csrf"]
