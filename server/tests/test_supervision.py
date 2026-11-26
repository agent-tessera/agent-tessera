"""Instance and run supervisor storage."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_tessera.bundle import decode_bundle
from agent_tessera.crypto import load_jwks
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Instance, Revocation
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key
from tessera_server.services.supervision import advance_dead_men, resolved_supervisor

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
"""

_INSTANCE = "cd" * 16
_GRANTS = """\
apiVersion: tessera/v1
kind: SupervisorGrants
metadata:
  supervisor: user:ada
spec:
  models:
    allow: ["openai/gpt-4o"]
  tools:
    allow: ["files.read"]
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


def test_run_supervisor_beats_instance_and_static(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers)
    registered = _register(client, headers, agent_id, bound_supervisor="user:instance")
    assert registered.status_code == 200
    token = str(registered.json()["access_token"])
    _assert_resolved(settings, agent_id, "user:instance")
    first = _event(client, token, "aa" * 16, "run-1", "user:run")
    assert first.status_code == 200
    _assert_resolved(settings, agent_id, "user:run")
    second = _event(client, token, "bb" * 16, "run-1", "user:later")
    assert second.status_code == 200
    _assert_resolved(settings, agent_id, "user:run")
    rejected = _register(client, headers, agent_id, bound_supervisor="ada")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_request"


def test_grants_are_stored_and_embedded_when_an_agent_caps(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers)
    missing = client.get("/v1/supervisors/user:ada/grants", headers=headers)
    assert missing.status_code == 404
    capped = _capped_policy()
    assert _put_policy(client, headers, agent_id, capped).status_code == 200
    before = _bundle(client, headers, settings, agent_id)
    assert before.supervisor_grants == {}
    stored = client.put(
        "/v1/supervisors/user:ada/grants",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_GRANTS,
    )
    assert stored.status_code == 200
    fetched = client.get("/v1/supervisors/user:ada/grants", headers=headers)
    assert fetched.status_code == 200
    assert "files.read" in fetched.text
    embedded = _bundle(client, headers, settings, agent_id)
    assert embedded.supervisor_grants["user:ada"].tool == ("files.read",)
    assert embedded.supervisor_grants["user:ada"].model == ("openai/gpt-4o",)
    mismatch = client.put(
        "/v1/supervisors/user:ada/grants",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_GRANTS.replace("user:ada", "user:other"),
    )
    assert mismatch.status_code == 422
    assert client.put("/v1/supervisors/user:ada/grants", content=_GRANTS).status_code == 401


def test_overdue_acknowledgement_suspends_and_ack_resets_the_timer(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers)
    assert _put_policy(client, headers, agent_id, _dead_man_policy()).status_code == 200
    moment = datetime.now(UTC)
    _age(settings, agent_id, moment - timedelta(hours=2))
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            assert advance_dead_men(session, settings) == 1
    finally:
        engine.dispose()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = session.get(Agent, agent_id)
            assert row is not None
            assert row.status == "suspended"
            kinds = session.scalars(select(Revocation.kind)).all()
            assert "agent_suspended" in kinds
    finally:
        engine.dispose()
    fresh = _agent(
        client,
        headers,
        name="runner",
        email="runner@example.com",
        policy=_dead_man_policy("runner"),
    )
    acked = client.post(f"/v1/agents/{fresh}/supervisor-ack", headers=headers)
    assert acked.status_code == 200
    _age(settings, fresh, moment - timedelta(hours=2), keep_ack=True)
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            assert advance_dead_men(session, settings) == 0
    finally:
        engine.dispose()


def _assert_resolved(settings: Settings, agent_id: str, expected: str) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            instance = session.get(Instance, _INSTANCE)
            assert instance is not None
            assert instance.bound_supervisor == "user:instance"
            chosen = resolved_supervisor(
                session,
                agent_id=agent_id,
                instance_id=_INSTANCE,
                run_id="run-1",
            )
            assert chosen == expected
    finally:
        engine.dispose()


def _agent(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "billing",
    email: str = "ada@example.com",
    policy: str = _POLICY,
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
        json={
            "name": name,
            "environment": "prod",
            "owner_id": owner.json()["id"],
            "supervisors": ["user:static"],
        },
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=policy,
    )
    assert stored.status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _register(
    client: TestClient,
    headers: dict[str, str],
    agent_id: str,
    *,
    bound_supervisor: str,
) -> httpx.Response:
    minted = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={"reusable": True, "expires_in": 3600},
    )
    assert minted.status_code == 200
    return client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": minted.json()["token"],
            "instance_id": _INSTANCE,
            "host": "app",
            "runtime": {"sdk": "0.4.0"},
            "bound_supervisor": bound_supervisor,
        },
    )


def _dead_man_policy(agent: str = "billing") -> str:
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {agent}\n"
        "  environment: prod\n"
        "  autonomy: autonomous\n"
        "spec:\n"
        "  tools:\n"
        "    allow: ['files.read']\n"
        "  supervision:\n"
        "    max_unacknowledged: 1h\n"
    )


def _age(
    settings: Settings,
    agent_id: str,
    created_at: datetime,
    *,
    keep_ack: bool = False,
) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = session.get(Agent, agent_id)
            assert row is not None
            row.created_at = created_at
            if not keep_ack:
                row.supervisor_ack_at = None
            session.commit()
    finally:
        engine.dispose()


def _capped_policy() -> str:
    return """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["files.read", "shell.exec"]
  supervision:
    cap_to_supervisor: true
"""


def _put_policy(
    client: TestClient, headers: dict[str, str], agent_id: str, text: str
) -> httpx.Response:
    return client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=text,
    )


def _bundle(client: TestClient, headers: dict[str, str], settings: Settings, agent_id: str):
    response = client.get(f"/v1/bundles/{agent_id}", headers=headers)
    assert response.status_code == 200
    jwks = load_jwks(client.get("/.well-known/jwks.json").json())
    return decode_bundle(response.text, jwks, issuer=settings.issuer)


def _event(
    client: TestClient, token: str, event_id: str, task: str, supervisor: str
) -> httpx.Response:
    return client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "events": [
                {
                    "id": event_id,
                    "ts": "2026-11-07T12:00:00.000Z",
                    "type": "decision",
                    "task": task,
                    "supervisor": supervisor,
                    "effect": "allow",
                }
            ]
        },
    )
