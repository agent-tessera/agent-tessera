"""Run principal binding and environment filter."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.services.secrets import create_admin_key

_INST = "ab" * 16


def _policy(name: str, environment: str) -> str:
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
                json={
                    "type": "user",
                    "email": "p@example.com",
                    "display_name": "Principal",
                },
            )
            assert owner.status_code == 200
            created = client.post(
                "/v1/agents",
                headers=headers,
                json={
                    "name": "prod-agent",
                    "environment": "prod",
                    "owner_id": owner.json()["id"],
                },
            )
            assert created.status_code == 200
            agent_id = str(created.json()["id"])
            assert (
                client.put(
                    f"/v1/agents/{agent_id}/policy",
                    headers={**headers, "Content-Type": "application/yaml"},
                    content=_policy("prod-agent", "prod"),
                ).status_code
                == 200
            )
            activated = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
            assert activated.status_code == 200
            reg = client.post(
                f"/v1/agents/{agent_id}/registration-tokens",
                headers=headers,
                json={},
            )
            assert reg.status_code == 200
            token = client.post(
                "/v1/token",
                json={
                    "grant_type": "registration",
                    "registration_token": reg.json()["token"],
                    "instance_id": _INST,
                    "host": "app",
                    "runtime": {"sdk": "0.1.0"},
                },
            )
            assert token.status_code == 200
            yield client, headers, str(token.json()["access_token"])
    finally:
        downgrade_database(database_url)


def test_principal_binds_once_and_environment_filters(
    api: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, runtime = api
    first = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {runtime}"},
        json={
            "events": [
                {
                    "id": "aa" * 16,
                    "type": "decision",
                    "ts": "2026-01-01T00:00:00.000Z",
                    "task": "run-principal",
                    "principal": {"user": "user:alice", "groups": []},
                    "action": {"kind": "tool", "resource": "files.read"},
                    "effect": "allow",
                    "reason": "allow",
                    "rule": "agent:tools.allow[0]",
                    "policy_version": "sha256:x",
                    "span_id": "11" * 16,
                }
            ]
        },
    )
    assert first.status_code == 200
    listed = client.get("/v1/runs", headers=headers)
    assert listed.status_code == 200
    row = next(item for item in listed.json()["items"] if item["id"] == "run-principal")
    assert row["principal"] == "user:alice"

    second = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {runtime}"},
        json={
            "events": [
                {
                    "id": "bb" * 16,
                    "type": "decision",
                    "ts": "2026-01-01T00:01:00.000Z",
                    "task": "run-principal",
                    "principal": {"user": "user:bob", "groups": []},
                    "action": {"kind": "tool", "resource": "files.read"},
                    "effect": "allow",
                    "reason": "allow",
                    "rule": "agent:tools.allow[0]",
                    "policy_version": "sha256:x",
                    "span_id": "22" * 16,
                }
            ]
        },
    )
    assert second.status_code == 200
    detail = client.get("/v1/runs/run-principal", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["principal"] == "user:alice"

    prod = client.get("/v1/runs", headers=headers, params={"environment": "prod"})
    assert prod.status_code == 200
    assert any(item["id"] == "run-principal" for item in prod.json()["items"])
    staging = client.get("/v1/runs", headers=headers, params={"environment": "staging"})
    assert staging.status_code == 200
    assert staging.json()["items"] == []

    trace = client.get("/v1/runs/run-principal/trace", headers=headers)
    assert trace.status_code == 200
    spans = trace.json()["spans"]
    assert any(span.get("principal") == "user:alice" for span in spans)
