"""Run effect ledger storage and the admin page."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
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
                json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
            )
            assert owner.status_code == 200, owner.text
            created = client.post(
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
            stored = client.put(
                f"/v1/agents/{agent_id}/policy",
                headers={**headers, "Content-Type": "application/yaml"},
                content=_POLICY,
            )
            assert stored.status_code == 200, stored.text
            activated = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
            assert activated.status_code == 200
            token_row = client.post(
                f"/v1/agents/{agent_id}/registration-tokens",
                headers=headers,
                json={},
            )
            assert token_row.status_code == 200, token_row.text
            runtime = client.post(
                "/v1/token",
                json={
                    "grant_type": "registration",
                    "registration_token": token_row.json()["token"],
                    "instance_id": "ab" * 16,
                    "host": "app",
                    "runtime": {"sdk": "0.3.0"},
                },
            )
            assert runtime.status_code == 200, runtime.text
            yield client, headers, str(runtime.json()["access_token"])
    finally:
        downgrade_database(database_url)


def test_effect_ledger_lists_refs_only(api: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, token = api
    posted = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "events": [
                {
                    "id": "cd" * 16,
                    "ts": "2026-11-06T16:00:00.000Z",
                    "type": "effect",
                    "task": "run-1",
                    "ref": "zendesk:ticket/123",
                    "reversible": True,
                    "undo": "zendesk.reopen",
                    "content": "secret ticket body",
                }
            ]
        },
    )
    assert posted.status_code == 200, posted.text
    listed = client.get("/v1/runs/run-1/effects", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["items"] == [
        {
            "ref": "zendesk:ticket/123",
            "reversible": True,
            "undo": "zendesk.reopen",
            "agent_id": body["items"][0]["agent_id"],
        }
    ]
    assert "content" not in body["items"][0]
    assert "secret" not in listed.text
    missing = client.get("/v1/runs/missing/effects", headers=headers)
    assert missing.status_code == 404


def test_known_model_energy_is_numeric(api: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, token = api
    posted = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "events": [
                {
                    "id": "66" * 16,
                    "ts": "2026-11-07T12:00:00.000Z",
                    "type": "usage",
                    "model": "openai/gpt-4o",
                    "input_tokens": 1000,
                    "output_tokens": 0,
                    "cost_usd": "0",
                }
            ]
        },
    )
    assert posted.status_code == 200
    agent_id = client.get("/v1/agents", headers=headers).json()["items"][0]["id"]
    listed = client.get(
        "/v1/footprint",
        headers=headers,
        params={
            "agent_id": agent_id,
            "from": "2026-11-07T00:00:00Z",
            "to": "2026-11-07T23:00:00Z",
        },
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["energy_j"] == 1000
    assert body["co2e_g"] == round(1000 / 3_600_000 * 400, 6)
    assert body["estimate"] is True


def test_footprint_matches_seeded_events(api: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, token = api
    posted = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "events": [
                {
                    "id": "11" * 16,
                    "ts": "2026-11-06T12:00:00.000Z",
                    "type": "usage",
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "cost_usd": "0.25",
                },
                {
                    "id": "22" * 16,
                    "ts": "2026-11-06T12:01:00.000Z",
                    "type": "call",
                    "kind": "model",
                },
                {
                    "id": "33" * 16,
                    "ts": "2026-11-06T12:02:00.000Z",
                    "type": "call",
                    "kind": "tool",
                },
                {
                    "id": "44" * 16,
                    "ts": "2026-11-06T12:03:00.000Z",
                    "type": "decision",
                    "effect": "deny",
                    "reason": "not_granted",
                },
                {
                    "id": "55" * 16,
                    "ts": "2026-11-06T18:00:00.000Z",
                    "type": "usage",
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cost_usd": "1",
                },
            ]
        },
    )
    assert posted.status_code == 200, posted.text
    agent_id = client.get("/v1/agents", headers=headers).json()["items"][0]["id"]
    listed = client.get(
        "/v1/footprint",
        headers=headers,
        params={"agent_id": agent_id, "from": "2026-11-06T00:00:00Z", "to": "2026-11-06T13:00:00Z"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == {
        "tokens_in": 10,
        "tokens_out": 4,
        "cost_usd": 0.25,
        "model_calls": 1,
        "tool_calls": 1,
        "denials": 1,
        "energy_j": None,
        "co2e_g": None,
        "estimate": True,
    }
    agent = client.get(f"/v1/agents/{agent_id}", headers=headers)
    assert agent.status_code == 200
    assert agent.json()["name"] == "billing"
