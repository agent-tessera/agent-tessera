"""Dashboard summary, spend, and denial totals."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Event, Owner
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.dashboards import (
    dashboard_denials,
    dashboard_spend,
    dashboard_summary,
)
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
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
    headers = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(create_app(settings)) as client:
            yield client, headers, settings
    finally:
        downgrade_database(database_url)


def test_seeded_dashboard_totals_match(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    moment = datetime.now(UTC)
    billing, owner_id = _agent(client, headers, "billing", "ada@example.com")
    notes, _owner = _agent(client, headers, "notes", "grace@example.com")
    _deactivate(settings, owner_id)
    _events(settings, billing, moment)
    summary = client.get("/v1/dashboards/summary", headers=headers)
    assert summary.status_code == 200
    body = summary.json()
    assert body["inventory"] == {
        "active": 0,
        "suspended": 0,
        "draft": 2,
        "retired": 0,
        "total": 2,
    }
    assert body["orphaned"] == 1
    assert body["dormant"] == 1
    assert body["pending_approvals"] == 0
    assert body["spend_24h"] == 2.0
    assert body["denials_24h"] == 2
    window = {
        "from": (moment - timedelta(hours=24)).isoformat(),
        "to": moment.isoformat(),
    }
    spend = client.get("/v1/dashboards/spend", headers=headers, params=window)
    assert spend.status_code == 200
    assert spend.json()["items"] == [
        {"agent_id": billing, "name": "billing", "cost_usd": 2.0},
    ]
    by_model = client.get(
        "/v1/dashboards/spend",
        headers=headers,
        params={**window, "group_by": "model"},
    )
    assert by_model.json()["items"] == [
        {"model": "openai/gpt-4o", "cost_usd": 1.25},
        {"model": "openai/gpt-4o-mini", "cost_usd": 0.75},
    ]
    denials = client.get(
        "/v1/dashboards/denials",
        headers=headers,
        params={"from": moment.isoformat(), "to": moment.isoformat()},
    )
    assert denials.json() == {
        "total": 2,
        "items": [{"agent_id": billing, "name": "billing", "denials": 2}],
    }
    assert notes
    rejected = client.get(
        "/v1/dashboards/spend",
        headers=headers,
        params={"group_by": "team", "from": "2026-11-07"},
    )
    assert rejected.status_code == 422


def test_summary_excludes_events_after_now(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    moment = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    billing, _owner = _agent(client, headers, "billing", "ada@example.com")
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            session.add(_usage(billing, "c1" * 16, moment, "openai/gpt-4o", "1.5"))
            session.add(_denial(billing, "c2" * 16, moment))
            session.add(
                _usage(billing, "c3" * 16, moment + timedelta(minutes=1), "openai/gpt-4o", "9")
            )
            session.add(_denial(billing, "c4" * 16, moment + timedelta(minutes=1)))
            session.commit()
            summary = dashboard_summary(session, now=moment)
            spend = dashboard_spend(
                session,
                start=moment - timedelta(hours=24),
                end=moment,
                group_by="agent",
            )
            denials = dashboard_denials(
                session,
                start=moment - timedelta(hours=24),
                end=moment,
            )
    finally:
        engine.dispose()
    assert summary["spend_24h"] == 1.5
    assert summary["denials_24h"] == 1
    assert spend["items"] == [{"agent_id": billing, "name": "billing", "cost_usd": 1.5}]
    assert denials == {
        "total": 1,
        "items": [{"agent_id": billing, "name": "billing", "denials": 1}],
    }


def _agent(client: TestClient, headers: dict[str, str], name: str, email: str) -> tuple[str, str]:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": email, "display_name": name},
    )
    assert owner.status_code == 200
    owner_id = str(owner.json()["id"])
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": name, "environment": "prod", "owner_id": owner_id},
    )
    assert created.status_code == 200
    return str(created.json()["id"]), owner_id


def _deactivate(settings: Settings, owner_id: str) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = session.get(Owner, owner_id)
            assert row is not None
            row.active = False
            session.commit()
    finally:
        engine.dispose()


def _events(settings: Settings, agent_id: str, moment: datetime) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            session.add(_usage(agent_id, "a1" * 16, moment, "openai/gpt-4o", "1.25"))
            session.add(_usage(agent_id, "a2" * 16, moment, "openai/gpt-4o-mini", "0.75"))
            session.add(
                _usage(agent_id, "a3" * 16, moment - timedelta(days=2), "openai/gpt-4o", "9")
            )
            session.add(_denial(agent_id, "b1" * 16, moment))
            session.add(_denial(agent_id, "b2" * 16, moment))
            session.add(_denial(agent_id, "b3" * 16, moment - timedelta(days=2)))
            session.commit()
    finally:
        engine.dispose()


def _usage(agent_id: str, event_id: str, moment: datetime, model: str, cost: str) -> Event:
    return Event(
        id=event_id,
        agent_id=agent_id,
        instance_id="instance",
        event_type="usage",
        ts=moment,
        payload={"model": model, "cost_usd": cost},
    )


def _denial(agent_id: str, event_id: str, moment: datetime) -> Event:
    return Event(
        id=event_id,
        agent_id=agent_id,
        instance_id="instance",
        event_type="decision",
        ts=moment,
        payload={"effect": "deny"},
    )
