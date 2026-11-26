"""Field-level 422 details on request validation failures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str]]]:
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
        with TestClient(app) as test_client:
            yield test_client, headers
    finally:
        downgrade_database(database_url)


def test_missing_agent_field_is_named(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, headers = client
    response = test_client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "playground"},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "invalid_request"
    fields = {entry["field"] for entry in body["details"]}
    assert "environment" in fields
    assert "owner_id" in fields
    for entry in body["details"]:
        assert "reason" in entry
        assert isinstance(entry["reason"], str)


def test_secret_in_body_is_never_echoed(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, headers = client
    secret = "s3cret-must-not-appear"  # noqa: S105
    response = test_client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "playground",
            "environment": "prod",
            "owner_id": "missing",
            "api_key": secret,
        },
    )
    assert response.status_code == 422
    assert secret not in response.text
    details = response.json()["error"]["details"]
    assert any(entry["field"] == "api_key" for entry in details)
