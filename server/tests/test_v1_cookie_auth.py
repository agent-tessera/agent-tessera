"""Cookie auth and CSRF on `/v1`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera_server.api.deps import require_admin, safe_next_path
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AdminSession
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key
from tessera_server.services.sessions import revoke_admin_session
from tessera_server.ui.auth import safe_next_path as auth_safe_next


@pytest.fixture
def cookie_app(database_url: str) -> Iterator[tuple[TestClient, str]]:
    """App with a POST probe protected by `require_admin`."""
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
    app = create_app(settings)

    @app.get("/probe")
    def get_probe(_admin: object = Depends(require_admin)) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/probe")
    def post_probe(_admin: object = Depends(require_admin)) -> dict[str, bool]:
        return {"ok": True}

    try:
        with TestClient(app) as client:
            yield client, admin
    finally:
        downgrade_database(database_url)


def _sign_in(client: TestClient, admin: str) -> str:
    """Log in through `/login` and return the session CSRF from `/v1/me`."""
    response = client.post("/login", data={"admin_key": admin}, follow_redirects=False)
    assert response.status_code == 303
    me = client.get("/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert "csrf" in body
    return str(body["csrf"])


def test_cookie_get_works(cookie_app: tuple[TestClient, str]) -> None:
    client, admin = cookie_app
    _sign_in(client, admin)
    assert client.get("/probe").status_code == 200
    me = client.get("/v1/me").json()
    assert me["method"] == "key"
    assert me["csrf"]


def test_cookie_post_requires_csrf(cookie_app: tuple[TestClient, str]) -> None:
    client, admin = cookie_app
    csrf = _sign_in(client, admin)
    missing = client.post("/probe")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"
    wrong = client.post("/probe", headers={"X-CSRF-Token": "not-the-token"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "csrf_failed"
    ok = client.post("/probe", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200


def test_bearer_post_skips_csrf(cookie_app: tuple[TestClient, str]) -> None:
    client, admin = cookie_app
    ok = client.post("/probe", headers={"Authorization": f"Bearer {admin}"})
    assert ok.status_code == 200
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {admin}"})
    assert me.status_code == 200
    assert "csrf" not in me.json()


def test_revoked_session_is_401(cookie_app: tuple[TestClient, str], database_url: str) -> None:
    client, admin = cookie_app
    _sign_in(client, admin)
    cookie = str(client.cookies.get("tessera_admin"))
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            assert revoke_admin_session(session, cookie) is not None
    finally:
        engine.dispose()
    denied = client.get("/probe")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_token"


def test_expired_session_is_401(cookie_app: tuple[TestClient, str], database_url: str) -> None:
    client, admin = cookie_app
    _sign_in(client, admin)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            row = session.scalars(select(AdminSession)).one()
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
    finally:
        engine.dispose()
    denied = client.get("/v1/me")
    assert denied.status_code == 401


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/app/agents", "/app/agents"),
        ("/ui/overview", "/app/overview"),
        ("/app", "/app"),
        ("//evil.com", "/app/overview"),
        ("https://evil.com", "/app/overview"),
        ("http://evil.com/app/", "/app/overview"),
        ("/evil", "/app/overview"),
        (None, "/app/overview"),
    ],
)
def test_safe_next_path(value: str | None, expected: str) -> None:
    assert safe_next_path(value) == expected
    assert auth_safe_next(value) == expected


def test_login_honors_safe_next(cookie_app: tuple[TestClient, str]) -> None:
    client, admin = cookie_app
    safe = client.post(
        "/login",
        data={"admin_key": admin, "next": "/app/agents"},
        follow_redirects=False,
    )
    assert safe.status_code == 303
    assert safe.headers["location"] == "/app/agents"
    client.cookies.clear()
    unsafe = client.post(
        "/login",
        data={"admin_key": admin, "next": "//evil.com"},
        follow_redirects=False,
    )
    assert unsafe.status_code == 303
    assert unsafe.headers["location"] == "/app/overview"
