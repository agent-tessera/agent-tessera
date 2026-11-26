"""OIDC admin sign-in, then a workload-identity bootstrap of that agent."""

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

import tessera_server.api.oidc as oidc_api
import tessera_server.services.workload as workload_api
from tessera_server.app import create_app
from tessera_server.config import Settings, WorkloadBinding
from tessera_server.db.migrate import downgrade_database, upgrade_database

_ISSUER = "https://idp.example"
_CLIENT = "tessera-admin"
_SERVER = "http://127.0.0.1:8700"
_SECRET = "secret"  # noqa: S105
_AUDIENCE = "tessera"
_INSTANCE = "cd" * 16
_PRIVATE = Ed25519PrivateKey.generate()
_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["files.read"]
"""


@pytest.fixture
def org(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    upgrade_database(database_url)
    jwk = json.loads(OKPAlgorithm.to_jwk(_PRIVATE.public_key()))
    jwk["kid"] = "idp"
    jwk["alg"] = "EdDSA"
    admin_token = _id_token("ada")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_discovery())
        if path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [jwk]})
        if path.endswith("/token") and "code=good" in request.content.decode():
            return httpx.Response(200, json={"id_token": admin_token})
        return httpx.Response(404)

    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(oidc_api, "open_client", factory)
    monkeypatch.setattr(workload_api, "open_client", factory)
    settings = Settings(
        database_url=database_url,
        issuer=_SERVER,
        org="default",
        kek=b"k" * 32,
        dev=False,
        oidc_issuer=_ISSUER,
        oidc_client_id=_CLIENT,
        oidc_client_secret=_SECRET,
        workload_bindings=(
            WorkloadBinding(
                issuer=_ISSUER,
                audience=_AUDIENCE,
                agent="billing",
                environment="prod",
            ),
        ),
    )
    try:
        with TestClient(create_app(settings)) as client:
            yield client
    finally:
        downgrade_database(database_url)


def test_sso_admin_registers_an_agent_and_workload_bootstraps_it(org: TestClient) -> None:
    started = org.get("/oidc/start", follow_redirects=False)
    assert started.status_code == 303
    state = org.cookies.get("tessera_oidc_state")
    assert state
    finished = org.get(
        "/oidc/callback",
        params={"code": "good", "state": state},
        follow_redirects=False,
    )
    assert finished.status_code == 303
    assert str(org.cookies.get("tessera_admin")).startswith("tss_")
    # Cookie sessions authorize `/v1` (CSRF still required on POST).
    assert org.get("/v1/agents").status_code == 200

    csrf = org.get("/v1/me").json()["csrf"]
    csrf_headers = {"X-CSRF-Token": csrf}
    owner = org.post(
        "/v1/owners",
        headers=csrf_headers,
        json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200, owner.text
    created = org.post(
        "/v1/agents",
        headers=csrf_headers,
        json={
            "name": "billing",
            "environment": "prod",
            "owner_id": owner.json()["id"],
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["id"]
    uploaded = org.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**csrf_headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    assert uploaded.status_code == 200, uploaded.text
    activated = org.post(f"/v1/agents/{agent_id}:activate", headers=csrf_headers)
    assert activated.status_code == 200, activated.text
    page = org.get(f"/v1/agents/{agent_id}")
    assert page.status_code == 200
    assert page.json()["status"] == "active"

    issued = org.post(
        "/v1/token",
        json={
            "grant_type": "workload",
            "assertion": _assertion(),
            "agent": "billing",
            "environment": "prod",
            "instance_id": _INSTANCE,
            "runtime": {},
        },
    )
    assert issued.status_code == 200, issued.text
    token = str(issued.json()["access_token"])
    uploaded_event = org.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "events": [
                {
                    "id": "ab" * 16,
                    "ts": "2026-11-08T03:00:00.000Z",
                    "type": "decision",
                    "agent": "billing",
                    "environment": "prod",
                    "instance": _INSTANCE,
                    "effect": "allow",
                }
            ]
        },
    )
    assert uploaded_event.status_code == 200, uploaded_event.text
    assert uploaded_event.json()["accepted"] >= 1


def _id_token(sub: str) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": _ISSUER,
        "sub": sub,
        "aud": _CLIENT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(claims, _PRIVATE, algorithm="EdDSA", headers={"kid": "idp"})
    return token if isinstance(token, str) else token.decode("ascii")


def _assertion() -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": _ISSUER,
        "sub": "system:serviceaccount:prod:billing",
        "aud": _AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(claims, _PRIVATE, algorithm="EdDSA", headers={"kid": "idp"})
    return token if isinstance(token, str) else token.decode("ascii")


def _discovery() -> dict[str, str]:
    return {
        "issuer": _ISSUER,
        "authorization_endpoint": f"{_ISSUER}/authorize",
        "token_endpoint": f"{_ISSUER}/token",
        "jwks_uri": f"{_ISSUER}/jwks",
    }
