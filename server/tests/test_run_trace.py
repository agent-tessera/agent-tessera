"""Run governance trace API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Event, Owner, Run, RunParticipant
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.secrets import create_admin_key

_START = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], str]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
            owner = Owner(
                id=new_ulid(),
                owner_type="user",
                email="trace@example.com",
                display_name="ops",
            )
            session.add(owner)
            session.flush()
            a1 = Agent(
                id=new_ulid(),
                name="alpha",
                environment="prod",
                status="active",
                owner_id=owner.id,
            )
            a2 = Agent(
                id=new_ulid(),
                name="beta",
                environment="prod",
                status="active",
                owner_id=owner.id,
            )
            session.add_all([a1, a2])
            session.flush()
            run_id = "run-trace-1"
            session.add(
                Run(
                    id=run_id,
                    status="running",
                    first_seen_at=_START,
                    last_seen_at=_START + timedelta(seconds=30),
                )
            )
            session.add_all(
                [
                    RunParticipant(run_id=run_id, instance_id="aa" * 16, agent_id=a1.id),
                    RunParticipant(run_id=run_id, instance_id="bb" * 16, agent_id=a2.id),
                ]
            )
            session.add(
                _evt(
                    "1" * 32,
                    a1.id,
                    _START,
                    "decision",
                    span="1111111111111111",
                    parent=None,
                    effect="allow",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.add(
                _evt(
                    "2" * 32,
                    a1.id,
                    _START + timedelta(seconds=2),
                    "call",
                    span="1111111111111111",
                    parent=None,
                    effect="allow",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.add(
                _evt(
                    "3" * 32,
                    a2.id,
                    _START + timedelta(seconds=3),
                    "decision",
                    span="2222222222222222",
                    parent="1111111111111111",
                    effect="deny",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.add(
                _evt(
                    "4" * 32,
                    a2.id,
                    _START + timedelta(seconds=4),
                    "decision",
                    span="3333333333333333",
                    parent="deadbeefdeadbeef",
                    effect="require_approval",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.add(
                _evt(
                    "5" * 32,
                    a2.id,
                    _START + timedelta(seconds=5),
                    "approval",
                    span="3333333333333333",
                    parent="deadbeefdeadbeef",
                    effect="require_approval",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.add(
                _evt(
                    "6" * 32,
                    a1.id,
                    _START + timedelta(seconds=6),
                    "decision",
                    span="4444444444444444",
                    parent="1111111111111111",
                    effect="allow",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.add(
                _evt(
                    "7" * 32,
                    a1.id,
                    _START + timedelta(seconds=7),
                    "call",
                    span="4444444444444444",
                    parent="1111111111111111",
                    effect="allow",
                    status="error",
                    run_id=run_id,
                    extra={"error_type": "Timeout"},
                )
            )
            session.add(
                _evt(
                    "8" * 32,
                    a1.id,
                    _START + timedelta(seconds=8),
                    "decision",
                    span=None,
                    parent=None,
                    effect="allow",
                    status="ok",
                    run_id=run_id,
                )
            )
            session.commit()
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
            yield test_client, headers, run_id
    finally:
        downgrade_database(database_url)


def _evt(
    event_id: str,
    agent_id: str,
    stamp: datetime,
    event_type: str,
    *,
    span: str | None,
    parent: str | None,
    effect: str,
    status: str,
    run_id: str,
    extra: dict[str, object] | None = None,
) -> Event:
    payload: dict[str, object] = {
        "id": event_id,
        "type": event_type,
        "ts": stamp.isoformat().replace("+00:00", "Z"),
        "effect": effect,
        "status": status,
        "run": run_id,
        "action": {"kind": "tool", "resource": "files.read"},
        **(extra or {}),
    }
    if span:
        payload["span_id"] = span
    if parent:
        payload["parent_span_id"] = parent
    return Event(
        id=event_id,
        agent_id=agent_id,
        instance_id="cc" * 16,
        event_type=event_type,
        ts=stamp,
        task=run_id,
        run_id=run_id,
        span_id=span,
        parent_span_id=parent,
        kind="tool",
        resource="files.read",
        effect=effect,
        status=status,
        rule="r",
        payload=payload,
        received_at=stamp,
    )


def test_trace_tree(client: tuple[TestClient, dict[str, str], str]) -> None:
    test_client, headers, run_id = client
    response = test_client.get(f"/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["id"] == run_id
    assert {agent["name"] for agent in body["agents"]} == {"alpha", "beta"}
    spans = {span["span_id"]: span for span in body["spans"]}
    assert spans["1111111111111111"]["duration_ms"] == 2000
    assert spans["2222222222222222"]["decision"]["effect"] == "deny"
    assert spans["2222222222222222"]["parent_span_id"] == "1111111111111111"
    assert spans["3333333333333333"]["parent_span_id"] is None
    assert spans["3333333333333333"]["approval_id"]
    assert spans["4444444444444444"]["status"] == "error"
    assert spans["4444444444444444"]["error_type"] == "Timeout"
    assert len(body["unparented"]) == 1
    assert body["unparented"][0]["id"] == "8" * 32
    assert body["truncated"] is False


def test_trace_truncated(client: tuple[TestClient, dict[str, str], str], database_url: str) -> None:
    test_client, headers, run_id = client
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            agent_id = session.scalars(select(Agent.id)).first()
            assert agent_id is not None
            for index in range(5001):
                span = f"{index:016x}"
                event_id = f"{index:032x}"
                stamp = _START + timedelta(seconds=10 + index)
                session.add(
                    _evt(
                        event_id,
                        str(agent_id),
                        stamp,
                        "decision",
                        span=span,
                        parent=None,
                        effect="allow",
                        status="ok",
                        run_id=run_id,
                    )
                )
            session.commit()
    finally:
        engine.dispose()
    response = test_client.get(f"/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200
    assert response.json()["truncated"] is True
    assert len(response.json()["spans"]) == 5000


def test_runs_list_filters(client: tuple[TestClient, dict[str, str], str]) -> None:
    test_client, headers, run_id = client
    response = test_client.get(
        "/v1/runs",
        headers=headers,
        params={"has_denials": "true", "status": "running"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert any(item["id"] == run_id for item in items)
    assert items[0]["denial_count"] >= 1
