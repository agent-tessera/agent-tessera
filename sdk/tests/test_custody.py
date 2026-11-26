"""Custody mode fetches one key per model call and leaves it off the client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent_tessera import AgentPolicy, Tessera

_ISSUED = "sk-issued-example"


def test_model_call_fetches_once_and_discards_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_KEY_CUSTODY", "1")
    client, path = _client(tmp_path)
    connection = _Connection()
    client._mode = "server"  # type: ignore[assignment]
    client._connection = connection  # type: ignore[assignment]
    seen: list[str] = []

    class OpenAI:
        def __init__(self) -> None:
            self.api_key = "stale"
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

        def _chat(self, **kwargs: object) -> dict[str, str]:
            del kwargs
            seen.append(self.api_key)
            return {"id": "ok"}

    OpenAI.__module__ = "openai"
    provider = OpenAI()
    governed = client.wrap(provider)
    governed.chat.completions.create(model="gpt-4.1-mini")
    client.close()

    assert connection.bodies == [{"provider": "openai"}]
    assert seen == [_ISSUED]
    assert provider.api_key == "stale"
    text = path.read_text(encoding="utf-8")
    assert _ISSUED not in text
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert any(event["type"] == "call" for event in events)


def test_offline_mode_does_not_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_KEY_CUSTODY", "1")
    client, _path = _client(tmp_path)
    connection = _Connection()
    client._connection = connection  # type: ignore[assignment]

    class OpenAI:
        def __init__(self) -> None:
            self.api_key = "stale"
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

        def _chat(self, **kwargs: object) -> dict[str, str]:
            del kwargs
            return {"id": "ok"}

    OpenAI.__module__ = "openai"
    provider = OpenAI()
    client.wrap(provider).chat.completions.create(model="gpt-4.1-mini")
    client.close()
    assert connection.bodies == []
    assert provider.api_key == "stale"


class _Connection:
    """Stand-in control plane that records issue bodies."""

    def __init__(self) -> None:
        self.bodies: list[object] = []

    def send(self, method: str, path: str, body: object | None = None) -> httpx.Response:
        del method
        self.bodies.append(body)
        return httpx.Response(
            200,
            json={
                "provider": "openai",
                "api_key": _ISSUED,
                "expires_at": "2026-11-08T00:02:00.000Z",
            },
            request=httpx.Request("POST", "http://127.0.0.1" + path),
        )

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


def _client(tmp_path: Path) -> tuple[Tessera, Path]:
    path = tmp_path / "audit.jsonl"
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"models": {"allow": ["openai/*"]}},
        }
    )
    return Tessera("bot", policy=policy, audit_path=path), path
