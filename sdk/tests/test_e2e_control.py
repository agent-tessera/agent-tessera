"""One control-plane run covers approval, a quota denial, and the live page."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Thread

import httpx
import pytest

from agent_tessera import Denied, Tessera
from agent_tessera.types import Reason

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: control
  environment: prod
spec:
  tools:
    allow: ["files.read", "files.delete"]
    require_approval: ["files.delete"]
  approvals:
    route_to: [user:ops]
    timeout: 30s
    forbid_self_approval: true
  limits:
    quotas:
      - name: reads
        metric: tool_calls
        window: day
        scope: instance
        max: 1
        resources: ["files.read"]
"""


def test_approve_quota_deny_and_live_page(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    url = live_server.url
    headers = live_server.headers
    agent_id, token = _register(url, headers)
    with Tessera("control", server_url=url, agent_token=token) as client:

        @client.tool("files.read")
        def read(path: str) -> str:
            return path

        @client.tool("files.delete")
        def delete(path: str) -> str:
            return path

        assert read("notes.txt") == "notes.txt"
        with pytest.raises(Denied) as denied:
            read("notes.txt")
        assert denied.value.decision.reason is Reason.LIMIT
        holder: dict[str, object] = {}

        def run() -> None:
            try:
                holder["result"] = delete("notes.txt")
            except Exception as err:
                holder["error"] = err

        thread = Thread(target=run)
        thread.start()
        approval_id = _pending(url, headers)
        approved = httpx.post(
            f"{url}/v1/approvals/{approval_id}:approve",
            headers=headers,
            json={"comment": "ok"},
            timeout=5,
        )
        assert approved.status_code == 200
        thread.join(5)
        assert holder.get("error") is None
        assert holder.get("result") == "notes.txt"
        assert client.flush(2)
    with httpx.Client(base_url=url, timeout=5) as http:
        listed = http.get(
            "/v1/events",
            headers=headers,
            params={"agent": agent_id, "type": "decision", "limit": 20},
        )
    assert listed.status_code == 200
    assert "files.delete" in listed.text


def _register(url: str, headers: dict[str, str]) -> tuple[str, str]:
    with httpx.Client(base_url=url, timeout=5) as client:
        owner = client.post(
            "/v1/owners",
            headers=headers,
            json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
        )
        owner.raise_for_status()
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"name": "control", "environment": "prod", "owner_id": owner.json()["id"]},
        )
        created.raise_for_status()
        agent_id = str(created.json()["id"])
        stored = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_POLICY,
        )
        stored.raise_for_status()
        activated = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
        activated.raise_for_status()
        token = client.post(
            f"/v1/agents/{agent_id}/registration-tokens",
            headers=headers,
            json={},
        )
        token.raise_for_status()
    return agent_id, str(token.json()["token"])


def _pending(url: str, headers: dict[str, str]) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{url}/v1/approvals",
            headers=headers,
            params={"status": "pending"},
            timeout=5,
        )
        response.raise_for_status()
        items = response.json()["items"]
        if items:
            return str(items[0]["id"])
        time.sleep(0.05)
    raise AssertionError("approval was not created")
