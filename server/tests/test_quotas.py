"""Daily token leases."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tessera_server.api.errors import ApiError
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, QuotaCounter, Revocation, TokenCounter, TokenLease
from tessera_server.services.quotas import grant_lease
from tessera_server.services.secrets import create_admin_key

_SHARED = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {name}
  environment: prod
spec:
  limits:
    per_call:
      max_output_tokens: 100
    quotas:
      - name: user-tokens
        metric: tokens
        window: day
        scope: principal
        max: 100
      - name: agent-calls
        metric: tool_calls
        window: hour
        scope: agent
        max: 4
        resources: ["zendesk.*"]
"""

_SUSPEND = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {name}
  environment: prod
spec:
  limits:
    quotas:
      - name: refunds
        metric: tool_calls
        window: hour
        scope: agent
        max: 2
        on_exhausted: suspend
"""

_ORG = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: org-calls
spec:
  selector:
    environment: prod
  limits:
    quotas:
      - name: org-calls
        metric: tool_calls
        window: hour
        scope: org
        max: 2
"""

_LOCAL = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {name}
  environment: prod
spec:
  limits:
    quotas:
      - name: local-calls
        metric: tool_calls
        window: call
        scope: instance
        max: 3
      - name: run-steps
        metric: steps
        window: day
        scope: run
        max: 2
      - name: boss-steps
        metric: steps
        window: day
        scope: supervisor
        max: 2
"""

_BARE = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {name}
  environment: prod
"""


def _policy(name: str, limit: int | None) -> str:
    limits = "" if limit is None else f"spec:\n  limits:\n    tokens_per_day: {limit}\n"
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {name}\n"
        "  environment: prod\n"
        f"{limits}"
    )


@pytest.fixture
def api(
    database_url: str,
) -> Iterator[tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]]]:
    upgrade_database(database_url)
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
        token_ttl=3 * 86_400,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            with app.state.sessions() as session:
                admin = create_admin_key(session, "ops")
            headers = {"Authorization": f"Bearer {admin}"}
            agents = {
                "small": _agent(client, headers, "small", 100),
                "large": _agent(client, headers, "large", 100_000),
                "capped": _agent(client, headers, "capped", 10_000),
                "open": _agent(client, headers, "open", None),
            }
            tokens = {
                "small": _token(client, headers, agents["small"], "aa" * 16),
                "large": _token(client, headers, agents["large"], "bb" * 16),
                "capped": _token(client, headers, agents["capped"], "cc" * 16),
                "open": _token(client, headers, agents["open"], "dd" * 16),
            }
            yield client, headers, settings, app.state.sessions, {**tokens, **_ids(agents)}
    finally:
        downgrade_database(database_url)


def test_grant_formula(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, _headers, settings, sessions, ids = api
    moment = datetime.now(UTC)
    small = _take(sessions, settings, ids["small"], moment)
    large = _take(sessions, settings, ids["large"], moment)
    assert small["granted"] == 100
    assert small["limit"] == 100
    assert small["remaining"] == 0
    assert large["granted"] == 5000
    assert large["limit"] == 100_000
    assert large["remaining"] == 95_000
    _set_counter(sessions, ids["capped_id"], moment.date(), used=9600, reserved=0)
    near = _take(sessions, settings, ids["capped"], moment)
    assert near["granted"] == 400
    _set_counter(sessions, ids["capped_id"], moment.date(), used=10_000, reserved=0)
    exhausted = _take(sessions, settings, ids["capped"], moment, lease_id=str(near["lease_id"]))
    assert exhausted["granted"] == 0
    assert exhausted["lease_id"] is None
    opened = client.post(
        "/v1/quotas/leases",
        headers={"Authorization": f"Bearer {ids['open']}"},
        json={"lease_id": None, "used": 0},
    )
    assert opened.status_code == 200
    assert opened.json()["limit"] is None
    assert opened.json()["remaining"] is None
    assert opened.json()["granted"] == 0
    assert opened.json()["lease_id"] is None
    assert client.post("/v1/quotas/leases", json={"used": 0}).status_code == 401


def test_closing_usage_and_abandoned_leases(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, headers, settings, sessions, ids = api
    moment = datetime.now(UTC)
    second = _token(client, headers, ids["large_id"], "ee" * 16)
    first = _take(sessions, settings, ids["large"], moment)
    closed = _take(
        sessions,
        settings,
        ids["large"],
        moment + timedelta(seconds=10),
        lease_id=str(first["lease_id"]),
        used=250,
    )
    assert closed["granted"] == 5000
    counter = _counter(sessions, ids["large_id"], moment.date())
    assert counter.used == 250
    assert counter.reserved == 5000
    later = moment + timedelta(seconds=10 + settings.lease_ttl + 1)
    abandoned = _take(sessions, settings, second, later)
    assert abandoned["granted"] == 5000
    booked = _counter(sessions, ids["large_id"], date.fromisoformat(str(closed["day"])))
    assert booked.used == 250 + 5000
    with sessions() as session:
        row = session.get(TokenLease, str(closed["lease_id"]))
        assert row is not None
        assert row.closed is True
    with pytest.raises(ApiError) as caught:
        _take(sessions, settings, ids["large"], moment, lease_id="missing")
    assert caught.value.status == 404


def test_day_rollover_and_shared_budget(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, headers, settings, sessions, ids = api
    moment = datetime.now(UTC)
    other = _token(client, headers, ids["capped_id"], "ff" * 16)
    first = _take(sessions, settings, ids["small"], moment)
    rolled = _take(sessions, settings, ids["small"], moment + timedelta(days=1))
    assert first["day"] != rolled["day"]
    assert rolled["granted"] == 100
    yesterday = _counter(sessions, ids["small_id"], moment.date())
    assert yesterday.used == 100
    granted = 0
    for index in range(12):
        token = ids["capped"] if index % 2 == 0 else other
        body = _take(sessions, settings, token, moment)
        amount = body["granted"]
        assert isinstance(amount, int)
        granted += amount
        assert granted <= 10_000
    assert granted == 10_000
    assert _take(sessions, settings, ids["capped"], moment)["granted"] == 0
    assert _take(sessions, settings, other, moment)["granted"] == 0


def test_agent_and_principal_quotas(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, headers, _settings, sessions, _ids = api
    agent_id = _agent_yaml(client, headers, "shared", _SHARED)
    first = _token(client, headers, agent_id, "11" * 16)
    second = _token(client, headers, agent_id, "22" * 16)
    opened = _lease(
        client, first, quota_name="agent-calls", metric="tool_calls", resource="zendesk.refund"
    )
    assert opened["granted"] == 1
    assert opened["remaining"] == 3
    closed = _lease(
        client,
        first,
        quota_name="agent-calls",
        metric="tool_calls",
        resource="zendesk.refund",
        lease_id=opened["lease_id"],
        used=1,
    )
    assert closed["granted"] == 1
    other = _lease(
        client, second, quota_name="agent-calls", metric="tool_calls", resource="zendesk.refund"
    )
    assert other["granted"] == 1
    with sessions() as session:
        counter = session.scalars(
            select(QuotaCounter).where(QuotaCounter.quota_name == "agent-calls")
        ).one()
        assert counter.scope_key == f"agent:{agent_id}"
        assert counter.used == 1
        assert counter.reserved == 2
    missed = _post(
        client, first, quota_name="agent-calls", metric="tool_calls", resource="other.refund"
    )
    assert missed.status_code == 422
    ada = _lease(client, first, quota_name="user-tokens", metric="tokens", principal="user:ada")
    assert ada["granted"] == 100
    assert ada["remaining"] == 0
    denied = _lease(client, second, quota_name="user-tokens", metric="tokens", principal="user:ada")
    assert denied["granted"] == 0
    assert denied["lease_id"] is None
    bob = _lease(client, second, quota_name="user-tokens", metric="tokens", principal="user:bob")
    assert bob["granted"] == 100
    missing = _post(client, first, quota_name="user-tokens", metric="tokens")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "quota_unavailable"
    unknown = _post(client, first, quota_name="missing", metric="tokens")
    assert unknown.status_code == 404
    mismatch = _post(client, first, quota_name="agent-calls", metric="tokens")
    assert mismatch.status_code == 422


def test_exhausted_quota_suspends_the_agent(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, headers, _settings, sessions, _ids = api
    agent_id = _agent_yaml(client, headers, "stops", _SUSPEND)
    token = _token(client, headers, agent_id, "33" * 16)
    first = _lease(client, token, quota_name="refunds", metric="tool_calls")
    second = _lease(client, token, quota_name="refunds", metric="tool_calls")
    assert first["granted"] == 1
    assert second["granted"] == 1
    exhausted = _lease(client, token, quota_name="refunds", metric="tool_calls")
    assert exhausted["granted"] == 0
    assert exhausted["lease_id"] is None
    assert exhausted["remaining"] == 0
    with sessions() as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == "suspended"
        kinds = session.scalars(
            select(Revocation.kind).where(Revocation.agent_id == agent_id)
        ).all()
    assert "agent_suspended" in kinds
    blocked = _post(client, token, quota_name="refunds", metric="tool_calls")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "agent_suspended"


def test_org_quota_is_keyed_by_agent(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, headers, _settings, _sessions, _ids = api
    stored = client.put(
        "/v1/baselines/org-calls",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_ORG,
    )
    assert stored.status_code == 200
    left = _agent_yaml(client, headers, "org-left", _BARE)
    right = _agent_yaml(client, headers, "org-right", _BARE)
    left_token = _token(client, headers, left, "44" * 16)
    left_other = _token(client, headers, left, "55" * 16)
    right_token = _token(client, headers, right, "66" * 16)
    assert _lease(client, left_token, quota_name="org-calls", metric="tool_calls")["granted"] == 1
    assert _lease(client, left_other, quota_name="org-calls", metric="tool_calls")["granted"] == 1
    assert _lease(client, left_token, quota_name="org-calls", metric="tool_calls")["granted"] == 0
    fresh = _lease(client, right_token, quota_name="org-calls", metric="tool_calls")
    assert fresh["granted"] == 1
    assert fresh["remaining"] == 1


def test_instance_call_and_run_quotas(
    api: tuple[TestClient, dict[str, str], Settings, sessionmaker[Session], dict[str, str]],
) -> None:
    client, headers, _settings, _sessions, _ids = api
    agent_id = _agent_yaml(client, headers, "bounded", _LOCAL)
    token = _token(client, headers, agent_id, "77" * 16)
    local = _lease(client, token, quota_name="local-calls", metric="tool_calls")
    assert local["lease_id"] is None
    assert local["granted"] == 0
    assert local["remaining"] == 3
    again = _lease(client, token, quota_name="local-calls", metric="tool_calls")
    assert again["remaining"] == 3
    missing_run = _post(client, token, quota_name="run-steps", metric="steps")
    assert missing_run.status_code == 422
    assert missing_run.json()["error"]["code"] == "quota_unavailable"
    one = _lease(client, token, quota_name="run-steps", metric="steps", run_id="run-1")
    other = _lease(client, token, quota_name="run-steps", metric="steps", run_id="run-2")
    assert one["granted"] == 1
    assert other["granted"] == 1
    boss = _post(client, token, quota_name="boss-steps", metric="steps")
    assert boss.status_code == 422
    assert boss.json()["error"]["code"] == "quota_unavailable"


def _ids(agents: dict[str, str]) -> dict[str, str]:
    return {f"{name}_id": agent_id for name, agent_id in agents.items()}


def _take(
    sessions: sessionmaker[Session],
    settings: Settings,
    token: str,
    moment: datetime,
    *,
    lease_id: str | None = None,
    used: int = 0,
) -> dict[str, object]:
    with sessions() as session:
        return grant_lease(
            session,
            settings,
            token,
            lease_id=lease_id,
            used=used,
            now=lambda moment=moment: moment,
        )


def _set_counter(
    sessions: sessionmaker[Session], agent_id: str, day: date, *, used: int, reserved: int
) -> None:
    with sessions() as session:
        row = session.get(TokenCounter, {"agent_id": agent_id, "day": day})
        if row is None:
            session.add(TokenCounter(agent_id=agent_id, day=day, used=used, reserved=reserved))
        else:
            row.used = used
            row.reserved = reserved
        session.commit()


def _counter(sessions: sessionmaker[Session], agent_id: str, day: date) -> TokenCounter:
    with sessions() as session:
        row = session.get(TokenCounter, {"agent_id": agent_id, "day": day})
        assert row is not None
        session.expunge(row)
        return row


def _agent(client: TestClient, headers: dict[str, str], name: str, limit: int | None) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": f"{name}@example.com", "display_name": name},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": name, "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_policy(name, limit),
    )
    assert stored.status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _agent_yaml(client: TestClient, headers: dict[str, str], name: str, content: str) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": f"{name}@example.com", "display_name": name},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={"name": name, "environment": "prod", "owner_id": owner.json()["id"]},
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=content.format(name=name),
    )
    assert stored.status_code == 200, stored.text
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    return agent_id


def _post(client: TestClient, token: str, **body: object) -> object:
    payload: dict[str, object] = {"used": 0}
    payload.update(body)
    return client.post(
        "/v1/quotas/leases",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


def _lease(client: TestClient, token: str, **body: object) -> dict[str, object]:
    response = _post(client, token, **body)
    assert response.status_code == 200, response.text
    parsed = response.json()
    assert isinstance(parsed, dict)
    return parsed


def _token(client: TestClient, headers: dict[str, str], agent_id: str, instance: str) -> str:
    created = client.post(
        f"/v1/agents/{agent_id}/registration-tokens",
        headers=headers,
        json={},
    )
    assert created.status_code == 200
    response = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": created.json()["token"],
            "instance_id": instance,
            "host": "app",
            "runtime": {"sdk": "0.1.0"},
        },
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])
