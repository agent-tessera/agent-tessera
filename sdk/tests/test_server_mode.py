"""Server mode: identity, cached bundles, and fail mode."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from agent_tessera import ConfigError, ServerError, Tessera
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.remote import connection as connection_module
from agent_tessera.types import Action, Reason

_LOCAL = AgentPolicy.model_validate(
    {
        "apiVersion": "tessera/v1",
        "kind": "AgentPolicy",
        "metadata": {"agent": "billing", "environment": "prod"},
        "spec": {"tools": {"allow": ["files.read"]}},
    }
)


def test_argument_beats_environment(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TESSERA_AGENT_TOKEN", "tsr_bad_bad")
    monkeypatch.setenv("TESSERA_FAIL_MODE", "closed")
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TESSERA_CONNECT_TIMEOUT", "0.2")
    with Tessera(
        "billing",
        server_url=live_server.url,
        agent_token=live_server.token,
        fail_mode="open",
    ) as client:
        assert client.mode == "server"
        assert client.policy is not None
        assert client.check(Action.tool("files.read")).allowed is True
    assert (tmp_path / "cache" / "billing.bundle.jwt").is_file()
    assert (tmp_path / "cache" / "jwks.json").stat().st_mode & 0o777 == 0o600


def test_environment_selects_the_server(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_URL", live_server.url)
    monkeypatch.setenv("TESSERA_AGENT_TOKEN", live_server.token)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with Tessera("billing") as client:
        assert client.mode == "server"
        assert client.policy is not None


def test_local_policy_is_rejected(live_server) -> None:
    with pytest.raises(ConfigError, match="policy comes from the server"):
        Tessera("billing", policy=_LOCAL, server_url=live_server.url, agent_token=live_server.token)


def test_wrong_agent_is_rejected(
    live_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(ConfigError, match="does not match server agent"):
        Tessera("other", server_url=live_server.url, agent_token=live_server.token)


def test_cached_bundle_starts_while_the_server_is_down(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(cache))
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token):
        pass
    live_server.stop()
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token) as client:
        assert client.policy is not None
        assert client.check(Action.tool("files.read")).allowed is True
    (cache / "billing.bundle.jwt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ServerError):
        Tessera(
            "billing", server_url=live_server.url, agent_token=live_server.token, fail_mode="closed"
        )
    with Tessera(
        "billing", server_url=live_server.url, agent_token=live_server.token, fail_mode="open"
    ) as client:
        decision = client.check(Action.tool("files.read"))
    assert decision.reason is Reason.FAIL_OPEN
    assert decision.policy_version == "none"


def test_unreachable_without_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TESSERA_CONNECT_TIMEOUT", "0.2")
    monkeypatch.setenv("TESSERA_FAIL_MODE", "open")
    monkeypatch.setenv("TESSERA_AGENT_TOKEN", "tsr_missing_missing")
    with pytest.raises(ServerError):
        Tessera("billing", server_url="http://127.0.0.1:1", fail_mode="closed")
    with Tessera("billing", server_url="http://127.0.0.1:1") as client:
        assert client.policy is None
        assert client.check(Action.tool("files.read")).reason is Reason.FAIL_OPEN


def test_policy_poll_and_refresh_rejection(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(connection_module, "POLL_INTERVAL", 0.05)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token) as client:
        assert client.policy is not None
        first = client.policy.version
        _replace_policy(live_server)
        deadline = time.monotonic() + 2
        while (
            time.monotonic() < deadline
            and client.policy is not None
            and client.policy.version == first
        ):
            time.sleep(0.05)
        assert client.policy is not None
        assert client.policy.version != first
        _suspend(live_server)
        connection = client._connection
        assert connection is not None
        connection._refresh_once()
        decision = client.check(Action.tool("files.read"))
        assert decision.reason is Reason.SUSPENDED
        client.resume()
        assert client.check(Action.tool("files.read")).reason is Reason.SUSPENDED


def test_check_does_not_use_the_network(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token) as client:
        connection = client._connection
        assert connection is not None
        original = connection._http.request

        def fail(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("network during check")

        connection._http.request = fail  # type: ignore[method-assign]
        try:
            assert client.check(Action.tool("files.read")).allowed is True
        finally:
            connection._http.request = original  # type: ignore[method-assign]


def _replace_policy(server) -> None:
    body = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["files.read"]
  limits:
    max_steps_per_task: 3
"""
    response = httpx.put(
        f"{server.url}/v1/agents/{server.agent_id}/policy",
        headers={**server.headers, "Content-Type": "application/yaml"},
        content=body,
        timeout=5,
    )
    assert response.status_code == 200


def _suspend(server) -> None:
    response = httpx.post(
        f"{server.url}/v1/agents/{server.agent_id}:suspend", headers=server.headers, timeout=5
    )
    assert response.status_code == 200
