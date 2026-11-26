"""Govern Anthropic clients without importing the SDK."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_tessera.adapters._proxy import governed_method

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

_PATHS = (("messages", "create"),)


@dataclass(frozen=True, slots=True)
class AnthropicProvider:
    """How to govern one Anthropic client.

    Attributes:
        prefix: Resource prefix, always `anthropic`.
        asynchronous: Whether intercepted methods are awaited.
        paths: Attribute paths to wrap.
    """

    prefix: str
    asynchronous: bool
    paths: tuple[tuple[str, ...], ...]


def provider_for(client: object) -> AnthropicProvider | None:
    """Return the governing rule for an Anthropic client.

    Args:
        client: Object the caller wants to wrap.

    Returns:
        The provider rule, or `None` when the object is not an Anthropic client.
    """
    if type(client).__module__.split(".")[0] != "anthropic":
        return None
    name = type(client).__name__
    if name == "Anthropic":
        asynchronous = False
    elif name == "AsyncAnthropic":
        asynchronous = True
    else:
        return None
    return AnthropicProvider("anthropic", asynchronous, _PATHS)


def bind_method(
    tessera: Tessera,
    provider: AnthropicProvider,
) -> Callable[[tuple[str, ...], object], object]:
    """Build the function that wraps `messages.create`.

    Args:
        tessera: Client that enforces the call.
        provider: Rule for this Anthropic client.

    Returns:
        A binder from a method path to a governed callable.
    """

    def bind(_path: tuple[str, ...], method: object) -> object:
        return governed_method(
            tessera,
            method,
            prefix=provider.prefix,
            style="tokens",
            asynchronous=provider.asynchronous,
        )

    return bind


__all__ = ["AnthropicProvider", "bind_method", "provider_for"]
