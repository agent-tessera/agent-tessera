"""Approval create, resolve, and escalation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.approvals import advance_approvals
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  approvals:
    route_to: [principal]
    timeout: 60
    forbid_self_approval: true
"""

_ACTION = {"kind": "tool", "resource": "files.delete"}
_DECISION = {"effect": "require_approval", "reason": "approval_rule"}
_Api = tuple[TestClient, dict[str, str], Settings, str, str]


@pytest.fixture
def api(database_url: str) -> Iterator[_Api]:
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
        with TestClient(app) as client:
            agent_id = _active_agent(
                client, headers, name="billing", email="ada@example.com", policy=_POLICY
            )
            token = _runtime_token(client, headers, agent_id, instance_id="ef" * 16)
            yield client, headers, settings, token, agent_id
    finally:
        downgrade_database(database_url)


def test_create_approve_deny_and_list(api: _Api) -> None:
    client, headers, _, token, agent_id = api
    created = _create(client, token, "ada")
    assert created["status"] == "pending"
    assert created["expires_at"].endswith("Z")
    approval_id = str(created["id"])
    fetched = client.get(
        f"/v1/approvals/{approval_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["route"]["parties"] == ["user:ada"]
    listed = client.get(
        "/v1/approvals",
        headers=headers,
        params={"status": "pending", "agent_id": agent_id},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [approval_id]
    approved = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers=headers,
        json={"comment": "ok"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["resolved_by"] == "ops"
    again = client.post(f"/v1/approvals/{approval_id}:approve", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"
    denied_id = str(_create(client, token, "ada")["id"])
    denied = client.post(f"/v1/approvals/{denied_id}:deny", headers=headers, json={"comment": "no"})
    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"
    empty = client.get(
        "/v1/approvals",
        headers=headers,
        params={"status": "pending", "agent_id": agent_id},
    )
    assert empty.json()["items"] == []


def test_forbid_self_approval(api: _Api) -> None:
    client, headers, settings, token, _ = api
    approval_id = str(_create(client, token, "ada")["id"])
    own = _admin(settings.database_url, "ada")
    rejected = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers={"Authorization": f"Bearer {own}"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "self_approval"
    allowed = client.post(f"/v1/approvals/{approval_id}:approve", headers=headers)
    assert allowed.status_code == 200


def test_escalation_then_timeout(api: _Api) -> None:
    client, headers, settings, _, _ = api
    policy = _policy("routed", ["user:lead@example.com", "user:boss@example.com"], forbid=True)
    agent_id = _active_agent(
        client, headers, name="routed", email="routed@example.com", policy=policy
    )
    token = _runtime_token(client, headers, agent_id, instance_id="ab" * 16)
    created = _create(client, token, "ada")
    approval_id = str(created["id"])
    moment = _when(str(created["expires_at"])) + timedelta(seconds=1)
    first = _advance(settings, moment)
    assert first == 1
    escalated = client.get(f"/v1/approvals/{approval_id}", headers=headers)
    assert escalated.status_code == 200
    assert escalated.json()["status"] == "pending"
    assert escalated.json()["route_index"] == 1
    later = _when(str(escalated.json()["expires_at"])) + timedelta(seconds=1)
    second = _advance(settings, later)
    assert second == 1
    finished = client.get(f"/v1/approvals/{approval_id}", headers=headers)
    assert finished.json()["status"] == "timeout"
    late = client.post(f"/v1/approvals/{approval_id}:deny", headers=headers)
    assert late.status_code == 409


def test_routed_agent_can_approve_and_the_requester_cannot(api: _Api) -> None:
    client, headers, _, _, _ = api
    reviewer = _active_agent(
        client,
        headers,
        name="reviewer",
        email="reviewer@example.com",
        policy=_policy("reviewer", ["principal"], forbid=True),
    )
    reviewer_token = _runtime_token(client, headers, reviewer, instance_id="cd" * 16)
    billing = _active_agent(
        client,
        headers,
        name="payer",
        email="payer@example.com",
        policy=_policy("payer", ["agent:reviewer"], forbid=True),
    )
    billing_token = _runtime_token(client, headers, billing, instance_id="12" * 16)
    approval_id = str(_create(client, billing_token, "ada")["id"])
    rejected = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers={"Authorization": f"Bearer {billing_token}"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "not_approver"
    approved = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    assert approved.status_code == 200
    assert approved.json()["resolved_by"] == "agent:reviewer"


def test_unresolved_route_is_unavailable(api: _Api) -> None:
    client, headers, _, _, _ = api
    agent_id = _active_agent(
        client,
        headers,
        name="alone",
        email="alone@example.com",
        policy=_policy("alone", ["supervisor"], forbid=True),
    )
    token = _runtime_token(client, headers, agent_id, instance_id="34" * 16)
    response = client.post(
        "/v1/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": _ACTION, "decision": _DECISION, "principal": "ada"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "approval_unavailable"


def test_list_approvals_pages_with_cursor(api: _Api) -> None:
    client, headers, _, token, agent_id = api
    for _ in range(60):
        _create(client, token, "ada")
    first = client.get(
        "/v1/approvals",
        headers=headers,
        params={"agent_id": agent_id},
    )
    assert first.status_code == 200
    page = first.json()
    assert len(page["items"]) == 50
    assert page["next_cursor"] == page["items"][-1]["id"]
    second = client.get(
        "/v1/approvals",
        headers=headers,
        params={"agent_id": agent_id, "cursor": page["next_cursor"]},
    )
    assert second.status_code == 200
    rest = second.json()
    assert len(rest["items"]) == 10
    assert rest["next_cursor"] is None
    first_ids = {item["id"] for item in page["items"]}
    second_ids = {item["id"] for item in rest["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_api_approves_a_pending_request(api: _Api) -> None:
    client, headers, _, token, _ = api
    created = _create(client, token, "ada")
    approval_id = str(created["id"])
    posted = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers=headers,
        json={"comment": "ok"},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "approved"
    detail = client.get(f"/v1/approvals/{approval_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "approved"


def test_cookie_session_reads_and_approves(api: _Api) -> None:
    """Console cookie+CSRF can GET and approve without a bearer header."""
    client, headers, _, token, _ = api
    created = _create(client, token, "ada")
    approval_id = str(created["id"])
    secret = headers["Authorization"].removeprefix("Bearer ")
    login = client.post("/login", data={"admin_key": secret}, follow_redirects=False)
    assert login.status_code == 303
    me = client.get("/v1/me")
    assert me.status_code == 200
    csrf = me.json()["csrf"]
    detail = client.get(f"/v1/approvals/{approval_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "pending"
    approved = client.post(
        f"/v1/approvals/{approval_id}:approve",
        headers={"X-CSRF-Token": csrf},
        json={"comment": "console"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["resolved_by"] == "ops"


def test_owner_and_supervisor_resolve(api: _Api) -> None:
    client, headers, settings, _, _ = api
    agent_id = _active_agent(
        client,
        headers,
        name="owned",
        email="owner@example.com",
        policy=_policy("owned", ["supervisor", "owner"], forbid=False),
        supervisors=["lead@example.com"],
    )
    token = _runtime_token(client, headers, agent_id, instance_id="56" * 16)
    created = _create(client, token, "lead@example.com")
    fetched = client.get(f"/v1/approvals/{created['id']}", headers=headers)
    parties = ["user:lead@example.com", "user:owner@example.com"]
    assert fetched.json()["route"]["parties"] == parties
    own = _admin(settings.database_url, "lead@example.com")
    approved = client.post(
        f"/v1/approvals/{created['id']}:approve",
        headers={"Authorization": f"Bearer {own}"},
    )
    assert approved.status_code == 200


def _create(client: TestClient, token: str, principal: str) -> dict[str, object]:
    response = client.post(
        "/v1/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": _ACTION, "decision": _DECISION, "principal": principal, "run_id": "run-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _advance(settings: Settings, moment: datetime) -> int:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            return advance_approvals(session, settings, now=lambda: moment)
    finally:
        engine.dispose()


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _admin(url: str, name: str) -> str:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            return create_admin_key(session, name)
    finally:
        engine.dispose()


def _policy(agent: str, route: list[str], *, forbid: bool) -> str:
    listed = ", ".join(route)
    flag = "true" if forbid else "false"
    return f"""\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {agent}
  environment: prod
spec:
  approvals:
    route_to: [{listed}]
    timeout: 60
    forbid_self_approval: {flag}
"""


def _active_agent(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    email: str,
    policy: str,
    supervisors: list[str] | None = None,
) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": email, "display_name": name},
    )
    assert owner.status_code == 200, owner.text
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": name,
            "environment": "prod",
            "owner_id": owner.json()["id"],
            "supervisors": supervisors or [],
        },
    )
    assert created.status_code == 200, created.text
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=policy,
    )
    assert stored.status_code == 200, stored.text
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _runtime_token(
    client: TestClient, headers: dict[str, str], agent_id: str, *, instance_id: str
) -> str:
    created = client.post(f"/v1/agents/{agent_id}/registration-tokens", headers=headers, json={})
    assert created.status_code == 200, created.text
    response = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": created.json()["token"],
            "instance_id": instance_id,
            "host": "app",
            "runtime": {"sdk": "0.3.0"},
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])
