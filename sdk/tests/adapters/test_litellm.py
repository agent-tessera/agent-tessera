"""LiteLLM completion calls are governed and denials do not reach the network."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.adapters import litellm as litellm_adapter
from agent_tessera.errors import Denied


def _policy(*models: str) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"models": {"allow": list(models)}},
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_adapter_source_does_not_import_litellm() -> None:
    source = Path(litellm_adapter.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = [node.module]
        assert all(name.split(".")[0] != "litellm" for name in names)


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", maxsplit=1)[0] == "litellm":
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ImportError, match=r"agent-tessera\[litellm\]"):
        litellm_adapter.import_litellm()


def test_completion_is_governed_and_denial_skips_the_call(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    called: list[str] = []

    class LiteLLM:
        def completion(self, **kwargs: object) -> SimpleNamespace:
            called.append(str(kwargs.get("model")))
            usage = SimpleNamespace(prompt_tokens=2, completion_tokens=3)
            return SimpleNamespace(usage=usage)

        async def acompletion(self, **kwargs: object) -> SimpleNamespace:
            called.append(f"async:{kwargs.get('model')}")
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
            return SimpleNamespace(usage=usage)

    LiteLLM.__module__ = "litellm"
    client = Tessera("bot", policy=_policy("litellm/*"), audit_path=path)
    try:
        result = client.wrap(LiteLLM()).completion(model="gpt-4.1-mini", messages=[])
        assert result.usage.prompt_tokens == 2
        assert asyncio.run(client.wrap(LiteLLM()).acompletion(model="gpt-4.1-mini"))
    finally:
        client.close()
    calls = [event for event in _events(path) if event["type"] == "call"]
    assert calls[-1]["resource"] == "litellm/gpt-4.1-mini"
    denied = Tessera("bot", policy=_policy("local/*"), audit_path=tmp_path / "deny.jsonl")
    try:
        with pytest.raises(Denied):
            denied.wrap(LiteLLM()).completion(model="gpt-4.1-mini", messages=[])
    finally:
        denied.close()
    assert called == ["gpt-4.1-mini", "async:gpt-4.1-mini"]
