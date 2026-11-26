"""LangChain tool functions are enforced without a network call."""

import asyncio

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.errors import ConfigError, Denied
from agent_tessera.integrations.langchain import govern, import_langchain, wrap_tool


def _policy(*tools: str) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": list(tools)}},
        }
    )


def test_wrapped_tool_enforces_and_denial_skips_the_body(tmp_path) -> None:
    called: list[str] = []

    def search(query: str) -> str:
        called.append(query)
        return query

    async def fetch(query: str) -> str:
        called.append(f"async:{query}")
        return query

    client = Tessera("bot", policy=_policy("search"), audit_path=tmp_path / "audit.jsonl")
    try:
        wrapped = wrap_tool(client, "search", search)
        assert wrapped("tickets") == "tickets"
        denied = wrap_tool(client, "shell.exec", search)
        with pytest.raises(Denied):
            denied("rm")
        assert asyncio.run(wrap_tool(client, "search", fetch)("tickets")) == "tickets"
    finally:
        client.close()
    assert called == ["tickets", "async:tickets"]


def test_govern_replaces_tool_callables(tmp_path) -> None:
    called: list[str] = []

    class Tool:
        name = "search"

        def func(self, query: str) -> str:
            called.append(query)
            return query

    client = Tessera("bot", policy=_policy("search"), audit_path=tmp_path / "audit.jsonl")
    try:
        tool = govern(client, Tool())
        assert tool.func("tickets") == "tickets"
        with pytest.raises(ConfigError, match="name"):
            govern(client, object())
    finally:
        client.close()
    assert called == ["tickets"]


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("langchain_core"):
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ImportError, match=r"agent-tessera\[langchain\]"):
        import_langchain()
