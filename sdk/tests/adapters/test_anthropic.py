"""Tests for the Anthropic adapter."""

import ast
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from anthropic import Anthropic, InternalServerError

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.adapters import anthropic as anthropic_adapter
from agent_tessera.audit import verify_audit_log
from agent_tessera.errors import ConfigError, Denied

_MESSAGE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 5, "output_tokens": 6},
}


def _client(tmp_path: Path, *models: str) -> tuple[Tessera, Path]:
    path = tmp_path / "audit.jsonl"
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"models": {"allow": list(models)}},
        }
    )
    return Tessera("bot", policy=policy, audit_path=path), path


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_adapter_source_does_not_import_the_anthropic_sdk() -> None:
    source = Path(anthropic_adapter.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = [node.module]
        assert all(name.split(".")[0] != "anthropic" for name in names)


def test_unknown_anthropic_class_is_rejected(tmp_path: Path) -> None:
    client, path = _client(tmp_path, "anthropic/*")

    class NotAClient:
        pass

    NotAClient.__module__ = "anthropic"
    with pytest.raises(ConfigError, match="NotAClient"):
        client.wrap(NotAClient())
    client.close()
    assert verify_audit_log(path).ok is True


def test_async_client_records_usage(tmp_path: Path) -> None:
    client, path = _client(tmp_path, "anthropic/*")

    class AsyncAnthropic:
        def __init__(self) -> None:
            self.messages = SimpleNamespace(create=self._create)

        async def _create(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=3))

    AsyncAnthropic.__module__ = "anthropic"

    async def run() -> None:
        wrapped = client.wrap(AsyncAnthropic())
        await wrapped.messages.create(model="claude-haiku-4-5", max_tokens=8)
        await wrapped.messages.create(model="claude-haiku-4-5", max_tokens=8, stream=True)

    asyncio.run(run())
    client.close()
    usage = [event for event in _events(path) if event["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["model"] == "anthropic/claude-haiku-4-5"
    assert usage[0]["input_tokens"] == 2
    assert usage[0]["output_tokens"] == 3
    assert Decimal(str(usage[0]["cost_usd"])) > 0
    assert verify_audit_log(path).ok is True


def test_real_anthropic_client_records_usage_and_errors(tmp_path: Path) -> None:
    seen: list[str] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_MESSAGE)

    raw = Anthropic(
        api_key="test",
        base_url="http://test",
        http_client=httpx.Client(transport=httpx.MockTransport(ok)),
    )
    client, path = _client(tmp_path, "anthropic/*")
    try:
        result = client.wrap(raw).messages.create(
            model="claude-haiku-4-5",
            max_tokens=16,
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        raw.close()
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 6
    calls = [event for event in _events(path) if event["type"] == "call"]
    usage = [event for event in _events(path) if event["type"] == "usage"]
    assert calls[-1]["status"] == "ok"
    assert calls[-1]["resource"] == "anthropic/claude-haiku-4-5"
    assert calls[-1]["streamed"] is False
    assert calls[-1]["finish_reason"] == "end_turn"
    assert usage[-1]["input_tokens"] == 5
    assert usage[-1]["output_tokens"] == 6

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"type": "error", "error": {"type": "api_error"}})

    failing = Anthropic(
        api_key="test",
        base_url="http://test",
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    try:
        with pytest.raises(InternalServerError):
            client.wrap(failing).messages.create(
                model="claude-haiku-4-5",
                max_tokens=16,
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        failing.close()
    error = [event for event in _events(path) if event["type"] == "call"][-1]
    assert error["status"] == "error"
    assert str(error["error_type"]).startswith("anthropic.")
    client.close()
    assert verify_audit_log(path).ok is True
    assert seen


def test_denied_model_does_not_call_the_transport(tmp_path: Path) -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json=_MESSAGE)

    raw = Anthropic(
        api_key="test",
        base_url="http://test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client, path = _client(tmp_path, "local/*")
    try:
        with pytest.raises(Denied):
            client.wrap(raw).messages.create(
                model="claude-haiku-4-5",
                max_tokens=16,
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        raw.close()
    assert called == []
    assert not any(event["type"] == "call" for event in _events(path))
    client.close()
    assert verify_audit_log(path).ok is True
