"""Tests for the OpenAI and Azure OpenAI adapters."""

import ast
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import InternalServerError, OpenAI

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.adapters import openai as openai_adapter
from agent_tessera.audit import verify_audit_log
from agent_tessera.errors import ConfigError, Denied

_CHAT = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4.1-mini",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
}


def _policy(*models: str) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"models": {"allow": list(models)}},
        }
    )


def _client(tmp_path: Path, *models: str) -> tuple[Tessera, Path]:
    path = tmp_path / "audit.jsonl"
    return Tessera("bot", policy=_policy(*models), audit_path=path), path


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _openai(cls: type) -> type:
    cls.__module__ = "openai"
    return cls


class _Bag:
    def __init__(self, **fields: object) -> None:
        for name, value in fields.items():
            setattr(self, name, value)


def test_adapter_source_does_not_import_the_openai_sdk() -> None:
    source = Path(openai_adapter.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = [node.module]
        assert all(name.split(".")[0] != "openai" for name in names)


def test_sync_async_azure_stream_and_missing_usage(tmp_path: Path) -> None:
    client, path = _client(tmp_path, "openai/*", "azure/*")

    @_openai
    class OpenAI:
        def __init__(self) -> None:
            self.models = "real-models"
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
            self.responses = SimpleNamespace(create=self._responses)

        def _chat(self, **kwargs: object) -> _Bag:
            return _Bag(usage=_Bag(prompt_tokens=3, completion_tokens=4), echo=kwargs)

        def _responses(self, **kwargs: object) -> _Bag:
            return _Bag(usage=_Bag(input_tokens=5, output_tokens=6))

    @_openai
    class AsyncOpenAI:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

        async def _chat(self, **kwargs: object) -> _Bag:
            return _Bag(usage=_Bag(prompt_tokens=1, completion_tokens=2))

    @_openai
    class AzureOpenAI:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

        def _chat(self, **kwargs: object) -> _Bag:
            return _Bag(usage=_Bag(prompt_tokens=8, completion_tokens=9))

    def bare_client() -> object:
        @_openai
        class OpenAI:
            def __init__(self) -> None:
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace())
                )

        return OpenAI()

    governed = client.wrap(OpenAI())
    assert governed.models == "real-models"
    chat = governed.chat.completions.create(model="gpt-4.1-mini", messages=[])
    assert chat.echo["model"] == "gpt-4.1-mini"
    governed.responses.create(model="gpt-4.1")
    governed.chat.completions.create(model="gpt-4.1-mini", stream=True)
    client.wrap(bare_client()).chat.completions.create(model="gpt-4.1-mini")

    async def run_async() -> None:
        wrapped = client.wrap(AsyncOpenAI())
        result = await wrapped.chat.completions.create(model="gpt-4.1")
        assert result.usage.prompt_tokens == 1
        await wrapped.chat.completions.create(model="gpt-4.1", stream=True)

    asyncio.run(run_async())
    client.wrap(AzureOpenAI()).chat.completions.create(model="support-deploy")
    client.close()

    usage = [event for event in _events(path) if event["type"] == "usage"]
    recorded = [(event["model"], event["input_tokens"], event["output_tokens"]) for event in usage]
    assert recorded == [
        ("openai/gpt-4.1-mini", 3, 4),
        ("openai/gpt-4.1", 5, 6),
        ("openai/gpt-4.1", 1, 2),
        ("azure/support-deploy", 8, 9),
    ]
    costs = {event["model"]: event["cost_usd"] for event in usage}
    assert Decimal(str(costs["openai/gpt-4.1-mini"])) > 0
    assert Decimal(str(costs["openai/gpt-4.1"])) > 0
    assert costs["azure/support-deploy"] == "0"
    calls = [event for event in _events(path) if event["type"] == "call"]
    assert all(event["status"] == "ok" for event in calls)
    assert len(calls) == 7
    assert [event["streamed"] for event in calls] == [
        False,
        False,
        True,
        False,
        False,
        True,
        False,
    ]
    assert verify_audit_log(path).ok is True


def test_none_usage_fields_are_skipped(tmp_path: Path) -> None:
    client, path = _client(tmp_path, "openai/*")

    @_openai
    class OpenAI:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

        def _chat(self, **kwargs: object) -> _Bag:
            tokens: object = True if kwargs.get("model") == "bool-model" else None
            return _Bag(usage=_Bag(prompt_tokens=tokens, completion_tokens=4))

    wrapped = client.wrap(OpenAI())
    wrapped.chat.completions.create(model="gpt-4.1-mini")
    wrapped.chat.completions.create(model="bool-model")
    client.close()
    assert not any(event["type"] == "usage" for event in _events(path))
    assert verify_audit_log(path).ok is True


def test_unsupported_client_names_the_type(tmp_path: Path) -> None:
    client, path = _client(tmp_path, "openai/*")

    @_openai
    class NotAClient:
        pass

    class Botocore:
        pass

    Botocore.__module__ = "botocore.client"
    with pytest.raises(ConfigError, match="NotAClient"):
        client.wrap(NotAClient())
    with pytest.raises(
        ConfigError,
        match=r"Unsupported client botocore\.client\.Botocore; use @tessera\.model_call instead",
    ):
        client.wrap(Botocore())
    client.close()
    assert verify_audit_log(path).ok is True


def test_real_openai_client_records_usage_and_errors(tmp_path: Path) -> None:
    seen: list[str] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_CHAT)

    raw = OpenAI(
        api_key="test",
        base_url="http://test",
        http_client=httpx.Client(transport=httpx.MockTransport(ok)),
    )
    client, path = _client(tmp_path, "openai/*")
    try:
        result = client.wrap(raw).chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        raw.close()
    assert result.usage is not None
    assert result.usage.prompt_tokens == 3
    calls = [event for event in _events(path) if event["type"] == "call"]
    usage = [event for event in _events(path) if event["type"] == "usage"]
    assert calls[-1]["status"] == "ok"
    assert calls[-1]["resource"] == "openai/gpt-4.1-mini"
    assert usage[-1]["input_tokens"] == 3
    assert usage[-1]["output_tokens"] == 4

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "down", "type": "server_error"}})

    failing = OpenAI(
        api_key="test",
        base_url="http://test",
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    try:
        with pytest.raises(InternalServerError):
            client.wrap(failing).chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        failing.close()
    error = [event for event in _events(path) if event["type"] == "call"][-1]
    assert error["status"] == "error"
    assert str(error["error_type"]).startswith("openai.")
    client.close()
    assert verify_audit_log(path).ok is True
    assert seen


def test_denied_model_does_not_call_the_transport(tmp_path: Path) -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json=_CHAT)

    raw = OpenAI(
        api_key="test",
        base_url="http://test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client, path = _client(tmp_path, "local/*")
    try:
        with pytest.raises(Denied):
            client.wrap(raw).chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        raw.close()
    assert called == []
    assert not any(event["type"] == "call" for event in _events(path))
    client.close()
    assert verify_audit_log(path).ok is True
