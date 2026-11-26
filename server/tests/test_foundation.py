"""Settings, schema, identifiers, and the health check."""

from __future__ import annotations

import base64
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from agent_tessera.errors import ConfigError
from tessera_server import __version__
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, table_names, upgrade_database
from tessera_server.db.models import Owner
from tessera_server.db.session import advisory_xact_lock, make_engine, session_factory
from tessera_server.ids import new_ulid


def test_ulids_are_unique_crockford_ids() -> None:
    ids = {new_ulid() for _ in range(10_000)}
    assert len(ids) == 10_000
    alphabet = set("0123456789ABCDEFGHJKMNPQRSTUVWXYZ")
    assert all(len(value) == 26 and set(value) <= alphabet for value in ids)


def test_dev_settings_create_and_reuse_a_private_kek(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TESSERA_SERVER_DATABASE_URL", raising=False)
    monkeypatch.delenv("TESSERA_SERVER_ISSUER", raising=False)
    monkeypatch.delenv("TESSERA_SERVER_KEK", raising=False)
    monkeypatch.delenv("TESSERA_SERVER_ORG", raising=False)

    first = Settings.from_env(dev=True)
    path = tmp_path / ".tessera" / "server.kek"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert first.dev is True
    assert first.database_url == "sqlite:///.tessera/server.db"
    assert first.issuer == "http://127.0.0.1:8700"
    assert first.org == "default"
    assert len(first.kek) == 32
    assert mode == 0o600
    assert Settings.from_env(dev=True).kek == first.kek


def test_settings_from_env_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "TESSERA_SERVER_DATABASE_URL",
        "TESSERA_SERVER_ISSUER",
        "TESSERA_SERVER_KEK",
        "TESSERA_SERVER_ORG",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match="TESSERA_SERVER_DATABASE_URL"):
        Settings.from_env()
    monkeypatch.setenv("TESSERA_SERVER_DATABASE_URL", "sqlite://")
    with pytest.raises(ConfigError, match="TESSERA_SERVER_ISSUER"):
        Settings.from_env()
    monkeypatch.setenv("TESSERA_SERVER_ISSUER", "http://example.test/")
    with pytest.raises(ConfigError, match="TESSERA_SERVER_KEK"):
        Settings.from_env()
    monkeypatch.setenv("TESSERA_SERVER_KEK", "not-base64")
    with pytest.raises(ConfigError, match="TESSERA_SERVER_KEK"):
        Settings.from_env()
    monkeypatch.setenv("TESSERA_SERVER_KEK", base64.b64encode(b"short").decode())
    with pytest.raises(ConfigError, match="TESSERA_SERVER_KEK"):
        Settings.from_env()

    monkeypatch.setenv("TESSERA_SERVER_KEK", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("TESSERA_SERVER_ORG", "acme")
    settings = Settings.from_env()
    assert settings.database_url == "sqlite://"
    assert settings.issuer == "http://example.test"
    assert settings.org == "acme"
    assert settings.kek == b"k" * 32
    assert settings.dev is False
    assert settings.token_ttl == 900
    assert settings.bundle_ttl == 86_400
    assert settings.lease_ttl == 300


def test_dev_kek_file_must_be_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TESSERA_SERVER_KEK", raising=False)
    path = tmp_path / ".tessera" / "server.kek"
    path.parent.mkdir()
    path.write_text("nope", encoding="utf-8")
    with pytest.raises(ConfigError, match="TESSERA_SERVER_KEK"):
        Settings.from_env(dev=True)


def test_migration_upgrade_and_downgrade(database_url: str) -> None:
    assert table_names(database_url) == set()
    upgrade_database(database_url)
    try:
        names = table_names(database_url)
        assert {
            "owners",
            "agents",
            "policies",
            "audit_log",
            "token_leases",
            "quota_counters",
            "quota_leases",
            "approvals",
            "webhook_endpoints",
            "run_effects",
            "alert_rules",
            "alert_firings",
            "provider_secrets",
        } <= names
        upgrade_database(database_url)
        engine = make_engine(database_url)
        factory = session_factory(engine)
        try:
            with factory() as session:
                owner = Owner(
                    id=new_ulid(),
                    owner_type="team",
                    email="owners@example.test",
                    display_name="Owners",
                    created_at=datetime.now(UTC),
                )
                session.add(owner)
                session.commit()
                advisory_xact_lock(session)
                stored = session.get(Owner, owner.id)
                assert stored is not None
                assert stored.email == "owners@example.test"
                session.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    finally:
        downgrade_database(database_url)
    assert "owners" not in table_names(database_url)


def test_advisory_lock_runs_on_postgres() -> None:
    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def __init__(self) -> None:
            self.statement = ""
            self.params: dict[str, object] = {}

        def get_bind(self) -> _Bind:
            return _Bind()

        def execute(self, statement: object, params: dict[str, object]) -> None:
            self.statement = str(statement)
            self.params = params

    session = _Session()
    advisory_xact_lock(cast(Session, session), 7401)
    assert "pg_advisory_xact_lock" in session.statement
    assert session.params == {"key": 7401}


def test_healthz_reports_version_and_dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TESSERA_SERVER_KEK", raising=False)
    settings = Settings.from_env(dev=True)
    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "dev": True}
