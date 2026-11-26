"""Registry endpoints, lifecycle, and the server audit chain."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import (
    Agent,
    AuditEntry,
    Instance,
    Policy,
    RegistrationToken,
    Revocation,
)
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.audit import verify_audit
from tessera_server.services.secrets import create_admin_key, parse_secret


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], str]]:
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
        token_ttl=900,
        bundle_ttl=86400,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with TestClient(app) as client:
            yield client, headers, database_url
    finally:
        downgrade_database(database_url)


def test_registry_flow_audits_every_change(
    api: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, url = api
    owner_id, agent_id = _draft_agent(client, headers)
    _assert_orphaned_filter(client, headers, owner_id, agent_id)
    _assert_activation_and_token(client, headers, url, agent_id)
    _assert_chain(url)


def _draft_agent(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "a@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200
    owner_id = str(owner.json()["id"])
    assert owner.json()["type"] == "user"
    again = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "a@example.com", "display_name": "Ada"},
    )
    assert again.status_code == 409
    renamed = client.patch(
        f"/v1/owners/{owner_id}",
        headers=headers,
        json={"display_name": "Ada Lovelace"},
    )
    assert renamed.status_code == 200
    missing = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": "missing"},
    )
    assert missing.status_code == 404
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "billing",
            "environment": "prod",
            "owner_id": owner_id,
            "supervisors": ["finance"],
        },
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    assert created.json()["status"] == "draft"
    duplicate = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner_id},
    )
    assert duplicate.status_code == 409
    return owner_id, agent_id


def _assert_orphaned_filter(
    client: TestClient, headers: dict[str, str], owner_id: str, agent_id: str
) -> None:
    inactive = client.patch(f"/v1/owners/{owner_id}", headers=headers, json={"active": False})
    assert inactive.status_code == 200
    listed = client.get("/v1/agents", headers=headers, params={"orphaned": True})
    assert [item["id"] for item in listed.json()["items"]] == [agent_id]
    hidden = client.get("/v1/agents", headers=headers, params={"orphaned": False})
    assert hidden.json()["items"] == []
    restored = client.patch(f"/v1/owners/{owner_id}", headers=headers, json={"active": True})
    assert restored.status_code == 200


def _assert_activation_and_token(
    client: TestClient, headers: dict[str, str], url: str, agent_id: str
) -> None:
    detail = client.get(f"/v1/agents/{agent_id}", headers=headers)
    assert detail.json()["policy_version"] is None
    denied = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "no_policy"
    _link_policy(url, agent_id)
    activated = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
    assert activated.status_code == 200
    assert activated.json()["policy_version"] == "sha256:" + "ab" * 32
    token = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={"reusable": True, "expires_in": 60},
    )
    assert token.status_code == 200
    parsed = parse_secret(str(token.json()["token"]), prefix="tsr")
    assert parsed is not None
    bare = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={"reusable": True},
    )
    assert bare.status_code == 422
    instance_id = "ab" * 16
    _insert_instance(url, agent_id, instance_id)
    revoked = client.post(f"/v1/instances/{instance_id}:revoke", headers=headers)
    assert revoked.json()["kind"] == "instance_revoked"
    _assert_token_stored(url, parsed[0], parsed[1])


def _assert_token_stored(url: str, token_id: str, secret: str) -> None:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            stored = session.get(RegistrationToken, token_id)
            assert stored is not None
            assert secret not in stored.secret_sha256
            kinds = set(session.scalars(select(Revocation.kind)).all())
            assert {"agent_activated", "instance_revoked"} <= kinds
    finally:
        engine.dispose()


def _assert_chain(url: str) -> None:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            check = verify_audit(session)
            assert check.ok is True
            assert check.events == len(session.scalars(select(AuditEntry)).all())
            row = session.scalars(select(AuditEntry).order_by(AuditEntry.seq)).first()
            assert row is not None
            row.payload = {"id": "tampered"}
            session.commit()
            failed = verify_audit(session)
            assert failed.ok is False
            assert failed.error == "hash mismatch"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("start", "action", "allowed"),
    [
        ("draft", "activate", True),
        ("draft", "suspend", False),
        ("draft", "retire", False),
        ("active", "activate", False),
        ("active", "suspend", True),
        ("active", "retire", True),
        ("suspended", "activate", True),
        ("suspended", "suspend", False),
        ("suspended", "retire", True),
        ("retired", "activate", False),
        ("retired", "suspend", False),
        ("retired", "retire", False),
    ],
)
def test_lifecycle_transitions(
    api: tuple[TestClient, dict[str, str], str],
    start: str,
    action: str,
    allowed: bool,
) -> None:
    client, headers, url = api
    agent_id = _agent_in_status(client, headers, url, start)
    before = _revocation_count(url)
    response = client.post(f"/v1/agents/{agent_id}:{action}", headers=headers)
    if allowed:
        assert response.status_code == 200
        expected = {"activate": "active", "suspend": "suspended", "retire": "retired"}[action]
        assert response.json()["status"] == expected
        grew = 0 if action == "retire" else 1
        assert _revocation_count(url) == before + grew
    else:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_transition"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/owners", {"type": "user", "email": "a@b.c", "display_name": "A"}),
        ("get", "/v1/owners", None),
        ("patch", "/v1/owners/missing", {"display_name": "A"}),
        ("post", "/v1/agents", {"name": "billing", "environment": "prod", "owner_id": "x"}),
        ("get", "/v1/agents", None),
        ("get", "/v1/agents/missing", None),
        ("patch", "/v1/agents/missing", {"description": "x"}),
        ("post", "/v1/agents/missing:activate", None),
        ("post", "/v1/agents/missing:suspend", None),
        ("post", "/v1/agents/missing:retire", None),
        ("post", "/v1/agents/missing/registration-tokens", {}),
        ("post", "/v1/instances/missing:revoke", None),
    ],
)
def test_registry_requires_an_admin_key(
    api: tuple[TestClient, dict[str, str], str],
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    client, _headers, _url = api
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_agent_metadata_and_list_filters(
    api: tuple[TestClient, dict[str, str], str],
) -> None:
    client, headers, _url = api
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "team", "email": "own@example.com", "display_name": "Own"},
    )
    other = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "team", "email": "other@example.com", "display_name": "Other"},
    )
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
    )
    agent_id = created.json()["id"]
    patched = client.patch(
        f"/v1/agents/{agent_id}",
        headers=headers,
        json={
            "description": "bills",
            "owner_id": other.json()["id"],
            "risk_tier": "high",
            "labels": {"team": "finance"},
            "autonomy": "supervised",
            "supervisors": ["lead"],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "bills"
    assert patched.json()["risk_tier"] == "high"
    missing_owner = client.patch(
        f"/v1/agents/{agent_id}",
        headers=headers,
        json={"owner_id": "missing"},
    )
    assert missing_owner.status_code == 404
    listed = client.get(
        "/v1/agents",
        headers=headers,
        params={"environment": "prod", "status": "draft", "owner_id": other.json()["id"]},
    )
    assert [item["id"] for item in listed.json()["items"]] == [agent_id]


def test_owner_pages_and_unknown_routes(api: tuple[TestClient, dict[str, str], str]) -> None:
    client, headers, _url = api
    first = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "team", "email": "first@example.com", "display_name": "First"},
    )
    second = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "team", "email": "second@example.com", "display_name": "Second"},
    )
    page = client.get("/v1/owners", headers=headers, params={"limit": 1})
    assert page.status_code == 200
    assert page.json()["next_cursor"] == first.json()["id"]
    nxt = client.get(
        "/v1/owners",
        headers=headers,
        params={"limit": 1, "cursor": page.json()["next_cursor"]},
    )
    assert [item["id"] for item in nxt.json()["items"]] == [second.json()["id"]]
    exact = client.get("/v1/owners", headers=headers, params={"email": "first@example.com"})
    assert [item["email"] for item in exact.json()["items"]] == ["first@example.com"]
    prefix = client.get("/v1/owners", headers=headers, params={"email": "first@example.co"})
    assert prefix.json()["items"] == []
    assert nxt.json()["next_cursor"] is None
    assert client.get("/v1/agents/missing", headers=headers).status_code == 404
    assert client.patch("/v1/owners/missing", headers=headers, json={}).status_code == 404
    bad = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "robot", "email": "nope@example.com", "display_name": "No"},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_request"


def _agent_in_status(client: TestClient, headers: dict[str, str], url: str, status: str) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": f"{status}@example.com", "display_name": status},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": "billing", "environment": status, "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    _link_policy(url, agent_id)
    if status == "draft":
        return agent_id
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    if status == "suspended":
        assert client.post(f"/v1/agents/{agent_id}:suspend", headers=headers).status_code == 200
    if status == "retired":
        assert client.post(f"/v1/agents/{agent_id}:retire", headers=headers).status_code == 200
    return str(agent_id)


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


def _insert_instance(url: str, agent_id: str, instance_id: str) -> None:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            session.add(Instance(id=instance_id, agent_id=agent_id, host="app"))
            session.commit()
    finally:
        engine.dispose()


def _revocation_count(url: str) -> int:
    engine = make_engine(url)
    try:
        with session_factory(engine)() as session:
            return len(session.scalars(select(Revocation)).all())
    finally:
        engine.dispose()
