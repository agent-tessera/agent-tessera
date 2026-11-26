"""Event histogram API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from math import floor

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Event, Owner
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.secrets import create_admin_key

_START = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
_END = _START + timedelta(hours=1)


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str]]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
            owner = Owner(
                id=new_ulid(),
                owner_type="user",
                email="hist@example.com",
                display_name="ops",
            )
            session.add(owner)
            session.flush()
            agent = Agent(
                id=new_ulid(),
                name="hist",
                environment="prod",
                status="active",
                owner_id=owner.id,
            )
            session.add(agent)
            session.flush()
            seeds = [
                (_START + timedelta(minutes=5), "deny", "ok"),
                (_START + timedelta(minutes=5, seconds=30), "allow", "ok"),
                (_START + timedelta(minutes=20), "require_approval", "ok"),
                (_START + timedelta(minutes=45), "allow", "error"),
                (_START + timedelta(minutes=45, seconds=10), "deny", "error"),
            ]
            for index, (stamp, effect, status) in enumerate(seeds):
                event_id = f"{index:032x}"
                session.add(
                    Event(
                        id=event_id,
                        agent_id=agent.id,
                        instance_id="ab" * 16,
                        event_type="decision",
                        ts=stamp,
                        task="run",
                        run_id="run",
                        span_id=f"{index:016x}",
                        kind="tool",
                        resource="files.read",
                        effect=effect,
                        status=status,
                        rule="r",
                        payload={"id": event_id, "ts": stamp.isoformat(), "effect": effect},
                        received_at=stamp,
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
            yield test_client, headers
    finally:
        downgrade_database(database_url)


def _reference(
    stamps: list[tuple[datetime, str, str]],
    *,
    start: datetime,
    end: datetime,
    buckets: int,
) -> list[dict[str, int]]:
    span = max(1.0, (end - start).total_seconds())
    width = max(1, int(span // buckets) or 1)
    count = max(1, int((span + width - 1) // width))
    series = [{"total": 0, "deny": 0, "require_approval": 0, "error": 0} for _ in range(count)]
    for stamp, effect, status in stamps:
        index = floor((stamp.timestamp() - start.timestamp()) / width)
        if index < 0 or index >= count:
            continue
        bucket = series[index]
        bucket["total"] += 1
        if effect == "deny":
            bucket["deny"] += 1
        if effect == "require_approval":
            bucket["require_approval"] += 1
        if status == "error":
            bucket["error"] += 1
    return series


@pytest.mark.parametrize("buckets", [10, 30, 60])
def test_histogram_matches_reference(
    client: tuple[TestClient, dict[str, str]], buckets: int
) -> None:
    test_client, headers = client
    response = test_client.get(
        "/v1/events/histogram",
        headers=headers,
        params={
            "since": _START.isoformat().replace("+00:00", "Z"),
            "until": _END.isoformat().replace("+00:00", "Z"),
            "buckets": buckets,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bucket_seconds"] >= 1
    seeds = [
        (_START + timedelta(minutes=5), "deny", "ok"),
        (_START + timedelta(minutes=5, seconds=30), "allow", "ok"),
        (_START + timedelta(minutes=20), "require_approval", "ok"),
        (_START + timedelta(minutes=45), "allow", "error"),
        (_START + timedelta(minutes=45, seconds=10), "deny", "error"),
    ]
    expected = _reference(seeds, start=_START, end=_END, buckets=buckets)
    actual = [
        {
            "total": row["total"],
            "deny": row["deny"],
            "require_approval": row["require_approval"],
            "error": row["error"],
        }
        for row in body["series"]
    ]
    assert actual == expected
    assert sum(row["total"] for row in actual) == 5
