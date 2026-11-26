"""The SDK follows the revocation stream."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
import pytest

from agent_tessera import Tessera
from agent_tessera.remote import stream as stream_module
from agent_tessera.types import Action, Reason


class _Server(Protocol):
    """Fields the stream tests read from the live server."""

    url: str
    headers: dict[str, str]
    agent_id: str
    token: str


def test_suspend_and_activate_reach_the_client(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with _client(live_server) as client:
        _wait_connected(client)
        _post(live_server, f"/v1/agents/{live_server.agent_id}:suspend")
        assert _until(lambda: client.check(Action.tool("files.read")).reason is Reason.SUSPENDED)
        client.resume()
        assert client.check(Action.tool("files.read")).reason is Reason.SUSPENDED
        _post(live_server, f"/v1/agents/{live_server.agent_id}:activate")
        assert _until(lambda: client.check(Action.tool("files.read")).allowed is True)


def test_instance_revoke_suspends_the_client(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with _client(live_server) as client:
        _wait_connected(client)
        _post(live_server, f"/v1/instances/{client.instance_id}:revoke")
        assert _until(lambda: client.check(Action.tool("files.read")).reason is Reason.SUSPENDED)
        client.resume()
        assert client.check(Action.tool("files.read")).reason is Reason.SUSPENDED


def test_revoked_run_denies_every_client_in_that_task(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    second_token = _registration_token(live_server)
    with _client(live_server) as first, _client(live_server, token=second_token) as second:
        _wait_connected(first)
        _wait_connected(second)
        with first.task("shared-run"):
            assert first.check(Action.tool("files.read")).allowed is True
        assert first.flush(2)
        _post(live_server, "/v1/runs/shared-run:revoke")
        assert _until(lambda: _in_run(first) is Reason.RUN_REVOKED)
        assert _until(lambda: _in_run(second) is Reason.RUN_REVOKED)
        assert first.check(Action.tool("files.read")).allowed is True


def test_policy_update_fetches_the_bundle(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with _client(live_server) as client:
        _wait_connected(client)
        assert client.policy is not None
        first = client.policy.version
        _put_policy(live_server)
        assert _until(
            lambda: client.policy is not None and client.policy.version != first,
            timeout=3,
        )


def test_reconnect_sends_the_last_event_id(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stream_module, "INITIAL_BACKOFF", 0.05)
    monkeypatch.setattr(stream_module, "MAX_BACKOFF", 0.2)
    monkeypatch.setattr(stream_module, "_READ_TIMEOUT", 3.0)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with _client(live_server) as client:
        _wait_connected(client)
        _post(live_server, f"/v1/agents/{live_server.agent_id}:suspend")
        assert _until(lambda: client.check(Action.tool("files.read")).reason is Reason.SUSPENDED)
        connection = client._connection
        assert connection is not None
        captured: list[object] = []
        original = connection._http.stream

        def spy(method: str, url: str, **kwargs: object) -> object:
            if str(url).endswith("/v1/revocations/stream"):
                headers = kwargs.get("headers")
                if isinstance(headers, dict):
                    captured.append(headers.get("Last-Event-ID"))
            return original(method, url, **kwargs)

        connection._http.stream = spy  # type: ignore[method-assign]
        live_server.stop()
        live_server.restart()
        assert _until(lambda: any(item is not None for item in captured), timeout=5)
        resumed = [item for item in captured if item is not None]
        assert int(str(resumed[-1])) > 0
        _post(live_server, f"/v1/agents/{live_server.agent_id}:activate")
        allowed = _until(
            lambda: client.check(Action.tool("files.read")).allowed is True,
            timeout=8,
        )
        assert allowed, (
            client.suspended,
            client._server_suspended,
            client._bundle_suspended,
            client._refresh_rejected,
        )


def _client(server: _Server, token: str | None = None) -> Tessera:
    """Open a server-mode client for the live agent.

    Args:
        server: Live control plane.
        token: Registration token. Defaults to the server fixture's token.

    Returns:
        A client that has not been closed.
    """
    return Tessera(
        "billing",
        server_url=server.url,
        agent_token=server.token if token is None else token,
    )


def _wait_connected(client: Tessera) -> None:
    """Block until the revocation stream is open.

    Args:
        client: Server-mode client.

    Raises:
        AssertionError: If the stream does not open within three seconds.
    """
    listener = client._listener
    assert listener is not None
    assert listener._ready.wait(3)


def _until(ready: Callable[[], bool], timeout: float = 3) -> bool:
    """Poll until a condition holds.

    Args:
        ready: Condition to check.
        timeout: How long to wait, in seconds.

    Returns:
        `True` when the condition held in time.

    Raises:
        AssertionError: If the condition stays false.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(0.05)
    return ready()


def _in_run(client: Tessera) -> Reason:
    """Check a tool call inside the shared run.

    Args:
        client: Client under test.

    Returns:
        The decision reason.
    """
    with client.task("shared-run"):
        return client.check(Action.tool("files.read")).reason


def _post(server: _Server, path: str) -> None:
    """Send an admin POST.

    Args:
        server: Live control plane.
        path: Request path.
    """
    response = httpx.post(f"{server.url}{path}", headers=server.headers, timeout=5)
    assert response.status_code == 200


def _registration_token(server: _Server) -> str:
    """Mint another registration token for the live agent.

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


def _put_policy(server: _Server) -> None:
    """Replace the live agent's policy with one that sets a step limit.

    Args:
        server: Live control plane.
    """
    body = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["*"]
  limits:
    max_steps_per_task: 4
"""
    response = httpx.put(
        f"{server.url}/v1/agents/{server.agent_id}/policy",
        headers={**server.headers, "Content-Type": "application/yaml"},
        content=body,
        timeout=5,
    )
    assert response.status_code == 200
