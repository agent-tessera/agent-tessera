"""Supervisor cap denies a tool, then a dead-man clock suspends the agent."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent_tessera import Denied, Tessera
from agent_tessera.types import Action, Reason

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: runner
  environment: prod
  autonomy: autonomous
spec:
  tools:
    allow: ["files.read", "shell.exec"]
  supervision:
    cap_to_supervisor: true
    max_unacknowledged: 1h
"""

_GRANTS = """\
apiVersion: tessera/v1
kind: SupervisorGrants
metadata:
  supervisor: user:ada
spec:
  tools:
    allow: ["files.read"]
"""


def test_cap_denies_then_dead_man_suspends(
    live_server,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.delenv("TESSERA_BASELINES", raising=False)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    agent_id, token = _register(live_server.url, live_server.headers)
    client = Tessera("runner", server_url=live_server.url, agent_token=token)
    try:
        listener = client._listener
        assert listener is not None
        assert listener._ready.wait(3)
        with client.task("capped-run", supervisor="user:ada"):
            client.enforce(Action.tool("files.read"))
            with pytest.raises(Denied) as denied:
                client.enforce(Action.tool("shell.exec"))
        assert denied.value.decision.reason is Reason.NOT_GRANTED
        assert denied.value.decision.rule == "agent:supervision.cap_to_supervisor"
        _age_and_suspend(live_server.app, agent_id)
        assert _until(lambda: client.check(Action.tool("files.read")).reason is Reason.SUSPENDED)
    finally:
        client.close()


def _register(url: str, headers: dict[str, str]) -> tuple[str, str]:
    with httpx.Client(base_url=url, timeout=5) as client:
        owner = client.post(
            "/v1/owners",
            headers=headers,
            json={"type": "user", "email": "runner@example.com", "display_name": "Runner"},
        )
        owner.raise_for_status()
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"name": "runner", "environment": "prod", "owner_id": owner.json()["id"]},
        )
        created.raise_for_status()
        agent_id = str(created.json()["id"])
        stored = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_POLICY,
        )
        stored.raise_for_status()
        grants = client.put(
            "/v1/supervisors/user:ada/grants",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_GRANTS,
        )
        grants.raise_for_status()
        assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
        minted = client.post(
            f"/v1/agents/{agent_id}/registration-tokens",
            headers=headers,
            json={},
        )
        minted.raise_for_status()
    return agent_id, str(minted.json()["token"])


def _age_and_suspend(app: object, agent_id: str) -> None:
    from tessera_server.db.models import Agent  # noqa: PLC0415
    from tessera_server.services.supervision import advance_dead_men  # noqa: PLC0415

    moment = datetime.now(UTC)
    with app.state.sessions() as session:  # type: ignore[attr-defined]
        row = session.get(Agent, agent_id)
        assert row is not None
        row.created_at = moment - timedelta(hours=2)
        row.supervisor_ack_at = None
        session.commit()
        assert advance_dead_men(session, app.state.settings) == 1  # type: ignore[attr-defined]


def _until(ready: object, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():  # type: ignore[operator]
            return True
        time.sleep(0.05)
    return bool(ready())  # type: ignore[operator]
