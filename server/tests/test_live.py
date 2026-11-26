"""Live event reads show recent call and decision events."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Event, Run
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["*"]
"""
_RUN = "run-live"
_COUNT = 51


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str]]]:
    upgrade_database(database_url)
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as api:
            with app.state.sessions() as session:
                admin = create_admin_key(session, "ops")
            headers = {"Authorization": f"Bearer {admin}"}
            yield api, headers
    finally:
        downgrade_database(database_url)


def test_events_list_shows_the_newest_calls(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    api, headers = client
    agent_id = _agent(api, headers)
    moment = datetime(2026, 11, 6, tzinfo=UTC)
    with api.app.state.sessions() as session:  # type: ignore[attr-defined]
        session.add(
            Run(
                id=_RUN,
                status="running",
                first_seen_at=moment,
                last_seen_at=moment,
            )
        )
        session.add(
            Event(
                id="ff" * 16,
                agent_id=agent_id,
                instance_id="cd" * 16,
                event_type="usage",
                ts=moment,
                task=_RUN,
                payload={"type": "usage", "resource": "not-a-call"},
                received_at=moment,
            )
        )
        for index in range(_COUNT):
            stamp = moment + timedelta(seconds=index + 1)
            session.add(
                Event(
                    id=f"{index:032x}",
                    agent_id=agent_id,
                    instance_id="cd" * 16,
                    event_type="call",
                    ts=stamp,
                    task=_RUN,
                    payload={"type": "call", "resource": f"tool-{index}"},
                    received_at=stamp,
                )
            )
        session.commit()
    listed = api.get(
        "/v1/events",
        headers=headers,
        params={"agent": agent_id, "type": "call", "limit": 50},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    resources = [str(item.get("resource", "")) for item in items]
    assert "tool-50" in resources
    assert "tool-0" not in resources
    assert "not-a-call" not in resources
    by_agent = api.get(
        "/v1/events",
        headers=headers,
        params={"agent": agent_id, "type": "call", "limit": 10},
    )
    assert by_agent.status_code == 200, by_agent.text
    assert "tool-50" in str(by_agent.json())


def test_events_list_shows_decision_action_resource(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    api, headers = client
    agent_id = _agent(api, headers)
    moment = datetime(2026, 11, 6, 12, tzinfo=UTC)
    with api.app.state.sessions() as session:  # type: ignore[attr-defined]
        session.add(
            Event(
                id="ab" * 16,
                agent_id=agent_id,
                instance_id="cd" * 16,
                event_type="decision",
                ts=moment,
                task="demo",
                payload={
                    "type": "decision",
                    "action": {"kind": "tool", "resource": "files.delete"},
                    "effect": "allow",
                    "rule": "agent:tools.allow[0]",
                },
                received_at=moment,
            )
        )
        session.commit()
    listed = api.get(
        "/v1/events",
        headers=headers,
        params={"agent": agent_id, "type": "decision"},
    )
    assert listed.status_code == 200, listed.text
    text = str(listed.json())
    assert "files.delete" in text
    assert "tool" in text or "decision" in text


def _agent(api: TestClient, headers: dict[str, str]) -> str:
    owner = api.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200, owner.text
    created = api.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "billing",
            "environment": "prod",
            "owner_id": owner.json()["id"],
        },
    )
    assert created.status_code == 200, created.text
    agent_id = str(created.json()["id"])
    stored = api.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    assert stored.status_code == 200, stored.text
    assert api.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id
