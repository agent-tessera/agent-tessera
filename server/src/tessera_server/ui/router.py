"""Short server-rendered auth pages for the console (v0.7).

Operator Jinja pages under `/ui` were removed; login/logout (and redirects
from the old `/ui/*` auth paths) live here. OIDC start/callback are mounted
from `api.oidc`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tessera_server.config import Settings
from tessera_server.db.models import AdminKey
from tessera_server.services.audit import append_audit
from tessera_server.services.oidc import oidc_configured
from tessera_server.services.secrets import parse_secret, secret_matches
from tessera_server.services.sessions import revoke_admin_session
from tessera_server.ui.auth import SESSION_COOKIE, safe_next_path, sign_in
from tessera_server.ui.csrf import CSRF_DEPENDENCY, install_csrf

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_STATIC = Path(__file__).resolve().parent / "static"
_ENV = Jinja2Templates(directory=str(_TEMPLATES))
_REDIRECT = 303
_COOKIE = SESSION_COOKIE


class LoginRequiredError(Exception):
    """Raised when a browser route needs a signed-in admin session."""


def install_ui(app: FastAPI) -> None:
    """Serve login static assets and short auth routes.

    Args:
        app: Application that should expose `/login` (and `/ui` redirects).
    """
    app.mount("/ui/static", StaticFiles(directory=_STATIC), name="ui-static")
    install_csrf(app)
    app.include_router(_router())

    @app.exception_handler(LoginRequiredError)
    async def redirect_to_login(_request: Request, _exc: LoginRequiredError) -> RedirectResponse:
        """Send an anonymous visitor to the login form.

        Args:
            _request: Current HTTP request.
            _exc: Signal that the session is missing or invalid.

        Returns:
            A redirect to `/login`.
        """
        return RedirectResponse("/login", status_code=_REDIRECT)


def _router() -> APIRouter:
    """Build login/logout routes and one-release `/ui` auth redirects.

    Returns:
        Auth routes for the console.
    """
    router = APIRouter()
    mutating = [CSRF_DEPENDENCY]
    router.add_api_route("/login", _login_form, methods=["GET"])
    router.add_api_route("/login", _login, methods=["POST"])
    router.add_api_route("/logout", _logout, methods=["POST"], dependencies=mutating)
    router.add_api_route("/ui/login", _redirect_login_get, methods=["GET"])
    router.add_api_route("/ui/login", _redirect_login_post, methods=["POST"])
    router.add_api_route("/ui/logout", _logout, methods=["POST"], dependencies=mutating)
    router.add_api_route("/ui/logout", _redirect_logout_get, methods=["GET"])
    router.add_api_route("/ui", _redirect_app, methods=["GET"])
    router.add_api_route("/ui/{path:path}", _redirect_app, methods=["GET"])
    return router


def _redirect_login_get(request: Request) -> RedirectResponse:
    """Redirect legacy GET `/ui/login` to `/login`, preserving query string.

    Args:
        request: Current HTTP request.

    Returns:
        A 303 redirect to `/login`.
    """
    return _login_redirect(request)


def _redirect_login_post(request: Request) -> RedirectResponse:
    """Redirect legacy POST `/ui/login` to `/login`, preserving query string.

    Args:
        request: Current HTTP request.

    Returns:
        A 303 redirect to `/login`.
    """
    return _login_redirect(request)


def _login_redirect(request: Request) -> RedirectResponse:
    """Build the legacy login redirect target.

    Args:
        request: Current HTTP request.

    Returns:
        A 303 redirect to `/login`.
    """
    query = request.url.query
    target = f"/login?{query}" if query else "/login"
    return RedirectResponse(target, status_code=_REDIRECT)


def _redirect_logout_get() -> RedirectResponse:
    """Redirect legacy GET `/ui/logout` to `/login`.

    Returns:
        A 303 redirect to `/login`.
    """
    return RedirectResponse("/login", status_code=_REDIRECT)


def _redirect_app(request: Request, path: str = "") -> RedirectResponse:
    """Send classic `/ui` operator URLs to the console overview.

    Args:
        request: Current HTTP request.
        path: Remaining path under `/ui` (unused).

    Returns:
        A redirect to `/app/overview`.
    """
    del request, path
    return RedirectResponse("/app/overview", status_code=_REDIRECT)


def _login_form(request: Request) -> HTMLResponse:
    """Show the admin key form and the SSO link when configured.

    Args:
        request: Current HTTP request.

    Returns:
        The login page.
    """
    return _render("login.html", _login_context(request, None))


def _login(
    request: Request,
    admin_key: Annotated[str, Form()],
    next_field: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    """Start a server-side session for a valid admin key.

    Args:
        request: Current HTTP request.
        admin_key: Key submitted by the form.
        next_field: Optional post-login path from the form.

    Returns:
        A redirect with the session cookie, or the login page on failure.
    """
    settings = cast("Settings", request.app.state.settings)
    next_path = next_field or request.query_params.get("next")
    if settings.oidc_required:
        context = _login_context(request, "admin key login is disabled")
        return _render("login.html", context, status=401)
    row = _lookup_key(request, admin_key)
    if row is None:
        context = _login_context(request, "admin key is required")
        return _render("login.html", context, status=401)
    return sign_in(
        request,
        actor=f"admin:{row.id}",
        admin_key_id=row.id,
        method="key",
        next_path=next_path,
    )


def _login_context(request: Request, error: str | None) -> dict[str, object]:
    """Build the login page flags.

    Args:
        request: Current HTTP request.
        error: Message to show, or ``None``.

    Returns:
        Template context for ``login.html``.
    """
    settings = cast("Settings", request.app.state.settings)
    raw_next = request.query_params.get("next")
    next_path = safe_next_path(raw_next) if raw_next else ""
    return {
        "error": error,
        "sso": oidc_configured(settings),
        "key_login": not settings.oidc_required,
        "next": next_path,
    }


def _logout(request: Request) -> RedirectResponse:
    """End the server-side session and clear the admin cookie.

    Args:
        request: Current HTTP request.

    Returns:
        A redirect to the login page.
    """
    raw = request.cookies.get(_COOKIE, "")
    if raw:
        with request.app.state.sessions() as session:
            actor = revoke_admin_session(session, raw)
            if actor is not None:
                append_audit(session, actor=actor, entry_type="admin_logout", payload={})
    response = RedirectResponse("/login", status_code=_REDIRECT)
    response.delete_cookie(
        _COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=_secure(request),
    )
    return response


def _lookup_key(request: Request, admin_key: str) -> AdminKey | None:
    """Return the admin key row for a plaintext token.

    Args:
        request: Current HTTP request.
        admin_key: Submitted key.

    Returns:
        The matching active key, or ``None``.
    """
    parsed = parse_secret(admin_key, prefix="tsa")
    if parsed is None:
        return None
    key_id, secret = parsed
    with request.app.state.sessions() as session:
        row = cast("AdminKey | None", session.get(AdminKey, key_id))
        if row is None or row.revoked or not secret_matches(row.secret_sha256, secret):
            return None
        row.last_used_at = datetime.now(UTC)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row


def _secure(request: Request) -> bool:
    """Whether cookies should set the Secure flag.

    Args:
        request: Current HTTP request.

    Returns:
        ``True`` when the issuer is not plain HTTP.
    """
    settings = cast("Settings", request.app.state.settings)
    return not settings.issuer.startswith("http://")


def _render(name: str, context: dict[str, object], *, status: int = 200) -> HTMLResponse:
    """Render a Jinja template.

    Args:
        name: Template file name.
        context: Template variables.
        status: HTTP status code.

    Returns:
        An HTML response.
    """
    html = _ENV.get_template(name).render(**context)
    return HTMLResponse(html, status_code=status)


__all__ = ["LoginRequiredError", "install_ui"]
