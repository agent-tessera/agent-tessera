"""Server-mode on_behalf_of exchanges a user token. Offline does not."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from agent_tessera import Tessera
from agent_tessera.context import current_principal
from agent_tessera.errors import ServerError
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.remote.connection import ServerConnection, ServerSettings
from agent_tessera.remote.identity import RuntimeCredential

_USER_TOKEN = "user-jwt"  # noqa: S105


def test_offline_on_behalf_of_ignores_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERA_URL", raising=False)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline mode must not exchange")

    monkeypatch.setattr("agent_tessera.remote.oidc_user.exchange_for_user", fail)
    client = Tessera("bot", policy=_policy())
    with client.on_behalf_of("ada", ["support"], user_token=_USER_TOKEN) as principal:
        assert principal.user == "ada"
        assert principal.groups == frozenset({"support"})
        assert current_principal() is principal
    assert current_principal() is None
    client.close()


def test_server_on_behalf_of_exchanges_and_restores(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "access_token": "delegated-jwt",
                "expires_in": 900,
                "agent_id": "agent-1",
                "agent": "bot",
                "environment": "prod",
            },
        )

    client, connection, original = _server_client(tmp_path, handler)
    try:
        with client.on_behalf_of("ada", ["support"], user_token=_USER_TOKEN) as principal:
            assert principal.user == "ada"
            assert connection.access_token() == "delegated-jwt"
        assert connection.access_token() == "actor-jwt"
        assert current_principal() is None
        body = seen["body"]
        assert body == {
            "grant_type": "token-exchange",
            "subject_token": _USER_TOKEN,
            "actor_token": "actor-jwt",
        }
    finally:
        original.close()
        client.close()


def test_rejected_exchange_leaves_the_actor_token(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "invalid_token", "message": "no"}})

    client, connection, original = _server_client(tmp_path, handler)
    try:
        with pytest.raises(ServerError), client.on_behalf_of("ada", user_token=_USER_TOKEN):
            raise AssertionError("the block must not run")
        assert connection.access_token() == "actor-jwt"
        assert current_principal() is None
    finally:
        original.close()
        client.close()


def _server_client(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[Tessera, ServerConnection, httpx.Client]:
    client = Tessera("bot", policy=_policy())
    settings = ServerSettings(
        url="http://127.0.0.1:9",
        agent_token=None,
        fail_mode="closed",
        cache_dir=tmp_path / "cache",
        timeout=1.0,
        spool_dir=tmp_path / "spool",
    )
    connection = ServerConnection(
        "bot",
        client.instance_id,
        settings,
        on_policy=lambda _policy, _status: None,
        on_unusable=lambda: None,
        on_rejected=lambda: None,
    )
    original = connection._http
    connection._http = httpx.Client(
        base_url="http://127.0.0.1:9",
        transport=httpx.MockTransport(handler),
    )
    connection._credential = RuntimeCredential("actor-jwt", 900, "agent-1", "bot", "prod")
    client._connection = connection
    return client, connection, original


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": ["files.read"]}},
        }
    )
