"""Confirm reasons via the admin API (classic search UI removed)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AuditEntry
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, str, str]]:
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
    try:
        with TestClient(create_app(settings)) as client:
            yield client, admin, database_url
    finally:
        downgrade_database(database_url)


def _create_agent(client: TestClient, admin: str) -> str:
    headers = {"Authorization": f"Bearer {admin}"}
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ops@example.com", "display_name": "Ops"},
    )
    assert owner.status_code == 200, owner.text
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "playground",
            "environment": "dev",
            "owner_id": owner.json()["id"],
        },
    )
    assert created.status_code == 200, created.text
    return str(created.json()["id"])


def test_retire_reason_is_audited(api: tuple[TestClient, str, str]) -> None:
    client, admin, url = api
    agent_id = _create_agent(client, admin)
    headers = {"Authorization": f"Bearer {admin}"}
    policy = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: playground
  environment: dev
spec:
  models:
    allow: ["*"]
"""
    assert (
        client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=policy,
        ).status_code
        == 200
    )
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    retired = client.post(
        f"/v1/agents/{agent_id}:retire",
        headers=headers,
        json={"reason": "end of season"},
    )
    assert retired.status_code == 200, retired.text
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            row = session.scalars(
                select(AuditEntry)
                .where(AuditEntry.entry_type == "agent_retired")
                .order_by(AuditEntry.seq.desc())
            ).first()
            assert row is not None
            assert row.payload.get("reason") == "end of season"
    finally:
        engine.dispose()


def test_search_lists_playground_agent(api: tuple[TestClient, str, str]) -> None:
    client, admin, _url = api
    _create_agent(client, admin)
    headers = {"Authorization": f"Bearer {admin}"}
    page = client.get("/v1/search", headers=headers, params={"q": "play"})
    assert page.status_code == 200, page.text
    body = page.json()
    text = str(body)
    assert "playground" in text
