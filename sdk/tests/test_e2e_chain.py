"""A worker cannot use a tool the calling orchestrator does not grant."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_tessera import Denied, Tessera
from agent_tessera.types import Action, Reason

_ORCHESTRATOR = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: orchestrator
  environment: prod
spec:
  tools:
    allow: ["zendesk.read"]
"""

_WORKER = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: worker
  environment: prod
spec:
  tools:
    allow: ["shell.exec", "zendesk.read"]
  agents:
    caller_constraint: intersect
"""


def test_worker_is_denied_when_the_orchestrator_lacks_the_tool(
    live_server,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.delenv("TESSERA_BASELINES", raising=False)
    orchestrator_token = _register(
        live_server.url, live_server.headers, "orchestrator", _ORCHESTRATOR
    )
    worker_token = _register(live_server.url, live_server.headers, "worker", _WORKER)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "orchestrator"))
    orchestrator = Tessera(
        "orchestrator",
        server_url=live_server.url,
        agent_token=orchestrator_token,
    )
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "worker"))
    worker = Tessera("worker", server_url=live_server.url, agent_token=worker_token)
    try:
        with orchestrator.task("confused-deputy"), worker.receive_handoff(orchestrator):
            worker.enforce(Action.tool("zendesk.read"))
            with pytest.raises(Denied) as denied:
                worker.enforce(Action.tool("shell.exec"))
        assert denied.value.decision.reason is Reason.NOT_GRANTED
    finally:
        worker.close()
        orchestrator.close()


def _register(url: str, headers: dict[str, str], name: str, policy: str) -> str:
    with httpx.Client(base_url=url, timeout=5) as client:
        owner = client.post(
            "/v1/owners",
            headers=headers,
            json={"type": "user", "email": f"{name}@example.com", "display_name": name},
        )
        owner.raise_for_status()
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"name": name, "environment": "prod", "owner_id": owner.json()["id"]},
        )
        created.raise_for_status()
        agent_id = str(created.json()["id"])
        stored = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=policy,
        )
        stored.raise_for_status()
        assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
        token = client.post(
            f"/v1/agents/{agent_id}/registration-tokens",
            headers=headers,
            json={},
        )
        token.raise_for_status()
    return str(token.json()["token"])
