"""Console parity JSON routes."""

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
  agent: parity
  environment: prod
spec:
  tools:
    allow: ["files.read"]
"""


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], str]]:
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
        with TestClient(app) as test_client:
            owner = test_client.post(
                "/v1/owners",
                headers=headers,
                json={"type": "user", "email": "parity@example.com", "display_name": "ops"},
            )
            assert owner.status_code == 200
            agent = test_client.post(
                "/v1/agents",
                headers=headers,
                json={
                    "name": "parity",
                    "environment": "prod",
                    "owner_id": owner.json()["id"],
                    "description": "parity agent",
                },
            )
            assert agent.status_code == 200
            agent_id = str(agent.json()["id"])
            stored = test_client.put(
                f"/v1/agents/{agent_id}/policy",
                headers={**headers, "Content-Type": "application/yaml"},
                content=_POLICY,
            )
            assert stored.status_code == 200
            yield test_client, headers, agent_id
    finally:
        downgrade_database(database_url)


def test_parity_routes(client: tuple[TestClient, dict[str, str], str]) -> None:
    test_client, headers, agent_id = client
    posture = test_client.get("/v1/posture", headers=headers)
    assert posture.status_code == 200
    assert any(item["id"] == agent_id for item in posture.json()["items"])

    instances = test_client.get(f"/v1/agents/{agent_id}/instances", headers=headers)
    assert instances.status_code == 200
    assert instances.json()["items"] == []

    valid = test_client.post(
        f"/v1/agents/{agent_id}/policy:validate",
        headers=headers,
        content=_POLICY,
    )
    assert valid.status_code == 200
    assert valid.json()["valid"] is True

    dry = test_client.post(
        f"/v1/agents/{agent_id}/policy:dry-run",
        headers=headers,
        json={"kind": "tool", "resource": "files.read"},
    )
    assert dry.status_code == 200
    assert dry.json()["effect"] in {"allow", "deny", "require_approval"}

    templates = test_client.get("/v1/policy-templates", headers=headers)
    assert templates.status_code == 200
    items = templates.json()["items"]
    assert {item["id"] for item in items} >= {
        "read-only",
        "support-with-approvals",
        "dev-permissive",
    }
    read_only = next(item for item in items if item["id"] == "read-only")
    assert "apiVersion: tessera/v1" in read_only["yaml"]
    assert "agent: AGENT" in read_only["yaml"]

    applied = test_client.post(
        f"/v1/agents/{agent_id}/policy:from-template",
        headers=headers,
        json={"template_id": "read-only"},
    )
    assert applied.status_code == 200

    keys = test_client.get("/v1/admin-keys", headers=headers)
    assert keys.status_code == 200
    created = test_client.post(
        "/v1/admin-keys",
        headers=headers,
        json={"name": "console"},
    )
    assert created.status_code == 200
    assert created.json()["token"].startswith("tsa_")
    key_id = created.json()["token"].split("_")[1]
    revoked = test_client.post(f"/v1/admin-keys/{key_id}:revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
