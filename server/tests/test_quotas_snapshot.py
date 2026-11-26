"""Admin `GET /v1/quotas` snapshot."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  limits:
    tokens_per_day: 10000
    quotas:
      - name: tool-calls
        metric: tool_calls
        window: day
        scope: agent
        max: 50
  tools:
    allow: ["*"]
"""


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str]]]:
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
            yield client, headers
    finally:
        downgrade_database(database_url)


def test_quota_snapshot_returns_ceilings(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = api
    agent_id = _agent(client, headers)
    page = client.get("/v1/quotas", headers=headers, params={"agent_id": agent_id})
    assert page.status_code == 200
    items = page.json()["items"]
    by_name = {row["name"]: row for row in items}
    assert by_name["tokens_per_day"]["max"] == 10000
    assert by_name["tokens_per_day"]["used"] == 0
    assert by_name["tokens_per_day"]["outstanding"] == 0
    assert by_name["tokens_per_day"]["metric"] == "tokens"
    assert by_name["tool-calls"]["max"] == 50
    assert by_name["tool-calls"]["scope"] == "agent"

    filtered = client.get(
        "/v1/quotas",
        headers=headers,
        params={"environment": "missing"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []

    owner_id = client.get("/v1/agents/" + agent_id, headers=headers).json()["owner_id"]
    no_policy = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "drafty",
            "environment": "prod",
            "owner_id": owner_id,
        },
    )
    assert no_policy.status_code == 200
    empty = client.get(
        "/v1/quotas",
        headers=headers,
        params={"agent_id": no_policy.json()["id"]},
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_quota_snapshot_requires_admin(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, _headers = api
    assert client.get("/v1/quotas").status_code == 401


def _agent(client: TestClient, headers: dict[str, str]) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={
            "type": "user",
            "email": "ops@example.com",
            "display_name": "Ops",
        },
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "billing",
            "environment": "prod",
            "owner_id": owner.json()["id"],
        },
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
