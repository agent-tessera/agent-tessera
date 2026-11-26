"""Console event query and facets."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Event, Owner
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.event_query import encode_cursor
from tessera_server.services.secrets import create_admin_key

_START = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], str, str]]:
    """App with two agents and seeded indexed events."""
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
            owner = Owner(
                id=new_ulid(),
                owner_type="user",
                email="ops@example.com",
                display_name="ops",
            )
            session.add(owner)
            session.flush()
            billing = Agent(
                id=new_ulid(),
                name="billing",
                environment="prod",
                status="active",
                owner_id=owner.id,
                description="billing bot",
            )
            support = Agent(
                id=new_ulid(),
                name="support",
                environment="prod",
                status="active",
                owner_id=owner.id,
                description="support bot",
            )
            session.add_all([billing, support])
            session.flush()
            rows = [
                _event(
                    "a" * 32,
                    billing.id,
                    _START,
                    event_type="decision",
                    kind="tool",
                    resource="files.read",
                    effect="deny",
                    status="ok",
                    rule="agent:tools.deny[0]",
                    run_id="run-1",
                    span_id="1111111111111111",
                ),
                _event(
                    "b" * 32,
                    billing.id,
                    _START + timedelta(seconds=1),
                    event_type="call",
                    kind="tool",
                    resource="files.write",
                    effect="allow",
                    status="ok",
                    rule="agent:tools.allow[0]",
                    run_id="run-1",
                    span_id="2222222222222222",
                ),
                _event(
                    "c" * 32,
                    support.id,
                    _START + timedelta(seconds=2),
                    event_type="decision",
                    kind="model",
                    resource="openai.chat",
                    effect="require_approval",
                    status="error",
                    rule="baseline:models[0]",
                    run_id="run-2",
                    span_id="3333333333333333",
                ),
                _event(
                    "d" * 32,
                    support.id,
                    _START + timedelta(seconds=3),
                    event_type="decision",
                    kind="data",
                    resource="Files.READ",
                    effect="deny",
                    status="ok",
                    rule="agent:data.deny[0]",
                    run_id="run-2",
                    span_id="4444444444444444",
                ),
            ]
            session.add_all(rows)
            session.commit()
            billing_id = billing.id
            support_id = support.id
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
        with TestClient(app) as test_client:
            yield test_client, headers, billing_id, support_id
    finally:
        downgrade_database(database_url)


def _event(
    event_id: str,
    agent_id: str,
    stamp: datetime,
    *,
    event_type: str,
    kind: str,
    resource: str,
    effect: str,
    status: str,
    rule: str,
    run_id: str,
    span_id: str,
) -> Event:
    payload: dict[str, object] = {
        "id": event_id,
        "type": event_type,
        "ts": stamp.isoformat().replace("+00:00", "Z"),
        "effect": effect,
        "status": status,
        "rule": rule,
        "run": run_id,
        "span_id": span_id,
        "action": {"kind": kind, "resource": resource},
    }
    return Event(
        id=event_id,
        agent_id=agent_id,
        instance_id="ef" * 16,
        event_type=event_type,
        ts=stamp,
        task=run_id,
        run_id=run_id,
        span_id=span_id,
        kind=kind,
        resource=resource,
        effect=effect,
        status=status,
        rule=rule,
        payload=payload,
        received_at=stamp,
    )


@pytest.mark.parametrize(
    ("params", "expected_ids"),
    [
        ({}, ["d" * 32, "c" * 32, "b" * 32, "a" * 32]),
        ({"type": "call"}, ["b" * 32]),
        ({"kind": "model"}, ["c" * 32]),
        ({"effect": "deny"}, ["d" * 32, "a" * 32]),
        ({"status": "error"}, ["c" * 32]),
        ({"resource": "files.read"}, ["d" * 32, "a" * 32]),
        ({"rule": "agent:tools.deny[0]"}, ["a" * 32]),
        ({"run": "run-2"}, ["d" * 32, "c" * 32]),
        ({"span": "2222222222222222"}, ["b" * 32]),
        ({"agent": "billing", "effect": "allow"}, ["b" * 32]),
        ({"type": "decision", "kind": "data"}, ["d" * 32]),
        ({"agent": "missing-agent"}, []),
    ],
)
def test_event_filters(
    client: tuple[TestClient, dict[str, str], str, str],
    params: dict[str, str],
    expected_ids: list[str],
) -> None:
    test_client, headers, _billing, _support = client
    response = test_client.get("/v1/events", headers=headers, params=params)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == expected_ids
    if items:
        assert "agent_name" in items[0]


def test_since_until_and_relative(
    client: tuple[TestClient, dict[str, str], str, str],
) -> None:
    test_client, headers, _billing, _support = client
    since = (_START + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    until = (_START + timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
    page = test_client.get(
        "/v1/events",
        headers=headers,
        params={"since": since, "until": until},
    )
    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == ["c" * 32, "b" * 32]
    bad = test_client.get("/v1/events", headers=headers, params={"since": "yesterday"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_request"
    relative = test_client.get("/v1/events", headers=headers, params={"since": "1h"})
    assert relative.status_code == 200
    assert relative.json()["items"] == []


def test_cursor_pagination_with_inserts(
    client: tuple[TestClient, dict[str, str], str, str], database_url: str
) -> None:
    test_client, headers, billing_id, _support = client
    first = test_client.get("/v1/events", headers=headers, params={"limit": 2})
    assert first.status_code == 200
    body = first.json()
    assert [item["id"] for item in body["items"]] == ["d" * 32, "c" * 32]
    cursor = body["next_cursor"]
    assert cursor
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            stamp = _START + timedelta(seconds=4)
            session.add(
                _event(
                    "e" * 32,
                    billing_id,
                    stamp,
                    event_type="decision",
                    kind="tool",
                    resource="files.read",
                    effect="allow",
                    status="ok",
                    rule="agent:tools.allow[1]",
                    run_id="run-1",
                    span_id="5555555555555555",
                )
            )
            session.commit()
    finally:
        engine.dispose()
    second = test_client.get(
        "/v1/events",
        headers=headers,
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["b" * 32, "a" * 32]
    seen = {item["id"] for item in body["items"]} | {item["id"] for item in second.json()["items"]}
    assert "e" * 32 not in seen
    assert encode_cursor(_START + timedelta(seconds=2), "c" * 32)


def test_facets_top_values(client: tuple[TestClient, dict[str, str], str, str]) -> None:
    test_client, headers, _billing, _support = client
    response = test_client.get("/v1/events/facets", headers=headers)
    assert response.status_code == 200
    facets = response.json()["facets"]
    assert {item["value"] for item in facets["type"]} == {"decision", "call"}
    assert {item["value"] for item in facets["effect"]} >= {"deny", "allow", "require_approval"}
    assert {item["value"] for item in facets["agent"]} == {"billing", "support"}


def test_sqlite_uses_indexes(
    client: tuple[TestClient, dict[str, str], str, str], database_url: str
) -> None:
    if not database_url.startswith("sqlite"):
        pytest.skip("EXPLAIN QUERY PLAN check is SQLite-only")
    _test_client, _headers, billing_id, _support = client
    engine = make_engine(database_url)
    try:
        with engine.connect() as connection:
            run_plan = connection.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM events WHERE run_id = :run"),
                {"run": "run-1"},
            ).fetchall()
            agent_plan = connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT * FROM events "
                    "WHERE agent_id = :agent AND type = :type"
                ),
                {"agent": billing_id, "type": "decision"},
            ).fetchall()
    finally:
        engine.dispose()
    run_text = " ".join(str(row) for row in run_plan).lower()
    agent_text = " ".join(str(row) for row in agent_plan).lower()
    assert "ix_events_run_ts" in run_text or "using index" in run_text
    assert "ix_events_agent_type_ts" in agent_text or "using index" in agent_text


def test_owner_model_fields(client: tuple[TestClient, dict[str, str], str, str]) -> None:
    """Keep mypy happy that the fixture shaped agents correctly."""
    test_client, headers, billing_id, support_id = client
    assert billing_id != support_id
    page = test_client.get("/v1/events", headers=headers, params={"agent": billing_id})
    assert page.status_code == 200
    assert all(item["agent_name"] == "billing" for item in page.json()["items"])
