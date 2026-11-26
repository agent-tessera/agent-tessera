"""MCP tool calls are enforced against a fake session."""

import asyncio

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.errors import Denied
from agent_tessera.integrations.mcp import GovernedSession, import_mcp


def _policy(*tools: str) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": list(tools)}},
        }
    )


def test_fake_session_is_enforced(tmp_path) -> None:
    called: list[tuple[str, object]] = []

    class Session:
        def call_tool(self, name: str, arguments: object) -> str:
            called.append((name, arguments))
            return name

    client = Tessera("bot", policy=_policy("files.read"), audit_path=tmp_path / "audit.jsonl")
    try:
        governed = GovernedSession(client, Session())
        assert governed.call_tool("files.read", {"path": "notes.txt"}) == "files.read"
        with pytest.raises(Denied):
            governed.call_tool("shell.exec", {})
    finally:
        client.close()
    assert called == [("files.read", {"path": "notes.txt"})]


def test_async_session_returns_a_coroutine(tmp_path) -> None:
    class Session:
        async def call_tool(self, name: str, arguments: object) -> str:
            return name

    client = Tessera("bot", policy=_policy("files.read"), audit_path=tmp_path / "audit.jsonl")
    try:
        governed = GovernedSession(client, Session())
        assert asyncio.run(governed.call_tool("files.read")) == "files.read"
    finally:
        client.close()


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", maxsplit=1)[0] == "mcp":
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ImportError, match=r"agent-tessera\[mcp\]"):
        import_mcp()
