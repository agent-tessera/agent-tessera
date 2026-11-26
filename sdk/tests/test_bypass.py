"""Ungoverned provider calls are recorded only when detection is enabled."""

import json
from pathlib import Path

import httpx
import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.bypass import _from_adapter


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": ["*"]}},
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_default_leaves_httpx_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERA_DETECT_BYPASS", raising=False)
    original = httpx.Client.send
    client = Tessera("bot", policy=_policy(), audit_path=tmp_path / "audit.jsonl")
    try:
        assert httpx.Client.send is original
    finally:
        client.close()
    assert httpx.Client.send is original


def test_opt_in_records_host_and_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERA_DETECT_BYPASS", raising=False)
    path = tmp_path / "audit.jsonl"
    original = httpx.Client.send

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with Tessera("bot", policy=_policy(), audit_path=path, detect_bypass=True) as client:
        assert httpx.Client.send is not original
        http = httpx.Client(transport=httpx.MockTransport(handler))
        http.send(httpx.Request("POST", "https://api.openai.com/v1/chat/completions?q=1"))
        http.send(httpx.Request("GET", "https://example.com/health"))
        instance = client.instance_id
    assert httpx.Client.send is original
    calls = [event for event in _events(path) if event["type"] == "ungoverned_call"]
    assert len(calls) == 1
    assert calls[0]["host"] == "api.openai.com"
    assert calls[0]["path"] == "/v1/chat/completions"
    assert calls[0]["agent"] == "bot"
    assert calls[0]["instance"] == instance
    assert "q=1" not in json.dumps(calls[0])


def test_environment_enables_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_DETECT_BYPASS", "1")
    original = httpx.Client.send
    client = Tessera("bot", policy=_policy(), audit_path=tmp_path / "audit.jsonl")
    try:
        assert httpx.Client.send is not original
    finally:
        client.close()
    assert httpx.Client.send is original


def test_adapter_frames_are_not_ungoverned(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Frame:
        def __init__(self, name: str) -> None:
            self.f_globals = {"__name__": name}
            self.f_back: _Frame | None = None

    adapter = _Frame("agent_tessera.adapters.openai")
    monkeypatch.setattr("agent_tessera.bypass.inspect.currentframe", lambda: adapter)
    assert _from_adapter() is True
