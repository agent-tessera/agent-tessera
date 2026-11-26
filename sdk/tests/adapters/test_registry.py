"""Process-global adapter registry for custom `wrap` targets."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.adapters import (
    _ensure_builtins,
    clear_adapters,
    governed_method,
    listed_adapters,
    register_adapter,
    unregister_adapter,
    wrap,
)
from agent_tessera.adapters._builtins import builtin_adapters
from agent_tessera.errors import ConfigError, Denied


@dataclass
class _EchoClient:
    """Minimal client with a single complete method."""

    last_model: str | None = None

    def complete(self, *, model: str, prompt: str = "") -> dict[str, object]:
        """Record the model and return a tiny usage payload.

        Args:
            model: Model name without provider prefix.
            prompt: Unused prompt text.

        Returns:
            A dict shaped like a chat completion usage blob.
        """
        del prompt
        self.last_model = model
        return {"usage": {"prompt_tokens": 1, "completion_tokens": 2}}


class _EchoAdapter:
    """Adapter that governs `_EchoClient.complete`."""

    def matches(self, client: object) -> bool:
        return isinstance(client, _EchoClient)

    def paths(self, client: object) -> tuple[tuple[str, ...], ...]:
        del client
        return (("complete",),)

    def bind(
        self,
        tessera: Tessera,
        client: object,
    ) -> Callable[[tuple[str, ...], object], object]:
        del client

        def binder(_path: tuple[str, ...], method: object) -> object:
            return governed_method(
                tessera,
                method,
                prefix="echo",
                style="tokens",
                asynchronous=False,
            )

        return binder


def _policy(*models: str) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "test"},
            "spec": {
                "models": {
                    "allow": list(models),
                    "deny": ["echo/blocked/*"],
                }
            },
        }
    )


def _tessera(tmp_path: Path, *models: str) -> Tessera:
    return Tessera(
        "bot",
        policy=_policy(*models),
        audit_path=tmp_path / "audit.jsonl",
    )


@pytest.fixture
def registry() -> Iterator[None]:
    """Isolate the process registry around each test."""
    clear_adapters()
    yield
    clear_adapters()


def test_custom_adapter_governs_complete(registry: None, tmp_path: Path) -> None:
    del registry
    adapter = _EchoAdapter()
    register_adapter(adapter)
    client = _tessera(tmp_path, "echo/*")
    try:
        governed = wrap(client, _EchoClient())
        result = governed.complete(model="tiny", prompt="hi")
        assert result["usage"]["prompt_tokens"] == 1
        with pytest.raises(Denied):
            governed.complete(model="blocked/secret")
    finally:
        client.close()


def test_prepend_puts_adapter_first(registry: None) -> None:
    del registry
    first = _EchoAdapter()
    second = _EchoAdapter()
    register_adapter(first, prepend=False)
    register_adapter(second, prepend=True)
    adapters = listed_adapters()
    assert adapters[0] is second
    assert adapters[1] is first


def test_unregister_restores_unsupported(registry: None, tmp_path: Path) -> None:
    del registry
    adapter = _EchoAdapter()
    register_adapter(adapter)
    client = _tessera(tmp_path, "echo/*")
    try:
        wrap(client, _EchoClient())
        unregister_adapter(adapter)
        with pytest.raises(ConfigError, match=r"@tessera\.model_call"):
            wrap(client, _EchoClient())
    finally:
        client.close()


def test_builtins_reload_after_clear(registry: None) -> None:
    del registry
    _ensure_builtins()
    assert len(listed_adapters()) == len(builtin_adapters())
    clear_adapters()
    assert listed_adapters() == ()
    _ensure_builtins()
    assert len(listed_adapters()) == len(builtin_adapters())


def test_unregister_missing_raises(registry: None) -> None:
    del registry
    with pytest.raises(ValueError, match="not registered"):
        unregister_adapter(_EchoAdapter())
