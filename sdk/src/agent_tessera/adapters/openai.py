"""Govern OpenAI and Azure OpenAI clients without importing the SDK."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_tessera.adapters._proxy import governed_method

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

_SYNC = frozenset({"OpenAI", "AzureOpenAI"})
_ASYNC = frozenset({"AsyncOpenAI", "AsyncAzureOpenAI"})
_PATHS = (
    ("chat", "completions", "create"),
    ("responses", "create"),
)


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    """How to govern one OpenAI-compatible client.

    Attributes:
        prefix: Resource prefix, `openai` or `azure`.
        asynchronous: Whether intercepted methods are awaited.
        paths: Attribute paths to wrap.
    """

    prefix: str
    asynchronous: bool
    paths: tuple[tuple[str, ...], ...]


def provider_for(client: object) -> OpenAIProvider | None:
    """Return the governing rule for an OpenAI or Azure client.

    Args:
        client: Object the caller wants to wrap.

    Returns:
        The provider rule, or `None` when the object is not an OpenAI client.
    """
    if type(client).__module__.split(".")[0] != "openai":
        return None
    name = type(client).__name__
    if name in _SYNC:
        asynchronous = False
    elif name in _ASYNC:
        asynchronous = True
    else:
        return None
    prefix = "azure" if "Azure" in name else "openai"
    return OpenAIProvider(prefix, asynchronous, _PATHS)


def bind_method(
    tessera: Tessera,
    provider: OpenAIProvider,
) -> Callable[[tuple[str, ...], object], object]:
    """Build the function that wraps one intercepted method.

    Args:
        tessera: Client that enforces the call.
        provider: Rule for this provider client.

    Returns:
        A binder from a method path to a governed callable.
    """

    def bind(path: tuple[str, ...], method: object) -> object:
        style = "chat" if path[:2] == ("chat", "completions") else "tokens"
        return governed_method(
            tessera,
            method,
            prefix=provider.prefix,
            style=style,
            asynchronous=provider.asynchronous,
        )

    return bind


__all__ = ["OpenAIProvider", "bind_method", "provider_for"]
