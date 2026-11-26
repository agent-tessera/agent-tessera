"""FastAPI application factory."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from tessera_server import __version__
from tessera_server.api.alerts import alerts_router
from tessera_server.api.approvals import approvals_router
from tessera_server.api.console_parity import console_parity_router
from tessera_server.api.custody import custody_router
from tessera_server.api.dashboards import dashboards_router
from tessera_server.api.effects import effects_router
from tessera_server.api.errors import install_error_handlers
from tessera_server.api.events import events_router
from tessera_server.api.footprint import footprint_router
from tessera_server.api.jwks import jwks_router
from tessera_server.api.me import me_router
from tessera_server.api.oidc import oidc_router
from tessera_server.api.policies import policies_router
from tessera_server.api.posture import posture_router
from tessera_server.api.quotas import quotas_router
from tessera_server.api.registry import registry_router
from tessera_server.api.runs import runs_router
from tessera_server.api.search import search_router
from tessera_server.api.supervisors import supervisors_router
from tessera_server.api.token import token_router
from tessera_server.api.webhooks import webhooks_router
from tessera_server.config import Settings
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.alerts import evaluate_alerts
from tessera_server.services.approvals import advance_approvals
from tessera_server.services.supervision import advance_dead_men
from tessera_server.services.webhooks import ensure_catch_all
from tessera_server.ui import install_ui
from tessera_server.web import install_console

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Escalate expired approvals, evaluate alerts, then close the engine.

    The loop runs only when `app.state.approval_poll` is a positive number of
    seconds. Tests leave it unset. Each pass also evaluates alert rules.

    Args:
        app: Application whose engine should be disposed.

    Yields:
        Control back to the server until shutdown.
    """
    settings = _settings(app)
    if settings.webhook_url:
        with app.state.sessions() as session:
            ensure_catch_all(session, settings)
    stop = threading.Event()
    poll = getattr(app.state, "approval_poll", None)
    worker: threading.Thread | None = None
    if isinstance(poll, (int, float)) and not isinstance(poll, bool) and poll > 0:
        worker = threading.Thread(
            target=_escalate,
            args=(app, stop, float(poll)),
            name="tessera-approvals",
            daemon=True,
        )
        worker.start()
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=2)
        app.state.engine.dispose()


def _escalate(app: FastAPI, stop: threading.Event, poll: float) -> None:
    """Advance expired approvals and evaluate alerts until shutdown.

    Args:
        app: Application whose session factory to use.
        stop: Set when the process is stopping.
        poll: Seconds between passes. The first pass waits this long.
    """
    while not stop.wait(poll):
        try:
            with app.state.sessions() as session:
                advance_approvals(session, settings=_settings(app))
        except Exception:
            log.exception("approval escalation failed")
        try:
            with app.state.sessions() as session:
                evaluate_alerts(session, _settings(app))
        except Exception:
            log.exception("alert evaluation failed")
        try:
            with app.state.sessions() as session:
                advance_dead_men(session, _settings(app))
        except Exception:
            log.exception("dead-man check failed")


def _settings(app: FastAPI) -> Settings:
    """Read the settings stored on the application.

    Args:
        app: Current application.

    Returns:
        The process settings.
    """
    return cast(Settings, app.state.settings)


def create_app(settings: Settings) -> FastAPI:
    """Build the control-plane application.

    Args:
        settings: Process settings stored on `app.state.settings`.

    Returns:
        An application that serves health, the JSON API, and the admin UI.

    Example:
        ```python
        from tessera_server.app import create_app
        from tessera_server.config import Settings

        app = create_app(Settings.from_env(dev=True))
        assert app.state.settings.dev is True
        ```
    """
    engine = make_engine(settings.database_url)
    app = FastAPI(title="Agent Tessera", version=__version__, lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = session_factory(engine)
    app.state.revocation_poll = 1.0
    app.state.revocation_keepalive = 15.0
    app.include_router(jwks_router())
    app.include_router(oidc_router())
    app.include_router(me_router())
    app.include_router(registry_router())
    app.include_router(policies_router())
    app.include_router(events_router())
    app.include_router(footprint_router())
    app.include_router(runs_router())
    app.include_router(search_router())
    app.include_router(console_parity_router())
    app.include_router(effects_router())
    app.include_router(token_router())
    app.include_router(quotas_router())
    app.include_router(approvals_router())
    app.include_router(webhooks_router())
    app.include_router(alerts_router())
    app.include_router(supervisors_router())
    app.include_router(posture_router())
    app.include_router(dashboards_router())
    app.include_router(custody_router())
    install_ui(app)
    install_console(app)
    install_error_handlers(app)

    @app.get("/")
    def root() -> RedirectResponse:
        """Send browsers to the React console.

        Returns:
            A redirect to `/app` (the SPA lands on `/app/overview`).
        """
        return RedirectResponse("/app", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        """Report that the process is up.

        Returns:
            Status, package version, and whether development mode is on.
        """
        return {"status": "ok", "version": __version__, "dev": settings.dev}

    return app


__all__ = ["create_app"]
