"""Baseline inheritors, validate, and dry-run preview."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.services.secrets import create_admin_key


def _baseline(name: str, environment: str | None = "prod") -> str:
    if environment is None:
        selector = "  selector: {}\n"
    else:
        selector = f"  selector:\n    environment: {environment}\n"
    return (
        "apiVersion: tessera/v1\n"
        "kind: BaselinePolicy\n"
        "metadata:\n"
        f"  name: {name}\n"
        "spec:\n"
        f"{selector}"
    )


def _agent_policy(name: str, environment: str) -> str:
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {name}\n"
        f"  environment: {environment}\n"
    )


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], str]]:
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
        with TestClient(app) as client:
            with app.state.sessions() as session:
                admin = create_admin_key(session, "ops")
            headers = {"Authorization": f"Bearer {admin}"}
            owner = client.post(
                "/v1/owners",
                headers=headers,
                json={"type": "user", "email": "b@example.com", "display_name": "Base"},
            )
            assert owner.status_code == 200
            agent = client.post(
                "/v1/agents",
                headers=headers,
                json={
                    "name": "prod-bot",
                    "environment": "prod",
                    "owner_id": owner.json()["id"],
                },
            )
            assert agent.status_code == 200
            agent_id = str(agent.json()["id"])
            assert (
                client.put(
                    f"/v1/agents/{agent_id}/policy",
                    headers={**headers, "Content-Type": "application/yaml"},
                    content=_agent_policy("prod-bot", "prod"),
                ).status_code
                == 200
            )
            stored = client.put(
                "/v1/baselines/org-core",
                headers={**headers, "Content-Type": "application/yaml"},
                content=_baseline("org-core", "prod"),
            )
            assert stored.status_code == 200
            yield client, headers, agent_id
    finally:
        downgrade_database(database_url)


def test_baseline_agents_validate_and_dry_run(
    api: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, agent_id = api
    agents = client.get("/v1/baselines/org-core/agents", headers=headers)
    assert agents.status_code == 200
    items = agents.json()["items"]
    assert any(item["id"] == agent_id for item in items)

    ok = client.post(
        "/v1/baselines/org-core:validate",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_baseline("org-core", "prod"),
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    bad = client.post(
        "/v1/baselines/org-core:validate",
        headers={**headers, "Content-Type": "application/yaml"},
        content="not: yaml: [[",
    )
    assert bad.status_code == 422

    preview = client.post(
        "/v1/baselines/org-core:dry-run",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_baseline("org-core", None),
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["matching_agents"] >= 1
    assert any(item["agent_id"] == agent_id and item["ok"] for item in body["items"])

    before = client.get("/v1/baselines/org-core", headers=headers)
    assert before.status_code == 200
    assert "environment: prod" in before.text
