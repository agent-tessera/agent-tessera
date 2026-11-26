"""Run revocation and the revocation stream."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.services.secrets import create_admin_key

_BILLING = "ab" * 16
_SIBLING = "cd" * 16
_OTHER = "ef" * 16


def _policy(name: str, environment: str, steps: int | None = None) -> str:
    limits = "" if steps is None else f"spec:\n  limits:\n    max_steps_per_task: {steps}\n"
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {name}\n"
        f"  environment: {environment}\n"
        f"{limits}"
    )


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], str, str, str, str]]:
    upgrade_database(database_url)
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
    )
    app = create_app(settings)
    app.state.revocation_poll = 0.01
    app.state.revocation_keepalive = 0.05
    try:
        with TestClient(app) as client:
            with app.state.sessions() as session:
                admin = create_admin_key(session, "ops")
            headers = {"Authorization": f"Bearer {admin}"}
            billing = _agent(client, headers, "billing", "prod", "a@example.com")
            other = _agent(client, headers, "other", "dev", "b@example.com")
            billing_token = _token(client, headers, billing, _BILLING)
            _token(client, headers, billing, _SIBLING)
            other_token = _token(client, headers, other, _OTHER)
            _event(client, billing_token, "11" * 16, "run-billing")
            _event(client, other_token, "22" * 16, "run-other")
            updated = client.put(
                f"/v1/agents/{billing}/policy",
                headers={**headers, "Content-Type": "application/yaml"},
                content=_policy("billing", "prod", 4),
            )
            assert updated.status_code == 200
            revoked = client.post(f"/v1/instances/{_SIBLING}:revoke", headers=headers)
            assert revoked.status_code == 200
            assert client.post(f"/v1/agents/{billing}:suspend", headers=headers).status_code == 200
            assert client.post(f"/v1/agents/{other}:suspend", headers=headers).status_code == 200
            yield client, headers, billing, other, billing_token, other_token
    finally:
        downgrade_database(database_url)


def test_revoke_run_lists_participants(
    api: tuple[TestClient, dict[str, str], str, str, str, str],
) -> None:
    client, headers, _billing, _other, _token, _other_token = api
    missing = client.post("/v1/runs/missing:revoke", headers=headers)
    assert missing.status_code == 404
    revoked = client.post(
        "/v1/runs/run-billing:revoke",
        headers=headers,
        json={"reason": "operator stop"},
    )
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["status"] == "revoked"
    assert body["revoked_by"].startswith("admin:")
    assert body["event_count"] == 1
    assert body["participants"] == [{"instance_id": _BILLING, "agent_id": _billing}]
    audit = client.get("/v1/audit/log", headers=headers)
    assert audit.status_code == 200
    revoked_rows = [
        item
        for item in audit.json()["items"]
        if item["type"] == "run_revoked" and item["payload"].get("id") == "run-billing"
    ]
    assert revoked_rows
    assert revoked_rows[-1]["payload"]["reason"] == "operator stop"
    again = client.post("/v1/runs/run-billing:revoke", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"
    listed = client.get("/v1/runs", headers=headers)
    assert {item["id"] for item in listed.json()["items"]} == {"run-billing", "run-other"}
    one = client.get("/v1/runs/run-other", headers=headers)
    assert one.json()["status"] == "running"
    assert client.get("/v1/runs/missing", headers=headers).status_code == 404


def test_stream_is_scoped_and_resumable(
    api: tuple[TestClient, dict[str, str], str, str, str, str],
) -> None:
    client, headers, billing, other, billing_token, other_token = api
    assert client.post("/v1/runs/run-billing:revoke", headers=headers).status_code == 200
    assert client.post("/v1/runs/run-other:revoke", headers=headers).status_code == 200
    seen = _events(client, billing_token, "0")
    assert seen["run_revoked"] == {"run-billing", "run-other"}
    assert seen["agent_suspended"] == {billing}
    assert seen["agent_activated"] == {billing}
    assert seen["policy_updated"] == {billing}
    assert "instance_revoked" not in seen
    foreign = _events(client, other_token, "0")
    assert foreign["agent_suspended"] == {other}
    assert billing not in foreign["agent_suspended"]
    first = _events(client, billing_token, "0")
    resumed = _events(client, billing_token, _cursor_after_activate(client, billing_token))
    assert "agent_activated" not in resumed
    assert first["run_revoked"] == resumed["run_revoked"]
    keepalive = _lines(client, billing_token, None)
    assert ": keepalive" in keepalive
    assert client.get("/v1/revocations/stream", headers=headers).status_code == 401
    bad = client.get(
        "/v1/revocations/stream",
        headers={"Authorization": f"Bearer {billing_token}", "Last-Event-ID": "soon"},
    )
    assert bad.status_code == 422


def _events(client: TestClient, token: str, last_id: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    event = ""
    for line in _lines(client, token, last_id):
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: ") and event:
            payload = json.loads(line.removeprefix("data: "))
            found.setdefault(event, set()).add(str(payload["subject"]))
            event = ""
            if "run-other" in found.get("run_revoked", set()):
                break
    return found


def _cursor_after_activate(client: TestClient, token: str) -> str:
    event = ""
    cursor = "0"
    for line in _lines(client, token, "0"):
        if line.startswith("id: "):
            cursor = line.removeprefix("id: ")
        elif line.startswith("event: "):
            event = line.removeprefix("event: ")
            if event == "agent_activated":
                return cursor
    return cursor


def _lines(client: TestClient, token: str, last_id: str | None) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"}
    if last_id is not None:
        headers["Last-Event-ID"] = last_id
    lines: list[str] = []
    with client.stream("GET", "/v1/revocations/stream", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            lines.append(line)
            if line == ": keepalive" or (
                last_id is not None and "run-other" in line and line.startswith("data: ")
            ):
                break
    return lines


def _agent(
    client: TestClient, headers: dict[str, str], name: str, environment: str, email: str
) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": email, "display_name": name},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": name, "environment": environment, "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_policy(name, environment),
    )
    assert stored.status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _token(client: TestClient, headers: dict[str, str], agent_id: str, instance_id: str) -> str:
    created = client.post(f"/v1/agents/{agent_id}/registration-tokens", headers=headers, json={})
    assert created.status_code == 200
    response = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": created.json()["token"],
            "instance_id": instance_id,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def _event(client: TestClient, token: str, event_id: str, task: str) -> None:
    response = client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "events": [
                {"id": event_id, "ts": "2026-11-05T16:00:00.000Z", "type": "usage", "task": task}
            ]
        },
    )
    assert response.status_code == 200
