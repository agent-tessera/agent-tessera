"""Admin `GET /v1/revocations` list."""

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


def test_list_revocations_newest_first_and_filters(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = api
    agent_id = _agent(client, headers)
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:suspend", headers=headers).status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200

    page = client.get("/v1/revocations", headers=headers, params={"limit": 2})
    assert page.status_code == 200
    body = page.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
    assert body["items"][0]["id"] > body["items"][1]["id"]
    assert body["items"][0]["kind"] == "agent_activated"

    next_page = client.get(
        "/v1/revocations",
        headers=headers,
        params={"limit": 10, "cursor": body["next_cursor"]},
    )
    assert next_page.status_code == 200
    next_body = next_page.json()
    seen = {item["id"] for item in body["items"]}
    assert seen.isdisjoint({item["id"] for item in next_body["items"]})

    suspended = client.get(
        "/v1/revocations",
        headers=headers,
        params={"kind": "agent_suspended", "agent_id": agent_id},
    )
    assert suspended.status_code == 200
    rows = suspended.json()["items"]
    assert rows
    assert all(row["kind"] == "agent_suspended" for row in rows)
    assert all(row["agent_id"] == agent_id for row in rows)

    bad = client.get("/v1/revocations", headers=headers, params={"cursor": "soon"})
    assert bad.status_code == 422


def test_list_revocations_rejects_runtime_jwt(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = api
    agent_id = _agent(client, headers)
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    token = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={},
    )
    assert token.status_code == 200
    runtime = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": token.json()["token"],
            "instance_id": "ab" * 16,
            "host": "host",
            "runtime": {"sdk": "0.6.0"},
        },
    )
    assert runtime.status_code == 200
    denied = client.get(
        "/v1/revocations",
        headers={"Authorization": f"Bearer {runtime.json()['access_token']}"},
    )
    assert denied.status_code in {401, 403}


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
