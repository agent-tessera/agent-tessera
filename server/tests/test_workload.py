"""Workload-identity grant with a mocked provider."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from jwt.algorithms import OKPAlgorithm

import tessera_server.services.workload as workload_api
from tessera_server.app import create_app
from tessera_server.config import Settings, WorkloadBinding
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Policy
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.secrets import create_admin_key

_ISSUER = "https://idp.example"
_KUBERNETES = "https://kubernetes.default.svc"
_AUDIENCE = "workload"
_INSTANCE = "cd" * 16
_PRIVATE = Ed25519PrivateKey.generate()
_JWK = json.loads(OKPAlgorithm.to_jwk(_PRIVATE.public_key()))
_JWK["kid"] = "idp"


@pytest.fixture
def workload(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    monkeypatch.setattr(workload_api, "open_client", lambda: _client(_JWK))
    binding = WorkloadBinding(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        agent="billing",
        environment="prod",
    )
    kubernetes = WorkloadBinding(
        issuer=_KUBERNETES,
        audience="tessera",
        agent="billing",
        environment="prod",
        claim="sub",
    )
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        kek=b"k" * 32,
        dev=False,
        workload_bindings=(binding, kubernetes),
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            yield client, {"Authorization": f"Bearer {admin}"}, settings
    finally:
        downgrade_database(database_url)


def test_workload_grant_issues_a_runtime_jwt(
    workload: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = workload
    agent_id = _active_agent(client, headers, settings.database_url)
    issued = client.post("/v1/token", json=_body(_assertion(_AUDIENCE)))
    assert issued.status_code == 200
    body = issued.json()
    assert body["agent"] == "billing"
    assert body["environment"] == "prod"
    claims = _runtime_claims(settings, str(body["access_token"]))
    assert claims["sub"] == "tessera://default/prod/billing"
    assert claims["agent_id"] == agent_id
    assert claims["inst"] == _INSTANCE


def test_kubernetes_service_account_maps_to_the_agent(
    workload: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = workload
    agent_id = _active_agent(client, headers, settings.database_url)
    assertion = _assertion("tessera", issuer=_KUBERNETES, sub="system:serviceaccount:prod:billing")
    issued = client.post("/v1/token", json=_body(assertion))
    assert issued.status_code == 200
    claims = _runtime_claims(settings, str(issued.json()["access_token"]))
    assert claims["agent_id"] == agent_id
    assert claims["sub"] == "tessera://default/prod/billing"


def test_workload_grant_rejects_a_bad_audience(
    workload: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = workload
    _active_agent(client, headers, settings.database_url)
    rejected = client.post("/v1/token", json=_body(_assertion("other")))
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "invalid_token"


def test_workload_bindings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_SERVER_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("TESSERA_SERVER_ISSUER", "http://example.test")
    monkeypatch.setenv("TESSERA_SERVER_KEK", base64.b64encode(b"k" * 32).decode())
    document = [
        {
            "issuer": f"{_ISSUER}/",
            "audience": _AUDIENCE,
            "agent": "billing",
            "environment": "prod",
            "claim": "sub",
        }
    ]
    monkeypatch.setenv("TESSERA_WORKLOAD_BINDINGS", json.dumps(document))
    settings = Settings.from_env()
    assert settings.workload_bindings[0].issuer == _ISSUER
    assert settings.workload_bindings[0].claim == "sub"


def _body(assertion: str) -> dict[str, object]:
    return {
        "grant_type": "workload",
        "assertion": assertion,
        "agent": "billing",
        "environment": "prod",
        "instance_id": _INSTANCE,
        "runtime": {},
    }


def _assertion(audience: str, issuer: str = _ISSUER, sub: str = "system:sa") -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": issuer,
        "sub": sub,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(claims, _PRIVATE, algorithm="EdDSA", headers={"kid": "idp"})
    return token if isinstance(token, str) else token.decode("ascii")


def _client(jwk: dict[str, object]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        issuer = f"{request.url.scheme}://{request.url.host}"
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={"issuer": issuer, "jwks_uri": f"{issuer}/jwks"},
            )
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _active_agent(client: TestClient, headers: dict[str, str], url: str) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
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
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _runtime_claims(settings: Settings, token: str) -> dict[str, object]:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            return dict(verify_runtime_token(session, settings, token))
    finally:
        engine.dispose()
