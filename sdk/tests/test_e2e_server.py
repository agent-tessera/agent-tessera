"""Register an agent and follow it through calls, suspension, and revocation."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from agent_tessera import Denied, Tessera
from agent_tessera.types import Action, Reason

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: triage
  environment: prod
spec:
  tools:
    allow: ["files.read"]
    require_approval: ["files.delete"]
"""

_BASELINE = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: prod-guard
spec:
  selector:
    environment: prod
  tools:
    deny: ["shell.*"]
"""


def test_registered_agent_is_governed_and_audited(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.delenv("TESSERA_BASELINES", raising=False)
    agent_id, token = _provision(live_server.url, live_server.headers)
    with Tessera("triage", server_url=live_server.url, agent_token=token) as client:
        listener = client._listener
        assert listener is not None
        assert listener._ready.wait(3)
        with client.task("e2e-run"):
            client.enforce(Action.tool("files.read"))
            with pytest.raises(Denied) as denied:
                client.enforce(Action.tool("shell.exec"))
            assert denied.value.decision.reason is Reason.DENY_RULE
            with pytest.raises(Denied) as unrouted:
                client.enforce(Action.tool("files.delete"))
            assert unrouted.value.decision.reason is Reason.APPROVAL_UNAVAILABLE
        assert client.flush(2)
        _post(live_server.url, live_server.headers, f"/v1/agents/{agent_id}:suspend")
        assert _until(lambda: client.check(Action.tool("files.read")).reason is Reason.SUSPENDED)
        _post(live_server.url, live_server.headers, "/v1/runs/e2e-run:revoke")
        run = _get(live_server.url, live_server.headers, "/v1/runs/e2e-run")
        assert run["status"] == "revoked"
    verify = _get(live_server.url, live_server.headers, "/v1/audit/verify")
    assert verify["ok"] is True
    assert int(verify["events"]) > 0
    page = _get(
        live_server.url,
        live_server.headers,
        "/v1/audit",
        params={"agent_id": agent_id, "type": "decision", "limit": 100},
    )
    effects = _effects(page["items"])
    assert ("files.read", "allow") in effects
    assert ("shell.exec", "deny") in effects
    assert ("files.delete", "require_approval") in effects


def _provision(url: str, headers: dict[str, str]) -> tuple[str, str]:
    """Create a baseline, an active agent, and a registration token.

    Args:
        url: Server origin.
        headers: Admin authorization.

    Returns:
        The agent id and the registration token.
    """
    with httpx.Client(base_url=url, timeout=5) as client:
        stored = client.put(
            "/v1/baselines/prod-guard",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_BASELINE,
        )
        stored.raise_for_status()
        owner = client.post(
            "/v1/owners",
            headers=headers,
            json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
        )
        owner.raise_for_status()
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"name": "triage", "environment": "prod", "owner_id": owner.json()["id"]},
        )
        created.raise_for_status()
        agent_id = str(created.json()["id"])
        policy = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_POLICY,
        )
        policy.raise_for_status()
        assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
        token = client.post(
            f"/v1/agents/{agent_id}/registration-tokens",
            headers=headers,
            json={"reusable": False},
        )
        token.raise_for_status()
    return agent_id, str(token.json()["token"])


def _post(url: str, headers: dict[str, str], path: str) -> None:
    """Send an admin POST and require success.

    Args:
        url: Server origin.
        headers: Admin authorization.
        path: Request path.
    """
    response = httpx.post(f"{url}{path}", headers=headers, timeout=5)
    response.raise_for_status()


def _get(
    url: str,
    headers: dict[str, str],
    path: str,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    """Send an admin GET and return the JSON object.

    Args:
        url: Server origin.
        headers: Admin authorization.
        path: Request path.
        params: Query string.

    Returns:
        The JSON object.
    """
    response = httpx.get(f"{url}{path}", headers=headers, params=params, timeout=5)
    response.raise_for_status()
    body = response.json()
    assert isinstance(body, dict)
    return body


def _until(ready: Callable[[], bool], timeout: float = 3) -> bool:
    """Poll until a condition holds.

    Args:
        ready: Condition to check.
        timeout: How long to wait, in seconds.

    Returns:
        Whether the condition held before the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(0.05)
    return ready()


def _effects(items: object) -> set[tuple[str, str]]:
    """Collect tool resource and effect pairs from decision events.

    Args:
        items: `items` from `GET /v1/audit`.

    Returns:
        Pairs of resource and effect.
    """
    found: set[tuple[str, str]] = set()
    if not isinstance(items, list):
        return found
    for item in items:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        effect = item.get("effect")
        if not isinstance(action, dict) or not isinstance(effect, str):
            continue
        resource = action.get("resource")
        if isinstance(resource, str):
            found.add((resource, effect))
    return found
