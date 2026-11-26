"""Serve the React console under `/app`."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_DIST = Path(__file__).resolve().parent / "dist"
_ASSETS = _DIST / "assets"
_INDEX = _DIST / "index.html"
_LOG = logging.getLogger(__name__)
_STATE = {"warned": False}

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


class ConsoleHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers and asset cache policy under `/app`."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Call the next handler and add console headers.

        Args:
            request: Current HTTP request.
            call_next: Downstream ASGI app.

        Returns:
            The downstream response with console headers when applicable.
        """
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/app/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if path.startswith("/app"):
            for name, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
        return response


def install_console(app: FastAPI) -> None:
    """Mount the built console SPA under `/app`.

    Args:
        app: Application that should serve the console.
    """
    app.add_middleware(ConsoleHeadersMiddleware)
    if _ASSETS.is_dir():
        app.mount(
            "/app/assets",
            StaticFiles(directory=_ASSETS),
            name="console-assets",
        )

    @app.get("/app")
    @app.get("/app/")
    @app.get("/app/{full_path:path}")
    def spa(full_path: str = "") -> Response:
        """Serve `index.html` for console routes, or a not-built page.

        Args:
            full_path: Remaining path under `/app` (unused; SPA handles it).

        Returns:
            The SPA document or a short HTML notice when the build is missing.
        """
        del full_path
        if not _INDEX.is_file():
            if not _STATE["warned"]:
                _LOG.warning("console not built; run `npm --prefix web run build`")
                _STATE["warned"] = True
            return HTMLResponse(
                "<!doctype html><html><body><p>Console not built. "
                "Run <code>npm --prefix web run build</code>.</p></body></html>",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        body = _INDEX.read_text(encoding="utf-8")
        return HTMLResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                **_SECURITY_HEADERS,
            },
        )


def console_dist() -> Path:
    """Return the console build directory.

    Returns:
        Path to `tessera_server/web/dist`.
    """
    return _DIST


__all__ = ["console_dist", "install_console"]
