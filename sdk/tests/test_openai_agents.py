"""OpenAI Agents SDK hooks enforce tools and models without a network call."""

import asyncio
import sys
import types

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.errors import Denied
from agent_tessera.integrations.openai_agents import (
    TesseraRunHooks,
    build_hooks,
    import_openai_agents,
)


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {
                "tools": {"allow": ["files.read"]},
                "models": {"allow": ["openai/gpt-4.1-mini"]},
            },
        }
    )


def test_hooks_enforce_tool_and_model(tmp_path) -> None:
    client = Tessera("bot", policy=_policy(), audit_path=tmp_path / "audit.jsonl")
    hooks = TesseraRunHooks(client)
    tool = types.SimpleNamespace(name="files.read")
    agent = types.SimpleNamespace(model="gpt-4.1-mini")
    try:
        asyncio.run(hooks.on_tool_start(None, agent, tool))
        asyncio.run(hooks.on_llm_start(None, agent, None, None))
        with pytest.raises(Denied):
            asyncio.run(hooks.on_tool_start(None, agent, types.SimpleNamespace(name="shell.exec")))
    finally:
        client.close()


def test_build_hooks_subclasses_run_hooks(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("agents")

    class RunHooks:
        def __init__(self) -> None:
            self.ready = True

    module.RunHooks = RunHooks
    monkeypatch.setitem(sys.modules, "agents", module)
    client = Tessera("bot", policy=_policy(), audit_path=tmp_path / "audit.jsonl")
    try:
        hooks = build_hooks(client)
        assert isinstance(hooks, RunHooks)
        assert isinstance(hooks, TesseraRunHooks)
    finally:
        client.close()


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", maxsplit=1)[0] == "agents":
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    monkeypatch.delitem(sys.modules, "agents", raising=False)
    with pytest.raises(ImportError, match=r"agent-tessera\[openai-agents\]"):
        import_openai_agents()
