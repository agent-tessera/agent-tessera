"""Bedrock runtime calls are governed and denials do not reach the client."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.adapters import bedrock as bedrock_adapter
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


def test_adapter_source_does_not_import_boto3() -> None:
    source = Path(bedrock_adapter.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = [node.module]
        assert all(name.split(".", maxsplit=1)[0] != "boto3" for name in names)


def test_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", maxsplit=1)[0] == "boto3":
            raise ImportError("missing")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ImportError, match=r"agent-tessera\[bedrock\]"):
        bedrock_adapter.import_bedrock()


def test_converse_is_governed_and_denial_skips_the_call(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    called: list[str] = []

    class BedrockRuntime:
        def converse(self, **kwargs: object) -> SimpleNamespace:
            called.append(str(kwargs.get("modelId")))
            usage = SimpleNamespace(input_tokens=4, output_tokens=5)
            return SimpleNamespace(usage=usage)

        def invoke_model(self, **kwargs: object) -> SimpleNamespace:
            called.append(f"invoke:{kwargs.get('modelId')}")
            usage = SimpleNamespace(input_tokens=1, output_tokens=1)
            return SimpleNamespace(usage=usage)

    BedrockRuntime.__module__ = "botocore.client"
    client = Tessera("bot", policy=_policy("bedrock/*"), audit_path=path)
    try:
        result = client.wrap(BedrockRuntime()).converse(modelId="anthropic.claude")
        assert result.usage.input_tokens == 4
        client.wrap(BedrockRuntime()).invoke_model(modelId="anthropic.claude")
    finally:
        client.close()
    calls = [event for event in _events(path) if event["type"] == "call"]
    assert calls[0]["resource"] == "bedrock/anthropic.claude"
    denied = Tessera("bot", policy=_policy("local/*"), audit_path=tmp_path / "deny.jsonl")
    try:
        with pytest.raises(Denied):
            denied.wrap(BedrockRuntime()).converse(modelId="anthropic.claude")
    finally:
        denied.close()
    assert called == ["anthropic.claude", "invoke:anthropic.claude"]
