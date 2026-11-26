"""Server-side admin sessions for short auth routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from ui_helpers import ui_form

from agent_tessera.errors import ConfigError
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AuditEntry
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key, rotate_admin_key
from tessera_server.services.sessions import (
    create_admin_session,
    resolve_admin_session,
    revoke_admin_session,
)

_START = datetime(2026, 11, 9, 12, tzinfo=UTC)


def _settings(url: str, **extra: object) -> Settings:
    values: dict[str, object] = {
        "database_url": url,
        "issuer": "http://127.0.0.1:8700",
        "kek": b"k" * 32,
        "dev": False,
    }
    values.update(extra)
    return Settings.model_validate(values)


def _sso(url: str, **extra: object) -> Settings:
    return _settings(
        url,
        oidc_issuer="https://idp.example",
        oidc_client_id="tessera-admin",
        oidc_client_secret="secret",  # noqa: S106
        **extra,
    )


@pytest.fixture
def migrated(database_url: str) -> Iterator[tuple[str, str]]:
    upgrade_database(database_url)
    engine = make_engine(url := database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    try:
        yield url, admin
    finally:
        downgrade_database(url)


def _audit_rows(url: str, entry_type: str) -> list[AuditEntry]:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            return list(
                session.scalars(select(AuditEntry).where(AuditEntry.entry_type == entry_type))
            )
    finally:
        engine.dispose()


def _session_cookie(url: str, actor: str, key_id: str | None) -> str:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            _session_id, cookie = create_admin_session(
                session, actor=actor, admin_key_id=key_id, max_age=3600
            )
    finally:
        engine.dispose()
    return cookie


def test_forged_sso_cookie_is_rejected(migrated: tuple[str, str]) -> None:
    url, _admin = migrated
    for required in (False, True):
        with TestClient(create_app(_sso(url, oidc_required=required))) as client:
            client.cookies.set("tessera_admin", "oidc:attacker")
            page = client.get("/v1/me", follow_redirects=False)
            assert page.status_code == 401
            created = client.post(
                "/v1/agents",
                json={"name": "pwned", "environment": "prod", "owner_id": "x"},
                follow_redirects=False,
            )
            assert created.status_code == 401


def test_raw_admin_key_cookie_is_rejected(migrated: tuple[str, str]) -> None:
    url, admin = migrated
    with TestClient(create_app(_settings(url))) as client:
        client.cookies.set("tessera_admin", admin)
        assert client.get("/v1/me", follow_redirects=False).status_code == 401


def test_key_login_stores_only_an_opaque_session(migrated: tuple[str, str]) -> None:
    url, admin = migrated
    with TestClient(create_app(_settings(url))) as client:
        signed = client.post("/login", data={"admin_key": admin}, follow_redirects=False)
        assert signed.headers["location"] == "/app/overview"
        cookie = str(client.cookies.get("tessera_admin"))
        assert cookie.startswith("tss_")
        assert admin.split("_")[2] not in cookie
        assert "max-age=86400" in signed.headers["set-cookie"].lower()
        assert client.get("/v1/me", follow_redirects=False).status_code == 200
    rows = _audit_rows(url, "admin_login")
    assert len(rows) == 1
    assert rows[0].actor == f"admin:{admin.split('_')[1]}"
    assert rows[0].payload == {"method": "key", "session": cookie.split("_")[1]}


def test_logout_ends_the_session_on_the_server(migrated: tuple[str, str]) -> None:
    url, admin = migrated
    with TestClient(create_app(_settings(url))) as client:
        client.post("/login", data={"admin_key": admin})
        cookie = str(client.cookies.get("tessera_admin"))
        client.post("/logout", data=ui_form(client))
        client.cookies.set("tessera_admin", cookie)
        assert client.get("/v1/me", follow_redirects=False).status_code == 401
    rows = _audit_rows(url, "admin_logout")
    assert [row.actor for row in rows] == [f"admin:{admin.split('_')[1]}"]


def test_rotating_the_key_ends_its_sessions(migrated: tuple[str, str]) -> None:
    url, admin = migrated
    with TestClient(create_app(_settings(url))) as client:
        client.post("/login", data={"admin_key": admin})
        assert client.get("/v1/me", follow_redirects=False).status_code == 200
        engine = make_engine(url)
        try:
            with session_factory(engine)() as session:
                rotate_admin_key(session, "ops")
        finally:
            engine.dispose()
        assert client.get("/v1/me", follow_redirects=False).status_code == 401


def test_sso_session_ends_when_sso_is_turned_off(migrated: tuple[str, str]) -> None:
    url, _admin = migrated
    cookie = _session_cookie(url, "oidc:ada", None)
    with TestClient(create_app(_sso(url))) as client:
        client.cookies.set("tessera_admin", cookie)
        assert client.get("/v1/me", follow_redirects=False).status_code == 200
    with TestClient(create_app(_settings(url))) as client:
        client.cookies.set("tessera_admin", cookie)
        assert client.get("/v1/me", follow_redirects=False).status_code == 401


def test_idle_and_absolute_expiry(migrated: tuple[str, str]) -> None:
    url, _admin = migrated
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            _id, cookie = create_admin_session(
                session, actor="oidc:ada", admin_key_id=None, max_age=7200, now=lambda: _START
            )

            def at(minutes: int) -> datetime:
                return _START + timedelta(minutes=minutes)

            assert resolve_admin_session(session, cookie, idle=1800, now=lambda: at(29))
            assert resolve_admin_session(session, cookie, idle=1800, now=lambda: at(58))
            assert resolve_admin_session(session, cookie, idle=1800, now=lambda: at(89)) is None
            assert resolve_admin_session(session, cookie, idle=1800, now=lambda: at(87))
            assert resolve_admin_session(session, cookie, idle=9999, now=lambda: at(120)) is None
    finally:
        engine.dispose()


def test_wrong_secret_and_revoked_sessions_do_not_resolve(migrated: tuple[str, str]) -> None:
    url, _admin = migrated
    cookie = _session_cookie(url, "oidc:ada", None)
    prefix, session_id, _secret = cookie.split("_", 2)
    forged = f"{prefix}_{session_id}_not-the-secret"
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            assert resolve_admin_session(session, forged, idle=3600) is None
            assert revoke_admin_session(session, forged) is None
            assert revoke_admin_session(session, cookie) == "oidc:ada"
            assert resolve_admin_session(session, cookie, idle=3600) is None
            assert revoke_admin_session(session, cookie) is None
    finally:
        engine.dispose()


def test_session_lifetimes_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_SERVER_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("TESSERA_SERVER_ISSUER", "https://tessera.example")
    monkeypatch.setenv("TESSERA_SERVER_KEK", "a" * 43 + "=")
    defaults = Settings.from_env()
    assert (defaults.session_idle, defaults.session_max) == (28_800, 86_400)
    monkeypatch.setenv("TESSERA_SERVER_SESSION_IDLE", "30m")
    monkeypatch.setenv("TESSERA_SERVER_SESSION_MAX", "12h")
    tuned = Settings.from_env()
    assert (tuned.session_idle, tuned.session_max) == (1800, 43_200)
    monkeypatch.setenv("TESSERA_SERVER_SESSION_IDLE", "soon")
    with pytest.raises(ConfigError, match="TESSERA_SERVER_SESSION_IDLE"):
        Settings.from_env()
