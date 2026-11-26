"""Access footprint for one agent."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Event
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["files.read", "files.write"]
"""


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


def test_access_footprint_matches_offline_semantics(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers)
    moment = datetime.now(UTC)
    _events(settings, agent_id, moment)
    report = client.get(f"/v1/agents/{agent_id}/footprint/access", headers=headers)
    assert report.status_code == 200
    body = report.json()
    patterns = [item["pattern"] for item in body["declared"]]
    assert patterns == ["files.read", "files.write"]
    assert body["observed"] == [{"kind": "tool", "resource": "files.read", "count": 1}]
    assert [item["pattern"] for item in body["unused"]] == ["files.write"]
    assert body["denied"] == [{"kind": "tool", "resource": "files.delete", "count": 1}]
    missing = client.get("/v1/agents/missing/footprint/access", headers=headers)
    assert missing.status_code == 404


def _agent(client: TestClient, headers: dict[str, str]) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    assert stored.status_code == 200
    return agent_id


def _events(settings: Settings, agent_id: str, moment: datetime) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            session.add(
                _decision(
                    agent_id,
                    "a1" * 16,
                    moment,
                    "files.read",
                    effect="allow",
                    reason="allow_rule",
                )
            )
            session.add(
                _decision(
                    agent_id,
                    "a2" * 16,
                    moment,
                    "files.delete",
                    effect="deny",
                    reason="not_granted",
                )
            )
            session.add(
                _decision(
                    agent_id,
                    "a3" * 16,
                    moment - timedelta(days=31),
                    "files.write",
                    effect="allow",
                    reason="allow_rule",
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _decision(
    agent_id: str,
    event_id: str,
    moment: datetime,
    resource: str,
    *,
    effect: str,
    reason: str,
) -> Event:
    return Event(
        id=event_id,
        agent_id=agent_id,
        instance_id="instance",
        event_type="decision",
        ts=moment,
        payload={
            "type": "decision",
            "agent": "billing",
            "ts": moment.isoformat(),
            "effect": effect,
            "reason": reason,
            "action": {"kind": "tool", "resource": resource},
        },
    )
