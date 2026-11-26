"""Start admin UI sessions for the key form and the SSO callback."""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.responses import RedirectResponse

from tessera_server.config import Settings
from tessera_server.services.audit import append_audit
from tessera_server.services.sessions import create_admin_session

SESSION_COOKIE = "tessera_admin"
_REDIRECT = 303
_HOME = "/app/overview"


def safe_next_path(value: str | None, *, default: str = _HOME) -> str:
    """Return a same-origin relative path for post-login redirects.

    Args:
        value: Candidate `next` query value.
        default: Path used when `value` is missing or unsafe.

    Returns:
        A path that starts with `/app` or `/login`. Legacy `/ui` paths are
        accepted and rewritten to `/app/overview`. Rejects protocol-relative
        URLs (`//…`), absolute URLs, and other prefixes.

    Example:
        ```python
        assert safe_next_path("/app/agents") == "/app/agents"
        assert safe_next_path("//evil.com") == "/app/overview"
        ```
    """
    if value is None:
        return default
    text = value.strip()
    if text in {"/app", "/login"}:
        return text if text != "/login" else default
    if text == "/ui" or text.startswith("/ui/"):
        return default
    if not text.startswith(("/app/", "/login")):
        return default
    if "://" in text or "\\" in text or "\n" in text:
        return default
    return text


def sign_in(
    request: Request,
    *,
    actor: str,
    admin_key_id: str | None,
    method: str,
    audit: dict[str, object] | None = None,
    next_path: str | None = None,
) -> RedirectResponse:
    """Create a session, audit the login, and send the browser onward.

    Args:
        request: Current HTTP request.
        actor: `admin:<key id>` or `oidc:<sub>`.
        admin_key_id: Key the session belongs to, or `None` for SSO.
        method: `key` or `oidc`, recorded in the audit row.
        audit: Extra audit payload fields, such as the SSO issuer.
        next_path: Optional post-login path; must start with `/app`
            (see `safe_next_path`).

    Returns:
        A redirect that sets the opaque session cookie. Defaults to
        `/app/overview` when `next_path` is missing or unsafe.
    """
    settings = cast("Settings", request.app.state.settings)
    with request.app.state.sessions() as session:
        session_id, cookie = create_admin_session(
            session,
            actor=actor,
            admin_key_id=admin_key_id,
            max_age=settings.session_max,
        )
        append_audit(
            session,
            actor=actor,
            entry_type="admin_login",
            payload={**(audit or {}), "method": method, "session": session_id},
        )
    target = safe_next_path(next_path)
    response = RedirectResponse(target, status_code=_REDIRECT)
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        max_age=settings.session_max,
        httponly=True,
        samesite="strict",
        secure=not settings.issuer.startswith("http://"),
        path="/",
    )
    return response


__all__ = ["SESSION_COOKIE", "safe_next_path", "sign_in"]
