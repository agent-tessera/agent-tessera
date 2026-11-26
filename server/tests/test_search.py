"""Console search API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Approval, Event, Owner, Run
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.secrets import create_admin_key

# Resource/rule facets only count events from the last 7 days.
_NOW = datetime.now(UTC)


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str]]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
            owner = Owner(
                id=new_ulid(),
                owner_type="user",
                email="search@example.com",
                display_name="ops",
            )
            session.add(owner)
            session.flush()
            agent = Agent(
                id=new_ulid(),
                name="billing-bot",
                environment="prod",
                status="active",
                owner_id=owner.id,
                description="invoices",
            )
            session.add(agent)
            session.flush()
            session.add(
                Run(
                    id="run-search-99",
                    status="running",
                    first_seen_at=_NOW,
                    last_seen_at=_NOW,
                )
            )
            session.add(
                Approval(
                    id="01APRSEARCH000000000000000",
                    agent_id=agent.id,
                    instance_id="dd" * 16,
                    run_id="run-search-99",
                    status="pending",
                    action={"kind": "tool", "resource": "files.read"},
                    decision={"effect": "require_approval"},
                    route={"parties": ["admin"], "timeout": 300},
                    expires_at=_NOW,
                    created_at=_NOW,
                )
            )
            session.add(
                Event(
                    id="e" * 32,
                    agent_id=agent.id,
                    instance_id="dd" * 16,
                    event_type="decision",
                    ts=_NOW,
                    task="run-search-99",
                    run_id="run-search-99",
                    span_id="abcdef0123456789",
                    kind="tool",
                    resource="files.read.secret",
                    effect="allow",
                    status="ok",
                    rule="agent:tools.allow[0]",
                    payload={
                        "id": "e" * 32,
                        "content": "unique-payload-needle-xyz",
                        "span_id": "abcdef0123456789",
                    },
                    received_at=_NOW,
                )
            )
            session.commit()
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


def test_search_types(client: tuple[TestClient, dict[str, str]]) -> None:
    test_client, headers = client
    short = test_client.get("/v1/search", headers=headers, params={"q": "b"})
    assert short.status_code == 200
    assert short.json()["results"] == []

    agents = test_client.get("/v1/search", headers=headers, params={"q": "billing"})
    assert any(item["type"] == "agent" for item in agents.json()["results"])

    runs = test_client.get("/v1/search", headers=headers, params={"q": "run-search"})
    assert any(item["type"] == "run" for item in runs.json()["results"])

    approvals = test_client.get("/v1/search", headers=headers, params={"q": "01APRSEARCH"})
    assert any(item["type"] == "approval" for item in approvals.json()["results"])

    spans = test_client.get("/v1/search", headers=headers, params={"q": "abcdef0123456789"})
    assert any(item["type"] == "span" for item in spans.json()["results"])

    resources = test_client.get("/v1/search", headers=headers, params={"q": "files.read"})
    assert any(item["type"] == "resource" for item in resources.json()["results"])

    rules = test_client.get("/v1/search", headers=headers, params={"q": "tools.allow"})
    assert any(item["type"] == "rule" for item in rules.json()["results"])


def test_search_ignores_payload_content(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, headers = client
    response = test_client.get(
        "/v1/search",
        headers=headers,
        params={"q": "unique-payload-needle"},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
