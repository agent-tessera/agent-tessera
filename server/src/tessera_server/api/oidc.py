"""Admin OpenID Connect browser routes."""

from __future__ import annotations

import secrets
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.services.oidc import (
    OidcGroupError,
    OidcLoginError,
    authorization_redirect,
    complete_login,
    oidc_configured,
    open_client,
)
from tessera_server.ui.auth import sign_in

_STATE_COOKIE = "tessera_oidc_state"
_STATE_BYTES = 32
_REDIRECT = 303
_PREFIX = "oidc:"


def oidc_router() -> APIRouter:
    """Build the admin SSO start and callback routes.

    Returns:
        Routes for `/oidc/start` and `/oidc/callback`, plus one-release
        redirects from the legacy `/ui/oidc/*` paths.
    """
    router = APIRouter()
    router.add_api_route("/oidc/start", _start, methods=["GET"])
    router.add_api_route("/oidc/callback", _callback, methods=["GET"])
    router.add_api_route("/ui/oidc/start", _redirect_start, methods=["GET"])
    router.add_api_route("/ui/oidc/callback", _redirect_callback, methods=["GET"])
    return router


def _redirect_start(request: Request) -> RedirectResponse:
    """Redirect legacy `/ui/oidc/start` to `/oidc/start`.

    Args:
        request: Current HTTP request.

    Returns:
        A 303 redirect preserving the query string.
    """
    query = request.url.query
    target = f"/oidc/start?{query}" if query else "/oidc/start"
    return RedirectResponse(target, status_code=_REDIRECT)


def _redirect_callback(request: Request) -> RedirectResponse:
    """Redirect legacy `/ui/oidc/callback` to `/oidc/callback`.

    Args:
        request: Current HTTP request.

    Returns:
        A 303 redirect preserving the query string.
    """
    query = request.url.query
    target = f"/oidc/callback?{query}" if query else "/oidc/callback"
    return RedirectResponse(target, status_code=_REDIRECT)


def _start(request: Request) -> Response:
    """Redirect the browser to the identity provider.

    Args:
        request: Current HTTP request.

    Returns:
        A redirect that stores the OAuth state in a cookie.

    Raises:
        ApiError: Status 404 when SSO is not configured, or 401 when discovery
            fails.
    """
    settings = _settings(request)
    if not oidc_configured(settings):
        raise ApiError(404, "not_found", "SSO is not configured")
    state = secrets.token_urlsafe(_STATE_BYTES)
    try:
        with open_client() as client:
            location = authorization_redirect(settings, state, client)
    except OidcLoginError as err:
        raise ApiError(401, "invalid_token", "sign-in failed") from err
    response = RedirectResponse(location, status_code=_REDIRECT)
    _set_cookie(response, request, _STATE_COOKIE, state)
    return response


def _callback(request: Request, code: str = "", state: str = "") -> Response:
    """Finish SSO and start a server-side session for `oidc:<sub>`.

    Args:
        request: Current HTTP request. The state cookie must match `state`.
        code: Authorization code.
        state: State value returned by the provider.

    Returns:
        A redirect to `/app/overview` with the opaque session cookie.

    Raises:
        ApiError: Status 404 when SSO is not configured, or 401 when the
            state or token is not acceptable.
    """
    settings = _settings(request)
    if not oidc_configured(settings):
        raise ApiError(404, "not_found", "SSO is not configured")
    expected = request.cookies.get(_STATE_COOKIE, "")
    if not _matches(state, expected) or not code:
        raise ApiError(401, "invalid_token", "sign-in failed")
    try:
        with open_client() as client:
            identity = complete_login(settings, code, client)
    except OidcGroupError as err:
        raise ApiError(403, "forbidden", "admin group is required") from err
    except OidcLoginError as err:
        raise ApiError(401, "invalid_token", "sign-in failed") from err
    response = sign_in(
        request,
        actor=f"{_PREFIX}{identity.sub}",
        admin_key_id=None,
        method="oidc",
        audit={"issuer": identity.issuer, "sub": identity.sub},
    )
    response.delete_cookie(
        _STATE_COOKIE,
        path="/",
        samesite="strict",
        httponly=True,
        secure=not settings.issuer.startswith("http://"),
    )
    return response


def _matches(state: str, expected: str) -> bool:
    """Compare OAuth state values without leaking the length.

    Args:
        state: Value from the query string.
        expected: Value from the cookie.

    Returns:
        `True` when both are non-empty and equal.
    """
    if not state or not expected or len(state) != len(expected):
        return False
    return secrets.compare_digest(state, expected)


def _set_cookie(response: Response, request: Request, name: str, value: str) -> None:
    """Store an HttpOnly admin cookie.

    Args:
        response: Response that should carry the cookie.
        request: Current request, used to decide the Secure flag.
        name: Cookie name.
        value: Cookie value.
    """
    response.set_cookie(
        name,
        value,
        httponly=True,
        samesite="strict",
        secure=not _settings(request).issuer.startswith("http://"),
        path="/",
    )


def _settings(request: Request) -> Settings:
    """Read process settings from the application.

    Args:
        request: Current HTTP request.

    Returns:
        The process settings.
    """
    return cast("Settings", request.app.state.settings)


__all__ = ["oidc_router"]
