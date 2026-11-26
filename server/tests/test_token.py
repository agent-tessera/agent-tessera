"""Registration and refresh grants."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Instance, IssuedToken, Policy, RegistrationToken
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.keys import issue_runtime_token, verify_runtime_token
from tessera_server.services.secrets import create_admin_key

_INSTANCE = "ab" * 16


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


def test_registration_and_refresh(api: tuple[TestClient, dict[str, str], Settings]) -> None:
    client, headers, settings = api
    agent_id = _active_agent(client, headers, settings.database_url)
    one_time = _registration_token(client, headers, agent_id, reusable=False)
    first = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": one_time,
            "instance_id": _INSTANCE,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["token_type"] == "Bearer"  # noqa: S105
    assert body["expires_in"] == settings.token_ttl
    assert body["agent"] == "billing"
    assert body["environment"] == "prod"
    claims = _claims(settings, str(body["access_token"]))
    assert claims["sub"] == "tessera://default/prod/billing"
    assert claims["aud"] == "tessera"
    assert claims["act"] is None
    assert claims["inst"] == _INSTANCE
    assert claims["agent_id"] == agent_id
    assert claims["pver"] == "sha256:" + "ab" * 32
    reused = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": one_time,
            "instance_id": _INSTANCE,
            "host": "app",
        },
    )
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "invalid_token"
    refreshed = client.post(
        "/v1/token",
        headers={"Authorization": f"Bearer {body['access_token']}"},
        json={"grant_type": "refresh"},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["agent_id"] == agent_id
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            assert session.scalars(select(IssuedToken)).all()
            instance = session.get(Instance, _INSTANCE)
            assert instance is not None
            assert instance.host == "app"
    finally:
        engine.dispose()


def test_reusable_token_until_it_expires(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _active_agent(client, headers, settings.database_url)
    token = _registration_token(client, headers, agent_id, reusable=True)
    assert _register(client, token, "cd" * 16).status_code == 200
    assert _register(client, token, "ef" * 16).status_code == 200
    _expire(settings.database_url, token)
    assert _register(client, token, "11" * 16).status_code == 401


@pytest.mark.parametrize("status", ["draft", "suspended", "retired"])
def test_inactive_agents_are_forbidden(
    api: tuple[TestClient, dict[str, str], Settings], status: str
) -> None:
    client, headers, settings = api
    agent_id = _agent_with_status(client, headers, settings.database_url, status)
    token = _registration_token(client, headers, agent_id, reusable=False)
    response = _register(client, token, _INSTANCE)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == f"agent_{status}"


def test_revoked_instance_and_expired_refresh(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _active_agent(client, headers, settings.database_url)
    token = _registration_token(client, headers, agent_id, reusable=True)
    first = _register(client, token, _INSTANCE)
    assert first.status_code == 200
    _revoke(settings.database_url, _INSTANCE)
    blocked = _register(client, token, _INSTANCE)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "instance_revoked"
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            agent = session.get(Agent, agent_id)
            assert agent is not None
            stale = issue_runtime_token(
                session,
                settings,
                agent_id=agent.id,
                name=agent.name,
                environment=agent.environment,
                instance_id=_INSTANCE,
                policy_version=None,
                now=lambda: datetime(2020, 1, 1, tzinfo=UTC),
            )
    finally:
        engine.dispose()
    expired = client.post(
        "/v1/token",
        headers={"Authorization": f"Bearer {stale}"},
        json={"grant_type": "refresh"},
    )
    assert expired.status_code == 401
    missing = client.post("/v1/token", json={"grant_type": "refresh"})
    assert missing.status_code == 401
    unknown = _register(client, "tsr_missing_secret", "22" * 16)
    assert unknown.status_code == 401


def _active_agent(client: TestClient, headers: dict[str, str], url: str) -> str:
    return _agent_with_status(client, headers, url, "active")


def _agent_with_status(client: TestClient, headers: dict[str, str], url: str, status: str) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": f"{status}@example.com", "display_name": status},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    if status == "draft":
        return agent_id
    _link_policy(url, agent_id)
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    if status == "suspended":
        assert client.post(f"/v1/agents/{agent_id}:suspend", headers=headers).status_code == 200
    if status == "retired":
        assert client.post(f"/v1/agents/{agent_id}:retire", headers=headers).status_code == 200
    return agent_id


def _registration_token(
    client: TestClient, headers: dict[str, str], agent_id: str, *, reusable: bool
) -> str:
    body: dict[str, object] = {"reusable": reusable}
    if reusable:
        body["expires_in"] = 3600
    response = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json=body,
    )
    assert response.status_code == 200
    return str(response.json()["token"])


def _register(client: TestClient, token: str, instance_id: str) -> Response:
    return client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": token,
            "instance_id": instance_id,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )


def _claims(settings: Settings, token: str) -> dict[str, object]:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            return dict(verify_runtime_token(session, settings, token))
    finally:
        engine.dispose()


def _link_policy(url: str, agent_id: str) -> None:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            agent = session.get(Agent, agent_id)
            assert agent is not None
            policy = Policy(
                id=new_ulid(),
                agent_id=agent_id,
                yaml="name: billing\n",
                sha256="sha256:" + "ab" * 32,
                author="test",
            )
            session.add(policy)
            session.flush()
            agent.policy_id = policy.id
            session.commit()
    finally:
        engine.dispose()


def _expire(url: str, token: str) -> None:
    token_id = token.split("_", 2)[1]
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            row = session.get(RegistrationToken, token_id)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
            session.commit()
    finally:
        engine.dispose()


def _revoke(url: str, instance_id: str) -> None:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            row = session.get(Instance, instance_id)
            assert row is not None
            row.revoked = True
            session.commit()
    finally:
        engine.dispose()
