"""Event index columns, migration backfill, and ingest extraction."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import (
    _config,
    database_revision,
    downgrade_database,
    upgrade_database,
)
from tessera_server.db.models import Event
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.event_index import index_fields
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
"""


def test_index_fields_reads_action_and_top_level() -> None:
    fields = index_fields(
        {
            "run": "run-1",
            "action": {"kind": "tool", "resource": "files.read"},
            "effect": "deny",
            "rule": "agent:tools.deny[0]",
            "span_id": "0123456789abcdef",
            "parent_span_id": "fedcba9876543210",
            "status": "ok",
        }
    )
    assert fields == {
        "run_id": "run-1",
        "span_id": "0123456789abcdef",
        "parent_span_id": "fedcba9876543210",
        "kind": "tool",
        "resource": "files.read",
        "effect": "deny",
        "status": "ok",
        "rule": "agent:tools.deny[0]",
    }


def test_index_fields_falls_back_and_truncates() -> None:
    fields = index_fields(
        {
            "task": "task-only",
            "kind": "model",
            "resource": "x" * 400,
            "span_id": "y" * 20,
        }
    )
    assert fields["run_id"] == "task-only"
    assert fields["kind"] == "model"
    assert fields["resource"] == "x" * 300
    assert fields["span_id"] == "y" * 16
    assert fields["effect"] is None


@pytest.fixture(params=["sqlite", "postgres"])
def legacy_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """Yield a URL for migration tests; skip Postgres when unset."""
    if request.param == "postgres":
        url = os.environ.get("TESSERA_TEST_DATABASE_URL")
        if not url:
            pytest.skip("TESSERA_TEST_DATABASE_URL is not set")
        yield url
        return
    yield f"sqlite:///{tmp_path / 'legacy.db'}"


def test_upgrade_from_0011_backfills_and_downgrade_restores(legacy_url: str) -> None:
    config = _config(legacy_url)
    command.upgrade(config, "0011")
    assert database_revision(legacy_url) == "0011"
    engine = make_engine(legacy_url)
    try:
        with engine.begin() as connection:
            # 0001 create_all uses current models; strip 0012 columns to
            # simulate a 0.5 database that stopped at revision 0011.
            _strip_event_index(connection)
            names = {column["name"] for column in inspect(connection).get_columns("events")}
            assert "run_id" not in names
            connection.execute(
                text(
                    "INSERT INTO events "
                    "(id, agent_id, instance_id, type, ts, task, payload, received_at) "
                    "VALUES "
                    "(:id, :agent_id, :instance_id, :type, :ts, :task, :payload, :received_at)"
                ),
                {
                    "id": "ab" * 16,
                    "agent_id": "01AGENT000000000000000000",
                    "instance_id": "cd" * 16,
                    "type": "call",
                    "ts": "2026-11-09T12:00:00+00:00",
                    "task": "run-old",
                    "payload": json.dumps(
                        {
                            "id": "ab" * 16,
                            "type": "call",
                            "run": "run-old",
                            "kind": "tool",
                            "resource": "files.read",
                            "status": "ok",
                            "span_id": "0123456789abcdef",
                        }
                    ),
                    "received_at": "2026-11-09T12:00:01+00:00",
                },
            )
    finally:
        engine.dispose()

        command.upgrade(config, "head")
        assert database_revision(legacy_url) == "0013"
    engine = make_engine(legacy_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT run_id, span_id, kind, resource, status FROM events WHERE id = :id"),
                {"id": "ab" * 16},
            ).one()
            assert row.run_id == "run-old"
            assert row.span_id == "0123456789abcdef"
            assert row.kind == "tool"
            assert row.resource == "files.read"
            assert row.status == "ok"
    finally:
        engine.dispose()

    command.downgrade(config, "0011")
    assert database_revision(legacy_url) == "0011"
    engine = make_engine(legacy_url)
    try:
        with engine.connect() as connection:
            names = {column["name"] for column in inspect(connection).get_columns("events")}
            assert "run_id" not in names
            assert "span_id" not in names
    finally:
        engine.dispose()
    downgrade_database(legacy_url)


def test_ingest_fills_index_columns(database_url: str) -> None:
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
    headers = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(app) as client:
            token = _runtime_token(client, headers)
            event = {
                "id": "ef" * 16,
                "ts": "2026-11-09T13:00:00.000Z",
                "type": "decision",
                "task": "run-new",
                "action": {"kind": "tool", "resource": "files.write"},
                "effect": "allow",
                "rule": "agent:tools.allow[0]",
                "span_id": "aabbccddeeff0011",
                "parent_span_id": "1122334455667788",
            }
            uploaded = client.post(
                "/v1/events",
                headers={"Authorization": f"Bearer {token}"},
                json={"events": [event]},
            )
            assert uploaded.status_code == 200
        engine = make_engine(database_url)
        try:
            with session_factory(engine)() as session:
                row = session.get(Event, "ef" * 16)
                assert row is not None
                assert row.run_id == "run-new"
                assert row.kind == "tool"
                assert row.resource == "files.write"
                assert row.effect == "allow"
                assert row.rule == "agent:tools.allow[0]"
                assert row.span_id == "aabbccddeeff0011"
                assert row.parent_span_id == "1122334455667788"
        finally:
            engine.dispose()
    finally:
        downgrade_database(database_url)


def _strip_event_index(connection: Connection) -> None:
    """Remove 0012 columns/indexes so the schema matches a 0.5 database.

    Args:
        connection: Open SQLAlchemy connection inside a transaction.
    """
    indexes = {index["name"] for index in inspect(connection).get_indexes("events")}
    for name in (
        "ix_events_run_ts",
        "ix_events_span_id",
        "ix_events_agent_type_ts",
        "ix_events_resource",
    ):
        if name in indexes:
            connection.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    columns = {column["name"] for column in inspect(connection).get_columns("events")}
    for name in (
        "run_id",
        "span_id",
        "parent_span_id",
        "kind",
        "resource",
        "effect",
        "status",
        "rule",
    ):
        if name in columns:
            connection.execute(text(f'ALTER TABLE events DROP COLUMN "{name}"'))


def _runtime_token(client: TestClient, headers: dict[str, str]) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ops@example.com", "display_name": "Ops"},
    )
    owner.raise_for_status()
    agent = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
    )
    agent.raise_for_status()
    agent_id = str(agent.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    stored.raise_for_status()
    activated = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
    activated.raise_for_status()
    registration = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={},
    )
    registration.raise_for_status()
    runtime = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": registration.json()["token"],
            "instance_id": "11" * 16,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )
    runtime.raise_for_status()
    return str(runtime.json()["access_token"])
