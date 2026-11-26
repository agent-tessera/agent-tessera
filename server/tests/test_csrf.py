"""CSRF protection on remaining auth POST routes."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from ui_helpers import read_csrf, ui_form

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def csrf_app(database_url: str) -> Iterator[tuple[TestClient, str]]:
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
        with TestClient(create_app(settings)) as client:
            client.post("/login", data={"admin_key": admin})
            yield client, admin
    finally:
        downgrade_database(database_url)


def test_logout_requires_csrf(csrf_app: tuple[TestClient, str]) -> None:
    client, _admin = csrf_app
    denied = client.post("/logout", data={}, follow_redirects=False)
    assert denied.status_code == 403
    assert "csrf token is required" in denied.text
    ok = client.post("/logout", data=ui_form(client), follow_redirects=False)
    assert ok.status_code == 303
    assert ok.headers["location"].endswith("/login")
    assert client.get("/v1/me").status_code == 401


def test_csrf_header_is_accepted(csrf_app: tuple[TestClient, str]) -> None:
    client, _admin = csrf_app
    token = read_csrf(client)
    response = client.post(
        "/logout",
        data={},
        headers={"X-CSRF-Token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_login_stays_exempt(csrf_app: tuple[TestClient, str]) -> None:
    client, _admin = csrf_app
    client.post("/logout", data=ui_form(client))
    denied = client.post("/login", data={"admin_key": "nope"})
    assert denied.status_code == 401
    assert "csrf" not in denied.text.lower()
