"""RFC 8693 token exchange claims."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from jwt.algorithms import OKPAlgorithm

import tessera_server.services.delegation as delegation_api
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Policy
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.secrets import create_admin_key

_ISSUER = "https://idp.example"
_CLIENT = "tessera-admin"
_INSTANCE = "de" * 16
_PRIVATE = Ed25519PrivateKey.generate()
_JWK = json.loads(OKPAlgorithm.to_jwk(_PRIVATE.public_key()))
_JWK["kid"] = "idp"
_GRANTS = (
    "token-exchange",
    "urn:ietf:params:oauth:grant-type:token-exchange",
)


@pytest.fixture
def exchange(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    monkeypatch.setattr(delegation_api, "open_client", _http)
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        kek=b"k" * 32,
        dev=False,
        oidc_issuer=_ISSUER,
        oidc_client_id=_CLIENT,
        oidc_client_secret="client-secret",  # noqa: S106
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            yield client, {"Authorization": f"Bearer {admin}"}, settings
    finally:
        downgrade_database(database_url)


@pytest.mark.parametrize("grant_type", _GRANTS)
def test_exchange_claims(
    exchange: tuple[TestClient, dict[str, str], Settings], grant_type: str
) -> None:
    client, headers, settings = exchange
    _active_agent(client, headers, settings.database_url)
    actor = _runtime(client, headers)
    first = client.post(
        "/v1/token",
        json={
            "grant_type": grant_type,
            "subject_token": _user("maria@acme.com"),
            "actor_token": actor,
        },
    )
    assert first.status_code == 200
    claims = _claims(settings, str(first.json()["access_token"]))
    assert claims["sub"] == "user:maria@acme.com"
    assert claims["sub_iss"] == _ISSUER
    assert claims["act"] == {"sub": "tessera://default/prod/billing"}
    second = client.post(
        "/v1/token",
        json={
            "grant_type": grant_type,
            "subject_token": _user("maria@acme.com"),
            "actor_token": first.json()["access_token"],
        },
    )
    assert second.status_code == 200
    nested = _claims(settings, str(second.json()["access_token"]))
    assert nested["act"] == {
        "sub": "tessera://default/prod/billing",
        "act": {"sub": "tessera://default/prod/billing"},
    }


def test_exchange_rejects_a_bad_user_audience(
    exchange: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = exchange
    _active_agent(client, headers, settings.database_url)
    rejected = client.post(
        "/v1/token",
        json={
            "grant_type": "token-exchange",
            "subject_token": _user("maria@acme.com", audience="other"),
            "actor_token": _runtime(client, headers),
        },
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "invalid_token"


def _user(sub: str, audience: str = _CLIENT) -> str:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": _ISSUER,
            "sub": sub,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        _PRIVATE,
        algorithm="EdDSA",
        headers={"kid": "idp"},
    )
    return token if isinstance(token, str) else token.decode("ascii")


def _http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={"issuer": _ISSUER, "jwks_uri": f"{_ISSUER}/jwks"},
            )
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [_JWK]})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _active_agent(client: TestClient, headers: dict[str, str], url: str) -> None:
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


def _runtime(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post(
        f"/v1/agents/{_agent_id(client, headers)}/registration-tokens",
        headers=headers,
        json={},
    )
    assert created.status_code == 200
    token = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": created.json()["token"],
            "instance_id": _INSTANCE,
            "host": "app",
            "runtime": {},
        },
    )
    assert token.status_code == 200
    return str(token.json()["access_token"])


def _agent_id(client: TestClient, headers: dict[str, str]) -> str:
    listed = client.get("/v1/agents", headers=headers)
    assert listed.status_code == 200
    return str(listed.json()["items"][0]["id"])


def _claims(settings: Settings, token: str) -> dict[str, object]:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            return dict(verify_runtime_token(session, settings, token))
    finally:
        engine.dispose()
