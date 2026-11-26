"""Built-in adapters registered for `wrap` at import time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from agent_tessera.adapters.anthropic import bind_method as bind_anthropic
from agent_tessera.adapters.anthropic import provider_for as anthropic_provider
from agent_tessera.adapters.bedrock import bind_method as bind_bedrock
from agent_tessera.adapters.bedrock import provider_for as bedrock_provider
from agent_tessera.adapters.gemini import bind_method as bind_gemini
from agent_tessera.adapters.gemini import provider_for as gemini_provider
from agent_tessera.adapters.litellm import bind_method as bind_litellm
from agent_tessera.adapters.litellm import provider_for as litellm_provider
from agent_tessera.adapters.openai import bind_method as bind_openai
from agent_tessera.adapters.openai import provider_for as openai_provider

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

_Detect = Callable[[object], Any | None]
_Binder = Callable[["Tessera", Any], Callable[[tuple[str, ...], object], object]]


@dataclass(frozen=True, slots=True)
class _ProviderAdapter:
    """Adapter that delegates to a provider_for / bind_method pair.

    Args:
        detect: Returns a provider rule or `None`.
        binder: Builds the method binder from a provider rule.
    """

    detect: _Detect
    binder: _Binder

    def matches(self, client: object) -> bool:
        """Return whether the provider recognizes `client`.

        Args:
            client: Object passed to `wrap`.

        Returns:
            `True` when `detect` returns a rule.
        """
        return self.detect(client) is not None

    def paths(self, client: object) -> tuple[tuple[str, ...], ...]:
        """Return intercepted paths from the provider rule.

        Args:
            client: Matched provider client.

        Returns:
            Attribute paths to wrap.
        """
        provider = self.detect(client)
        if provider is None:
            return ()
        return cast(tuple[tuple[str, ...], ...], provider.paths)

    def bind(
        self,
        tessera: Tessera,
        client: object,
    ) -> Callable[[tuple[str, ...], object], object]:
        """Return the provider binder for `client`.

        Args:
            tessera: Client that enforces model calls.
            client: Matched provider client.

        Returns:
            A binder from path to governed callable.

        Raises:
            RuntimeError: When `client` no longer matches (should not happen).
        """
        provider = self.detect(client)
        if provider is None:
            raise RuntimeError("adapter bind called for a non-matching client")
        return self.binder(tessera, provider)


def builtin_adapters() -> tuple[_ProviderAdapter, ...]:
    """Return the built-in adapters in default match order.

    Returns:
        OpenAI, Gemini, Bedrock, LiteLLM, then Anthropic (same order as the
        pre-registry `wrap` chain).
    """
    return (
        _ProviderAdapter(openai_provider, bind_openai),
        _ProviderAdapter(gemini_provider, bind_gemini),
        _ProviderAdapter(bedrock_provider, bind_bedrock),
        _ProviderAdapter(litellm_provider, bind_litellm),
        _ProviderAdapter(anthropic_provider, bind_anthropic),
    )


__all__ = ["builtin_adapters"]
