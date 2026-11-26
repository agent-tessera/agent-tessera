"""Auth-only admin surface after classic ``/ui`` removal."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from ui_helpers import ui_form

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, str]]:
    """Migrated app with a fresh admin key."""
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
    )
    try:
        with TestClient(create_app(settings)) as test_client:
            yield test_client, admin
    finally:
        downgrade_database(database_url)


def test_static_assets_still_served(client: tuple[TestClient, str]) -> None:
    test_client, _admin = client
    css = test_client.get("/ui/static/style.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    fonts = test_client.get("/ui/static/fonts/Manrope-Variable.woff2")
    assert fonts.status_code == 200


def test_classic_operator_pages_redirect_to_console(client: tuple[TestClient, str]) -> None:
    test_client, _admin = client
    for path in (
        "/ui",
        "/ui/overview",
        "/ui/agents",
        "/ui/agents/new",
        "/ui/baselines",
        "/ui/runs",
        "/ui/alerts",
        "/ui/custody",
        "/ui/live",
        "/ui/approvals",
        "/ui/audit",
        "/ui/search",
        "/ui/settings",
    ):
        response = test_client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/app/overview"


def test_login_logout_and_legacy_redirects(client: tuple[TestClient, str]) -> None:
    test_client, admin = client
    legacy = test_client.get("/ui/login", follow_redirects=False)
    assert legacy.status_code == 303
    assert legacy.headers["location"].endswith("/login")

    login = test_client.get("/login")
    assert login.status_code == 200
    assert "Admin sign-in" in login.text
    assert 'href="/ui/static/style.css"' in login.text

    denied = test_client.post("/login", data={"admin_key": "nope"})
    assert denied.status_code == 401

    signed = test_client.post("/login", data={"admin_key": admin}, follow_redirects=False)
    assert signed.status_code == 303
    assert signed.headers["location"] == "/app/overview"
    assert test_client.get("/v1/me").status_code == 200

    logout = test_client.post("/logout", data=ui_form(test_client), follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"].endswith("/login")
    assert test_client.get("/v1/me").status_code == 401

    legacy_logout = test_client.get("/ui/logout", follow_redirects=False)
    assert legacy_logout.status_code == 303
    assert legacy_logout.headers["location"].endswith("/login")


def test_console_spa_still_served(client: tuple[TestClient, str]) -> None:
    test_client, _admin = client
    page = test_client.get("/app/")
    assert page.status_code == 200
    assert "Tessera" in page.text
