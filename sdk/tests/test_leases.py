"""Server mode shares a daily token budget across instances."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Protocol

import httpx
import pytest

from agent_tessera import Tessera
from agent_tessera.types import Action, Reason

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  models:
    allow: ["*"]
  limits:
    tokens_per_day: 10000
"""

_SMALL = _POLICY.replace("tokens_per_day: 10000", "tokens_per_day: 1000")

_NAMED = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  models:
    allow: ["*"]
  limits:
    per_call:
      max_output_tokens: 100
    quotas:
      - name: agent-tokens
        metric: tokens
        window: day
        scope: agent
        max: 1000
"""


class _Server(Protocol):
    """Fields the lease tests read from the live server."""

    url: str
    headers: dict[str, str]
    agent_id: str
    token: str


def test_two_clients_stop_at_the_shared_limit(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    _put_policy(live_server, _POLICY)
    second = _registration_token(live_server)
    with _client(live_server) as first, _client(live_server, second) as other:
        total = 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            denied = 0
            for client in (first, other):
                decision = client.check(Action.model("echo"))
                if decision.reason is Reason.LIMIT:
                    denied += 1
                    assert decision.rule == "agent:limits.tokens_per_day"
                    continue
                client.record_usage("echo", 500, 0)
                total += 500
            if denied == 2 and total >= 10_000:
                break
            time.sleep(0.02)
        assert first.check(Action.model("echo")).reason is Reason.LIMIT
        assert other.check(Action.model("echo")).reason is Reason.LIMIT
        assert 10_000 <= total <= 11_000


def test_exhausted_lease_names_the_daily_rule(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    _put_policy(live_server, _SMALL)
    with _client(live_server) as client:
        assert client.check(Action.model("echo")).allowed is True
        client.record_usage("echo", 1000, 0)
        decision = client.check(Action.model("echo"))
        assert decision.reason is Reason.LIMIT
        assert decision.rule == "agent:limits.tokens_per_day"


def test_named_agent_quota_is_shared_across_instances(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    _put_policy(live_server, _NAMED)
    second = _registration_token(live_server)
    with _client(live_server) as first, _client(live_server, second) as other:
        assert first.check(Action.model("echo")).allowed is True
        denied = other.check(Action.model("echo"))
        assert denied.reason is Reason.LIMIT
        assert denied.rule == "agent:limits.quotas[agent-tokens]"
        first.record_usage("echo", 1000, 0)
        assert first.check(Action.model("echo")).reason is Reason.LIMIT


def test_enforce_does_not_call_the_network(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    _put_policy(live_server, _POLICY)
    with _client(live_server) as client:
        connection = client._connection
        assert connection is not None
        original = connection._http.request
        caller = threading.current_thread()

        def fail(*args: object, **kwargs: object) -> object:
            if threading.current_thread() is caller:
                raise AssertionError("network during enforce")
            return original(*args, **kwargs)

        connection._http.request = fail  # type: ignore[method-assign]
        try:
            assert client.enforce(Action.model("echo")).allowed is True
        finally:
            connection._http.request = original  # type: ignore[method-assign]


def test_close_reports_lease_usage(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    _put_policy(live_server, _SMALL)
    client = _client(live_server)
    connection = client._connection
    assert connection is not None
    reported: list[dict[str, object]] = []
    original = connection._http.request

    def spy(method: str, url: str, **kwargs: object) -> httpx.Response:
        if str(url).endswith("/v1/quotas/leases"):
            body = kwargs.get("json")
            if isinstance(body, dict):
                reported.append(body)
        return original(method, url, **kwargs)

    connection._http.request = spy  # type: ignore[method-assign]
    try:
        client.record_usage("echo", 250, 0)
        client.close()
    finally:
        if not client._closed:
            client.close()
    assert any(
        item.get("used") == 250 and isinstance(item.get("lease_id"), str) for item in reported
    )


def _client(server: _Server, token: str | None = None) -> Tessera:
    """Open a server-mode client.

    Args:
        server: Live control plane.
        token: Registration token. Defaults to the fixture token.

    Returns:
        A client that has not been closed.
    """
    return Tessera(
        "billing",
        server_url=server.url,
        agent_token=server.token if token is None else token,
    )


def _put_policy(server: _Server, body: str) -> None:
    """Replace the live agent's policy.

    Args:
        server: Live control plane.
        body: Policy YAML.
    """
    response = httpx.put(
        f"{server.url}/v1/agents/{server.agent_id}/policy",
        headers={**server.headers, "Content-Type": "application/yaml"},
        content=body,
        timeout=5,
    )
    assert response.status_code == 200


def _registration_token(server: _Server) -> str:
    """Mint a registration token.

    Args:
        server: Live control plane.

    Returns:
        The new token.
    """
    response = httpx.post(
        f"{server.url}/v1/agents/{server.agent_id}/registration-tokens",
        headers=server.headers,
        json={},
        timeout=5,
    )
    assert response.status_code == 200
    return str(response.json()["token"])
