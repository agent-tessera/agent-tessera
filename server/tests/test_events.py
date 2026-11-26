"""Event ingestion and the audit read API."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AuditEntry, Event, Run, RunParticipant
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.audit import verify_audit
from tessera_server.services.events import ingest_events, list_events
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
"""

_BREAKER_SUSPEND = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: guarded
  environment: prod
spec:
  circuit_breaker:
    denials: 2
    window: 1h
    action: suspend
"""

_BREAKER_REVOKE = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: runner
  environment: prod
spec:
  circuit_breaker:
    denials: 2
    window: 1h
    action: revoke_run
"""


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], Settings, str]]:
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
            agent_id = _active_agent(client, headers)
            token = _runtime_token(client, headers, agent_id)
            yield client, headers, settings, token
    finally:
        downgrade_database(database_url)


def test_events_keep_the_token_identity(
    api: tuple[TestClient, dict[str, str], Settings, str],
) -> None:
    client, headers, settings, token = api
    event = {
        "id": "ab" * 16,
        "ts": "2026-11-05T16:00:00.000Z",
        "type": "decision",
        "agent": "evil",
        "environment": "dev",
        "instance": "00" * 16,
        "task": "task-1",
        "effect": "deny",
    }
    uploaded = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={"events": [event]},
    )
    assert uploaded.status_code == 200
    assert uploaded.json() == {"accepted": 1, "duplicates": 0}
    again = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={"events": [event]},
    )
    assert again.json() == {"accepted": 0, "duplicates": 1}
    listed = client.get("/v1/audit", headers=headers, params={"type": "decision", "task": "task-1"})
    assert listed.status_code == 200
    stored = listed.json()["items"][0]
    assert stored["agent"] == "billing"
    assert stored["environment"] == "prod"
    assert stored["instance"] != "00" * 16
    assert stored["effect"] == "deny"
    log = client.get("/v1/audit/log", headers=headers)
    assert log.status_code == 200
    assert any(row["type"] == "decision" for row in log.json()["items"])
    invalid = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        content=b"not-json",
    )
    assert invalid.status_code == 422
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            run = session.get(Run, "task-1")
            assert run is not None
            assert run.status == "running"
            participants = session.scalars(select(RunParticipant)).all()
            assert len(participants) == 1
            check = verify_audit(session)
            assert check.ok is True
    finally:
        engine.dispose()
    assert client.post("/v1/events", json={"events": []}).status_code == 401
    too_many = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={"events": [_copy(event, index) for index in range(501)]},
    )
    assert too_many.status_code == 413
    huge = b'{"events":[]}' + b" " * 1_000_000
    oversized = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        content=huge,
    )
    assert oversized.status_code == 413


def test_concurrent_ingest_keeps_the_chain(
    api: tuple[TestClient, dict[str, str], Settings, str],
) -> None:
    _client, headers, settings, token = api
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    errors: list[BaseException] = []

    def upload(index: int) -> None:
        event = {
            "id": f"{index:02d}" + "cd" * 15,
            "ts": "2026-11-05T16:00:00.000Z",
            "type": "usage",
            "task": "shared",
        }
        try:
            with factory() as session:
                ingest_events(session, settings, token, [event])
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=upload, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    try:
        assert errors == []
        with factory() as session:
            check = verify_audit(session)
            assert check.ok is True
            assert check.events >= 4
    finally:
        engine.dispose()
    client = _client
    tampered = client.get("/v1/audit/verify", headers=headers)
    assert tampered.json()["ok"] is True
    _tamper(settings.database_url)
    failed = client.get("/v1/audit/verify", headers=headers)
    assert failed.status_code == 200
    assert failed.json()["ok"] is False
    assert failed.json()["error"] == "hash mismatch"


def test_repeated_denials_suspend_the_agent(
    api: tuple[TestClient, dict[str, str], Settings, str],
) -> None:
    client, headers, _, _ = api
    agent_id = _active_agent(
        client,
        headers,
        name="guarded",
        email="guarded@example.com",
        policy=_BREAKER_SUSPEND,
    )
    token = _runtime_token(client, headers, agent_id, instance_id="cd" * 16)
    for index in range(2):
        uploaded = client.post(
            "/v1/events",
            headers={"Authorization": f"Bearer {token}"},
            json={"events": [_decision(index)]},
        )
        assert uploaded.status_code == 200
    agent = client.get(f"/v1/agents/{agent_id}", headers=headers)
    assert agent.status_code == 200
    assert agent.json()["status"] == "suspended"


def test_repeated_denials_revoke_the_run(
    api: tuple[TestClient, dict[str, str], Settings, str],
) -> None:
    client, headers, _, _ = api
    agent_id = _active_agent(
        client,
        headers,
        name="runner",
        email="runner@example.com",
        policy=_BREAKER_REVOKE,
    )
    token = _runtime_token(client, headers, agent_id, instance_id="ab" * 16)
    for index in range(2):
        uploaded = client.post(
            "/v1/events",
            headers={"Authorization": f"Bearer {token}"},
            json={"events": [_decision(index + 10, task="run-circuit")]},
        )
        assert uploaded.status_code == 200
    run = client.get("/v1/runs/run-circuit", headers=headers)
    assert run.status_code == 200
    assert run.json()["status"] == "revoked"


def _decision(index: int, task: str | None = None) -> dict[str, object]:
    event: dict[str, object] = {
        "id": f"{index:032x}",
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "type": "decision",
        "agent": "ignored",
        "environment": "dev",
        "instance": "00" * 16,
        "effect": "deny",
        "reason": "not_granted",
    }
    if task is not None:
        event["task"] = task
    return event


def _copy(event: dict[str, object], index: int) -> dict[str, object]:
    copied = dict(event)
    copied["id"] = f"{index:032x}"
    return copied


def _tamper(url: str) -> None:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            row = session.scalars(select(AuditEntry).order_by(AuditEntry.seq)).first()
            assert row is not None
            row.payload = {"id": "tampered"}
            session.commit()
    finally:
        engine.dispose()


def _active_agent(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "billing",
    email: str = "a@example.com",
    policy: str = _POLICY,
) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": email, "display_name": "Ada"},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": name, "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=policy,
    )
    assert stored.status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _runtime_token(
    client: TestClient,
    headers: dict[str, str],
    agent_id: str,
    *,
    instance_id: str = "ef" * 16,
) -> str:
    created = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={},
    )
    assert created.status_code == 200
    response = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": created.json()["token"],
            "instance_id": instance_id,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def test_audit_lists_events_by_received_at(
    api: tuple[TestClient, dict[str, str], Settings, str],
) -> None:
    _client, _headers, settings, _token = api
    agent_id = "01JE2E2EAUDIT0000000000000"
    start = datetime(2026, 11, 6, 12, tzinfo=UTC)
    # Ids deliberately reverse chronological so an id-ordered page would be wrong.
    rows = [
        ("ff" * 16, start),
        ("00" * 16, start + timedelta(seconds=1)),
        ("aa" * 16, start + timedelta(seconds=2)),
    ]
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            for event_id, stamp in rows:
                session.add(
                    Event(
                        id=event_id,
                        agent_id=agent_id,
                        instance_id="cd" * 16,
                        event_type="decision",
                        ts=stamp,
                        task="order",
                        payload={"type": "decision", "id": event_id, "effect": "allow"},
                        received_at=stamp,
                    )
                )
            session.commit()
            ascending = list_events(session, agent_id=agent_id, task="order", limit=10)
            assert [item["id"] for item in ascending["items"]] == [
                "ff" * 16,
                "00" * 16,
                "aa" * 16,
            ]
            newest = list_events(
                session, agent_id=agent_id, task="order", limit=10, direction="desc"
            )
            assert [item["id"] for item in newest["items"]] == [
                "aa" * 16,
                "00" * 16,
                "ff" * 16,
            ]
    finally:
        engine.dispose()
