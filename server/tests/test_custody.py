"""Admin CRUD for provider keys encrypted with the server key."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_tessera.errors import ConfigError
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AuditEntry, ProviderSecret
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.custody import decrypt_provider_secret
from tessera_server.services.secrets import create_admin_key

_PLAIN = "sk-live-example"
_NEXT = "sk-rotated-example"


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
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(app) as client:
            yield client, headers, settings
    finally:
        downgrade_database(database_url)


def test_secret_is_encrypted_and_never_returned(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    created = client.post(
        "/v1/custody/secrets",
        headers=headers,
        json={"name": "openai-prod", "provider": "openai", "secret": _PLAIN},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert set(body) == {"id", "name", "provider"}
    assert body["name"] == "openai-prod"
    assert _PLAIN not in created.text

    listed = client.get("/v1/custody/secrets", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == {"items": [body]}
    assert _PLAIN not in listed.text

    one = client.get(f"/v1/custody/secrets/{body['id']}", headers=headers)
    assert one.status_code == 200
    assert one.json() == body

    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = session.get(ProviderSecret, body["id"])
            assert row is not None
            assert _PLAIN not in row.ciphertext
            assert decrypt_provider_secret(row, settings) == _PLAIN
            other = Settings(
                database_url=settings.database_url,
                issuer=settings.issuer,
                kek=b"x" * 32,
            )
            with pytest.raises(ConfigError, match="provider secrets"):
                decrypt_provider_secret(row, other)
    finally:
        engine.dispose()

    updated = client.put(
        f"/v1/custody/secrets/{body['id']}",
        headers=headers,
        json={"secret": _NEXT},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == body
    assert _NEXT not in updated.text
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = session.get(ProviderSecret, body["id"])
            assert row is not None
            assert decrypt_provider_secret(row, settings) == _NEXT
            payloads = [json.dumps(item.payload) for item in session.scalars(select(AuditEntry))]
    finally:
        engine.dispose()
    assert payloads
    joined = "\n".join(payloads)
    assert _PLAIN not in joined
    assert _NEXT not in joined

    removed = client.delete(f"/v1/custody/secrets/{body['id']}", headers=headers)
    assert removed.status_code == 200
    assert removed.json() == {"id": body["id"]}
    missing = client.get(f"/v1/custody/secrets/{body['id']}", headers=headers)
    assert missing.status_code == 404


def test_duplicate_name_and_missing_admin(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, _settings = api
    first = client.post(
        "/v1/custody/secrets",
        headers=headers,
        json={"name": "openai-prod", "provider": "openai", "secret": _PLAIN},
    )
    assert first.status_code == 200, first.text
    again = client.post(
        "/v1/custody/secrets",
        headers=headers,
        json={"name": "openai-prod", "provider": "openai", "secret": _NEXT},
    )
    assert again.status_code == 409
    anonymous = client.get("/v1/custody/secrets")
    assert anonymous.status_code == 401
    empty = client.post(
        "/v1/custody/secrets",
        headers=headers,
        json={"name": "bad name", "provider": "openai", "secret": ""},
    )
    assert empty.status_code == 422


_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
"""


def test_issue_returns_the_key_and_audits_without_it(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    stored = client.post(
        "/v1/custody/secrets",
        headers=headers,
        json={"name": "openai-prod", "provider": "openai", "secret": _PLAIN},
    )
    assert stored.status_code == 200, stored.text
    token = _runtime_token(client, headers)
    issued = client.post(
        "/v1/custody/issue",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "openai", "ttl": 30},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["provider"] == "openai"
    assert issued.json()["api_key"] == _PLAIN
    assert issued.json()["expires_at"].endswith("Z")
    admin = client.post(
        "/v1/custody/issue",
        headers=headers,
        json={"provider": "openai"},
    )
    assert admin.status_code == 401
    too_long = client.post(
        "/v1/custody/issue",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "openai", "ttl": 121},
    )
    assert too_long.status_code == 422
    client.post(
        "/v1/custody/secrets",
        headers=headers,
        json={"name": "openai-backup", "provider": "openai", "secret": _NEXT},
    )
    ambiguous = client.post(
        "/v1/custody/issue",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "openai"},
    )
    assert ambiguous.status_code == 409
    named = client.post(
        "/v1/custody/issue",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "openai-backup"},
    )
    assert named.status_code == 200
    assert named.json()["api_key"] == _NEXT
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            payloads = [json.dumps(item.payload) for item in session.scalars(select(AuditEntry))]
    finally:
        engine.dispose()
    joined = "\n".join(payloads)
    assert "provider_secret_issued" not in joined or _PLAIN not in joined
    assert _PLAIN not in joined
    assert _NEXT not in joined


def _runtime_token(client: TestClient, headers: dict[str, str]) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "a@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    assert stored.status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    registered = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={},
    )
    assert registered.status_code == 200
    response = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": registered.json()["token"],
            "instance_id": "ef" * 16,
            "host": "app",
            "runtime": {"sdk": "0.4.0"},
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])
