"""Posture score math and the agent posture route."""

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
from tessera_server.services.posture import score_posture
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  guardrails:
    required: ["input.pii"]
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


def test_perfect_posture_is_one_hundred() -> None:
    body = score_posture(
        owner_ok=True,
        has_policy=True,
        required_names=("input.pii",),
        declared_names=frozenset({"input.pii"}),
        input_required=("input.pii",),
        ran=frozenset({"input.pii"}),
        decisions=0,
        denials=0,
        ungoverned=0,
    )
    assert body["score"] == 100
    assert body["coverage"] == 1.0
    assert body["components"] == {
        "owner": 20,
        "guardrails_declared": 20,
        "coverage": 30.0,
        "denial_rate": 15.0,
        "ungoverned": 15,
        "probes": 0,
    }


def test_partial_coverage_and_a_mid_denial_rate() -> None:
    half = score_posture(
        owner_ok=True,
        has_policy=True,
        required_names=("input.pii", "input.injection"),
        declared_names=frozenset({"input.pii", "input.injection"}),
        input_required=("input.pii", "input.injection"),
        ran=frozenset({"input.pii"}),
        decisions=20,
        denials=1,
        ungoverned=0,
    )
    assert half["coverage"] == 0.5
    assert half["components"]["coverage"] == 15.0
    assert half["components"]["denial_rate"] == 15.0
    assert half["score"] == 85
    mid = score_posture(
        owner_ok=True,
        has_policy=True,
        required_names=(),
        declared_names=frozenset(),
        input_required=(),
        ran=frozenset(),
        decisions=20,
        denials=3,
        ungoverned=0,
    )
    assert mid["components"]["denial_rate"] == 7.5
    assert mid["score"] == 93
    none = score_posture(
        owner_ok=False,
        has_policy=False,
        required_names=(),
        declared_names=frozenset(),
        input_required=(),
        ran=frozenset(),
        decisions=4,
        denials=4,
        ungoverned=1,
    )
    assert none["coverage"] == 0.0
    assert none["components"]["denial_rate"] == 0.0
    assert none["score"] == 0


def test_edges_of_the_denial_band_and_a_missing_declaration() -> None:
    full = score_posture(
        owner_ok=True,
        has_policy=True,
        required_names=("input.pii", "output.moderation"),
        declared_names=frozenset({"input.pii"}),
        input_required=("input.pii",),
        ran=frozenset({"input.pii"}),
        decisions=100,
        denials=5,
        ungoverned=0,
    )
    assert full["components"]["guardrails_declared"] == 0
    assert full["components"]["denial_rate"] == 15.0
    zero = score_posture(
        owner_ok=True,
        has_policy=True,
        required_names=(),
        declared_names=frozenset(),
        input_required=(),
        ran=frozenset(),
        decisions=4,
        denials=1,
        ungoverned=2,
    )
    assert zero["components"]["denial_rate"] == 0.0
    assert zero["components"]["ungoverned"] == 0


def test_route_scores_the_last_seven_days(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers)
    moment = datetime.now(UTC)
    _events(settings, agent_id, moment)
    body = client.get(f"/v1/agents/{agent_id}/posture", headers=headers)
    assert body.status_code == 200
    assert body.json()["score"] == 100
    assert body.json()["coverage"] == 1.0
    missing = client.get("/v1/agents/missing/posture", headers=headers)
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
                _event(
                    agent_id,
                    "ab" * 16,
                    "guardrail",
                    moment,
                    {"name": "input.pii", "status": "ran"},
                )
            )
            session.add(
                _event(
                    agent_id,
                    "cd" * 16,
                    "ungoverned_call",
                    moment - timedelta(days=8),
                    {"host": "example.test"},
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _event(
    agent_id: str, event_id: str, event_type: str, moment: datetime, payload: dict[str, object]
) -> Event:
    return Event(
        id=event_id,
        agent_id=agent_id,
        instance_id="instance",
        event_type=event_type,
        ts=moment,
        payload=payload,
    )
