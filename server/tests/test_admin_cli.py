"""Admin keys, authentication, and the server command."""

from __future__ import annotations

import base64
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_tessera.cli import main as sdk_main
from tessera_server.api.deps import require_admin
from tessera_server.app import create_app
from tessera_server.cli import main
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AdminKey, AuditEntry
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key, parse_secret

_KEK = base64.b64encode(b"k" * 32).decode()


@pytest.fixture
def settings(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Settings]:
    upgrade_database(database_url)
    monkeypatch.setenv("TESSERA_SERVER_DATABASE_URL", database_url)
    monkeypatch.setenv("TESSERA_SERVER_ISSUER", "http://127.0.0.1:8700")
    monkeypatch.setenv("TESSERA_SERVER_KEK", _KEK)
    monkeypatch.chdir(tmp_path)
    try:
        yield Settings.from_env()
    finally:
        downgrade_database(database_url)


def _probe(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/probe")
    def probe(_admin: object = Depends(require_admin)) -> dict[str, bool]:
        return {"ok": True}

    return app


def test_admin_key_format_and_auth(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            token = create_admin_key(session, "ops")
        parsed = parse_secret(token, prefix="tsa")
        assert parsed is not None
        key_id, secret = parsed
        assert token == f"tsa_{key_id}_{secret}"
        assert parse_secret("nope", prefix="tsa") is None
        assert parse_secret("tsa_only", prefix="tsa") is None
        with session_factory(engine)() as session:
            row = session.get(AdminKey, key_id)
            assert row is not None
            assert secret not in row.secret_sha256
        app = _probe(settings)
        with TestClient(app) as client:
            denied = client.get("/probe")
            assert denied.status_code == 401
            assert denied.json()["error"]["code"] == "invalid_token"
            ok = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
            assert ok.status_code == 200
            unknown = client.get("/probe", headers={"Authorization": "Bearer tsa_missing_secret"})
            assert unknown.status_code == 401
            malformed = client.get("/probe", headers={"Authorization": "Bearer nope"})
            assert malformed.status_code == 401
        with session_factory(engine)() as session:
            row = session.get(AdminKey, key_id)
            assert row is not None
            assert row.last_used_at is not None
            row.revoked = True
            session.commit()
        with TestClient(app) as client:
            revoked = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
            assert revoked.status_code == 401
    finally:
        engine.dispose()


def test_dev_bypass_is_only_loopback(settings: Settings) -> None:
    opened = settings.model_copy(update={"dev": True})
    app = _probe(opened)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/probe").status_code == 200
    with TestClient(app, client=("10.0.0.8", 50000)) as client:
        assert client.get("/probe").status_code == 401
    closed = _probe(settings)
    with TestClient(closed, client=("127.0.0.1", 50000)) as client:
        assert client.get("/probe").status_code == 401


def test_serve_refuses_an_unmigrated_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setenv("TESSERA_SERVER_DATABASE_URL", url)
    monkeypatch.setenv("TESSERA_SERVER_ISSUER", "http://127.0.0.1:8700")
    monkeypatch.setenv("TESSERA_SERVER_KEK", _KEK)
    calls: list[str] = []

    def _run(app: FastAPI, **_kwargs: object) -> None:
        calls.append(str(app.state.settings.database_url))
        app.state.engine.dispose()

    monkeypatch.setattr("tessera_server.cli.uvicorn.run", _run)
    assert main(["serve", "--host", "127.0.0.1", "--port", "8701"]) == 1
    assert "not migrated" in capsys.readouterr().err
    assert calls == []
    upgrade_database(url)
    assert main(["serve", "--host", "127.0.0.1", "--port", "8701"]) == 0
    assert calls


def test_cli_creates_an_admin_key_and_rotates(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert settings.database_url
    assert main(["migrate"]) == 0
    assert "database migrated" in capsys.readouterr().out
    assert main(["create-admin-key", "--name", "ops"]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("tsa_")
    assert main(["rotate-keys"]) == 0
    kid = capsys.readouterr().out.strip()
    assert kid
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            actors = session.scalars(select(AuditEntry.actor)).all()
            assert actors == ["cli", "cli"]
            payloads = session.scalars(select(AuditEntry.payload)).all()
            assert token not in str(payloads)
            assert kid
    finally:
        engine.dispose()


def test_cli_rotates_the_break_glass_admin_key(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin-key", "--name", "ops"]) == 0
    old = capsys.readouterr().out.strip()
    assert old.startswith("tsa_")
    app = _probe(settings)
    with TestClient(app) as client:
        assert client.get("/probe", headers={"Authorization": f"Bearer {old}"}).status_code == 200
    assert main(["rotate-admin-key", "--name", "ops"]) == 0
    new = capsys.readouterr().out.strip()
    assert new.startswith("tsa_")
    assert new != old
    with TestClient(app) as client:
        assert client.get("/probe", headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert client.get("/probe", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            old_parsed = parse_secret(old, prefix="tsa")
            new_parsed = parse_secret(new, prefix="tsa")
            assert old_parsed is not None
            assert new_parsed is not None
            previous = session.get(AdminKey, old_parsed[0])
            current = session.get(AdminKey, new_parsed[0])
            assert previous is not None
            assert previous.revoked is True
            assert current is not None
            assert current.revoked is False
            rotated = session.scalars(
                select(AuditEntry).where(AuditEntry.entry_type == "admin_key_rotated")
            ).all()
            assert len(rotated) == 1
            payload = rotated[0].payload
            assert payload == {
                "id": new_parsed[0],
                "name": "ops",
                "previous_ids": [old_parsed[0]],
            }
            assert old not in str(payload)
            assert new not in str(payload)
            assert old_parsed[1] not in str(payload)
            assert new_parsed[1] not in str(payload)
    finally:
        engine.dispose()


def test_sdk_server_command_reports_a_missing_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "tessera_server.cli", None)
    assert sdk_main(["server", "migrate"]) == 1
    assert "agent-tessera-server" in capsys.readouterr().err
