"""Google GenAI calls are governed and denials do not reach the client."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.adapters import gemini as gemini_adapter
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


def test_adapter_source_does_not_import_genai() -> None:
    source = Path(gemini_adapter.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = [node.module]
        assert all(not name.startswith("google") for name in names)


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google.genai"):
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ImportError, match=r"agent-tessera\[gemini\]"):
        gemini_adapter.import_gemini()


def test_generate_content_is_governed_and_denial_skips_the_call(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    called: list[str] = []

    class Client:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._generate)

        def _generate(self, **kwargs: object) -> SimpleNamespace:
            called.append(str(kwargs.get("model")))
            usage = SimpleNamespace(prompt_token_count=6, candidates_token_count=7)
            return SimpleNamespace(usage_metadata=usage)

    Client.__module__ = "google.genai.client"
    client = Tessera("bot", policy=_policy("gemini/*"), audit_path=path)
    try:
        result = client.wrap(Client()).models.generate_content(model="gemini-2.0-flash")
        assert result.usage_metadata.prompt_token_count == 6
    finally:
        client.close()
    usage = [event for event in _events(path) if event["type"] == "usage"]
    assert usage[-1]["input_tokens"] == 6
    assert usage[-1]["output_tokens"] == 7
    denied = Tessera("bot", policy=_policy("local/*"), audit_path=tmp_path / "deny.jsonl")
    try:
        with pytest.raises(Denied):
            denied.wrap(Client()).models.generate_content(model="gemini-2.0-flash")
    finally:
        denied.close()
    assert called == ["gemini-2.0-flash"]
