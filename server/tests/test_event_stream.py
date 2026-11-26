"""Event SSE stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Event, Owner
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.event_query import encode_cursor
from tessera_server.services.secrets import create_admin_key

_START = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


@pytest.fixture
def stream_app(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, str], str, str]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
            owner = Owner(
                id=new_ulid(),
                owner_type="user",
                email="stream@example.com",
                display_name="ops",
            )
            session.add(owner)
            session.flush()
            agent = Agent(
                id=new_ulid(),
                name="streamer",
                environment="prod",
                status="active",
                owner_id=owner.id,
            )
            other = Agent(
                id=new_ulid(),
                name="other",
                environment="prod",
                status="active",
                owner_id=owner.id,
            )
            session.add_all([agent, other])
            session.flush()
            for index, agent_row in enumerate((agent, agent, other)):
                stamp = _START + timedelta(seconds=index)
                event_id = f"{index + 1:032x}"
                session.add(
                    Event(
                        id=event_id,
                        agent_id=agent_row.id,
                        instance_id="cd" * 16,
                        event_type="decision",
                        ts=stamp,
                        task="run",
                        run_id="run",
                        span_id=f"{index + 1:016x}",
                        kind="tool",
                        resource="files.read",
                        effect="allow",
                        status="ok",
                        rule="r",
                        payload={
                            "id": event_id,
                            "ts": stamp.isoformat().replace("+00:00", "Z"),
                            "effect": "allow",
                        },
                        received_at=stamp,
                    )
                )
            session.commit()
            agent_id = agent.id
    finally:
        engine.dispose()

    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("tessera_server.api.event_reads.asyncio.sleep", fast_sleep)
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
            yield client, headers, agent_id, database_url
    finally:
        downgrade_database(database_url)


def _read_events(
    client: TestClient,
    headers: dict[str, str],
    *,
    count: int = 2,
    **params: str,
) -> list[dict[str, object]]:
    with client.stream("GET", "/v1/events/stream", headers=headers, params=params) as response:
        assert response.status_code == 200
        items: list[dict[str, object]] = []
        event_id: str | None = None
        for line in response.iter_lines():
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                payload["_cursor"] = event_id
                items.append(payload)
                if len(items) >= count:
                    break
            elif line.startswith("event: lag"):
                items.append({"_lag": True})
                break
        return items


def test_stream_batches_in_order(
    stream_app: tuple[TestClient, dict[str, str], str, str],
) -> None:
    client, headers, agent_id, _url = stream_app
    items = _read_events(client, headers, count=2, agent=agent_id)
    ids = [str(item["id"]) for item in items]
    assert ids == ["00000000000000000000000000000001", "00000000000000000000000000000002"]
    assert ids == list(dict.fromkeys(ids))


def test_stream_resume_and_filter(
    stream_app: tuple[TestClient, dict[str, str], str, str],
) -> None:
    client, headers, agent_id, database_url = stream_app
    cursor = encode_cursor(_START, "00000000000000000000000000000001")
    with client.stream(
        "GET",
        "/v1/events/stream",
        headers={**headers, "Last-Event-ID": cursor},
        params={"agent": agent_id},
    ) as response:
        assert response.status_code == 200
        seen: list[str] = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                seen.append(str(payload["id"]))
                if len(seen) >= 1:
                    break
    assert seen == ["00000000000000000000000000000002"]
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            stamp = _START + timedelta(seconds=10)
            session.add(
                Event(
                    id="00000000000000000000000000000009",
                    agent_id=agent_id,
                    instance_id="cd" * 16,
                    event_type="decision",
                    ts=stamp,
                    task="run",
                    run_id="run",
                    span_id="9" * 16,
                    kind="tool",
                    resource="files.read",
                    effect="allow",
                    status="ok",
                    rule="r",
                    payload={
                        "id": "00000000000000000000000000000009",
                        "ts": stamp.isoformat().replace("+00:00", "Z"),
                    },
                    received_at=stamp,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    resume = encode_cursor(_START + timedelta(seconds=1), "00000000000000000000000000000002")
    with client.stream(
        "GET",
        "/v1/events/stream",
        headers={**headers, "Last-Event-ID": resume},
        params={"agent": agent_id},
    ) as response:
        got: list[str] = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                got.append(str(json.loads(line[6:])["id"]))
                break
    assert got == ["00000000000000000000000000000009"]
    filtered = _read_events(client, headers, count=1, agent="other")
    assert [str(item["id"]) for item in filtered] == ["00000000000000000000000000000003"]
