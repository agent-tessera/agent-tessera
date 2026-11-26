"""Server-mode approval wait, timeout, and approval ids."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Thread

import httpx
import pytest

from agent_tessera import ApprovalRequired, Denied, Tessera
from agent_tessera.types import Action, Reason

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: approver
  environment: prod
spec:
  tools:
    allow: ["files.read"]
    require_approval: ["files.delete"]
  approvals:
    route_to: [user:ops]
    timeout: "{timeout}"
    forbid_self_approval: true
"""


def test_blocking_approve_records_wait(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _quiet(monkeypatch, tmp_path)
    token = _token(live_server.url, live_server.headers, timeout="30s")
    with Tessera("approver", server_url=live_server.url, agent_token=token) as client:

        @client.tool("files.delete")
        def delete(path: str) -> str:
            return path

        holder: dict[str, object] = {}

        def run() -> None:
            try:
                holder["result"] = delete("notes.txt")
            except Exception as err:
                holder["error"] = err

        thread = Thread(target=run)
        thread.start()
        approval_id = _pending(live_server.url, live_server.headers)
        approved = httpx.post(
            f"{live_server.url}/v1/approvals/{approval_id}:approve",
            headers=live_server.headers,
            json={"comment": "ok"},
            timeout=5,
        )
        assert approved.status_code == 200
        thread.join(5)
        assert holder.get("error") is None
        assert holder.get("result") == "notes.txt"
        assert client.flush(2)
    calls = _events(live_server.url, live_server.headers, "call")
    waits = [
        item.get("approval_wait_ms") for item in calls if item.get("resource") == "files.delete"
    ]
    assert waits
    assert isinstance(waits[0], int)
    assert waits[0] >= 0


def test_approval_timeout_denies(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _quiet(monkeypatch, tmp_path)
    token = _token(live_server.url, live_server.headers, timeout="1s")
    with (
        Tessera("approver", server_url=live_server.url, agent_token=token) as client,
        pytest.raises(Denied) as denied,
    ):
        client.enforce(Action.tool("files.delete"))
    assert denied.value.decision.reason is Reason.APPROVAL_TIMEOUT


def test_approval_wait_false_returns_the_id(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _quiet(monkeypatch, tmp_path)
    token = _token(live_server.url, live_server.headers, timeout="30s")
    with (
        Tessera(
            "approver",
            server_url=live_server.url,
            agent_token=token,
            approval_wait=False,
        ) as client,
        pytest.raises(ApprovalRequired) as required,
    ):
        client.enforce(Action.tool("files.delete"))
    approval_id = required.value.approval_id
    assert isinstance(approval_id, str)
    assert approval_id
    fetched = httpx.get(
        f"{live_server.url}/v1/approvals/{approval_id}",
        headers=live_server.headers,
        timeout=5,
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending"


def _quiet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.delenv("TESSERA_BASELINES", raising=False)


def _token(url: str, headers: dict[str, str], *, timeout: str) -> str:
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
            json={
                "name": "approver",
                "environment": "prod",
                "owner_id": owner.json()["id"],
            },
        )
        created.raise_for_status()
        agent_id = str(created.json()["id"])
        stored = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_POLICY.format(timeout=timeout),
        )
        stored.raise_for_status()
        client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
        token = client.post(
            f"/v1/agents/{agent_id}/registration-tokens",
            headers=headers,
            json={},
        )
        token.raise_for_status()
    return str(token.json()["token"])


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


def _events(url: str, headers: dict[str, str], event_type: str) -> list[dict[str, object]]:
    response = httpx.get(
        f"{url}/v1/audit",
        headers=headers,
        params={"type": event_type, "limit": 100},
        timeout=5,
    )
    response.raise_for_status()
    items = response.json()["items"]
    assert isinstance(items, list)
    return [item for item in items if isinstance(item, dict)]
