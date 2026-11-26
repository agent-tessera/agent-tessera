"""Policy storage, baselines, and signed bundles."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_tessera.bundle import decode_bundle
from agent_tessera.crypto import load_jwks
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Revocation
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
    max_steps_per_task: 10
"""

_POLICY_DEV = _POLICY.replace("billing", "other").replace("prod", "dev")
_POLICY_TIGHTER = _POLICY.replace("10", "11")
_BASELINE = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: prod-rules
spec:
  selector:
    environment: prod
  tools:
    deny: ["shell.*"]
"""
_OWNERS = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: owners
spec:
  selector:
    environment: prod
  require_owner: true
"""


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            token = create_admin_key(session, "ops")
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
    try:
        with TestClient(app) as client:
            yield client, {"Authorization": f"Bearer {token}"}, settings
    finally:
        downgrade_database(database_url)


def test_policies_baselines_and_bundles(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    billing = _agent(client, headers, "billing", "prod", "a@example.com")
    other = _agent(client, headers, "other", "dev", "b@example.com")
    assert _put_policy(client, headers, billing, _POLICY).status_code == 200
    assert _put_policy(client, headers, other, _POLICY_DEV).status_code == 200
    mismatch = _put_policy(client, headers, billing, _POLICY_DEV)
    assert mismatch.status_code == 422
    assert "must match" in mismatch.json()["error"]["message"]
    invalid = _put_policy(client, headers, billing, "[\n")
    assert invalid.status_code == 422
    assert "invalid YAML" in invalid.json()["error"]["message"]
    assert _policy_updates(settings.database_url) == []
    assert client.post(f"/v1/agents/{billing}:activate", headers=headers).status_code == 200
    assert client.post(f"/v1/agents/{other}:activate", headers=headers).status_code == 200
    changed = _put_policy(client, headers, billing, _POLICY_TIGHTER)
    assert changed.status_code == 200
    version = changed.json()["version"]
    assert _policy_updates(settings.database_url) == [billing]
    again = _put_policy(client, headers, billing, _POLICY_TIGHTER)
    assert again.json()["version"] == version
    assert _policy_updates(settings.database_url) == [billing]
    stored = client.get(f"/v1/agents/{billing}/policy", headers=headers)
    assert stored.status_code == 200
    assert "max_steps_per_task: 11" in stored.text
    baseline = client.put(
        "/v1/baselines/prod-rules",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_BASELINE,
    )
    assert baseline.status_code == 200
    assert client.get("/v1/baselines/prod-rules", headers=headers).text == _BASELINE
    assert _policy_updates(settings.database_url) == [billing, billing]
    owners = client.put(
        "/v1/baselines/owners",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_OWNERS,
    )
    assert owners.status_code == 422
    assert "owner required" in owners.json()["error"]["message"]
    assert client.get("/v1/baselines/owners", headers=headers).status_code == 404
    bundle = client.get(f"/v1/bundles/{billing}", headers=headers)
    assert bundle.status_code == 200
    assert bundle.headers["content-type"].startswith("application/jwt")
    etag = bundle.headers["etag"]
    jwks = load_jwks(client.get("/.well-known/jwks.json").json())
    claims = decode_bundle(bundle.text, jwks, issuer=settings.issuer)
    assert claims.agent_id == billing
    assert claims.agent == "billing"
    assert claims.version.startswith("sha256:")
    assert etag == f'"{claims.version}"'
    cached = client.get(f"/v1/bundles/{billing}", headers={**headers, "If-None-Match": etag})
    assert cached.status_code == 304
    foreign = _runtime_token(client, headers, other)
    denied = client.get(f"/v1/bundles/{billing}", headers={"Authorization": f"Bearer {foreign}"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_baseline_delete_and_auth(api: tuple[TestClient, dict[str, str], Settings]) -> None:
    client, headers, _settings = api
    billing = _agent(client, headers, "billing", "prod", "a@example.com")
    assert _put_policy(client, headers, billing, _POLICY).status_code == 200
    assert client.post(f"/v1/agents/{billing}:activate", headers=headers).status_code == 200
    stored = client.put(
        "/v1/baselines/prod-rules",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_BASELINE,
    )
    assert stored.status_code == 200
    listed = client.get("/v1/baselines", headers=headers)
    assert [item["name"] for item in listed.json()["items"]] == ["prod-rules"]
    assert client.delete("/v1/baselines/prod-rules", headers=headers).status_code == 204
    assert client.get("/v1/baselines/prod-rules", headers=headers).status_code == 404
    denied = client.put(
        f"/v1/agents/{billing}/policy",
        content=_POLICY,
        headers={"Content-Type": "application/yaml"},
    )
    assert denied.status_code == 401
    assert client.get(f"/v1/bundles/{billing}").status_code == 401


def test_bundle_grows_when_an_agent_intersects(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    orchestrator = _agent(client, headers, "orchestrator", "prod", "orch@example.com")
    worker = _agent(client, headers, "worker", "prod", "worker@example.com")
    plain_orchestrator = _agent_policy("orchestrator")
    plain_worker = _agent_policy("worker")
    assert _put_policy(client, headers, orchestrator, plain_orchestrator).status_code == 200
    assert _put_policy(client, headers, worker, plain_worker).status_code == 200
    assert client.post(f"/v1/agents/{worker}:activate", headers=headers).status_code == 200
    plain = _bundle(client, headers, settings, worker)
    assert plain.peers == {}
    plain_size = len(_bundle_text(client, headers, worker))

    intersect = _agent_policy(
        "worker",
        constraint="intersect",
        tools=("shell.exec", "zendesk.read"),
    )
    limited = _agent_policy("orchestrator", tools=("zendesk.read",))
    assert _put_policy(client, headers, worker, intersect).status_code == 200
    assert _put_policy(client, headers, orchestrator, limited).status_code == 200
    embedded = _bundle(client, headers, settings, worker)

    assert len(_bundle_text(client, headers, worker)) > plain_size
    assert embedded.peers["orchestrator"].tool == ("zendesk.read",)
    assert embedded.peers["worker"].tool == ("shell.exec", "zendesk.read")
    assert embedded.peers["orchestrator"].model == ()


def _agent_policy(
    name: str,
    *,
    constraint: str | None = None,
    tools: tuple[str, ...] = (),
) -> str:
    agents = ""
    if constraint is not None:
        agents = f"  agents:\n    caller_constraint: {constraint}\n"
    allow = ""
    if tools:
        listed = ", ".join(f'"{tool}"' for tool in tools)
        allow = f"  tools:\n    allow: [{listed}]\n"
    body = f"{allow}{agents}" or "  {}\n"
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {name}\n"
        "  environment: prod\n"
        "spec:\n"
        f"{body}"
    )


def _bundle(client: TestClient, headers: dict[str, str], settings: Settings, agent_id: str):
    text = _bundle_text(client, headers, agent_id)
    jwks = load_jwks(client.get("/.well-known/jwks.json").json())
    return decode_bundle(text, jwks, issuer=settings.issuer)


def _bundle_text(client: TestClient, headers: dict[str, str], agent_id: str) -> str:
    response = client.get(f"/v1/bundles/{agent_id}", headers=headers)
    assert response.status_code == 200
    return response.text


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
    return str(created.json()["id"])


def _put_policy(client: TestClient, headers: dict[str, str], agent_id: str, text: str):
    return client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=text,
    )


def _policy_updates(url: str) -> list[str]:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            query = select(Revocation).where(Revocation.kind == "policy_updated")
            rows = session.scalars(query.order_by(Revocation.id)).all()
            return [row.agent_id or "" for row in rows]
    finally:
        engine.dispose()


def _runtime_token(client: TestClient, headers: dict[str, str], agent_id: str) -> str:
    created = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={"reusable": False},
    )
    assert created.status_code == 200
    response = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": created.json()["token"],
            "instance_id": "cd" * 16,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])
