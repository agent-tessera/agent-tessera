"""CrewAI tool runs are enforced without a network call."""

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.errors import ConfigError, Denied
from agent_tessera.integrations.crewai import govern_tool, import_crewai


def _policy(*tools: str) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": list(tools)}},
        }
    )


def test_govern_tool_enforces_and_denial_skips_run(tmp_path) -> None:
    called: list[str] = []

    class Tool:
        name = "search"

        def _run(self, query: str) -> str:
            called.append(query)
            return query

    client = Tessera("bot", policy=_policy("search"), audit_path=tmp_path / "audit.jsonl")
    try:
        tool = govern_tool(client, Tool())
        assert tool._run("tickets") == "tickets"
        blocked = Tool()
        blocked.name = "shell.exec"
        govern_tool(client, blocked)
        with pytest.raises(Denied):
            blocked._run("rm")
        with pytest.raises(ConfigError, match="name"):
            govern_tool(client, object())
    finally:
        client.close()
    assert called == ["tickets"]


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", maxsplit=1)[0] == "crewai":
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ImportError, match=r"agent-tessera\[crewai\]"):
        import_crewai()
