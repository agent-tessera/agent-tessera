"""Govern LiteLLM completion calls without importing LiteLLM."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_tessera.adapters._proxy import governed_method

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

_PATHS = (("completion",), ("acompletion",))
_EXTRA = "agent-tessera[litellm]"


@dataclass(frozen=True, slots=True)
class LiteLLMProvider:
    """How to govern one LiteLLM client.

    Attributes:
        prefix: Resource prefix, always `litellm`.
        paths: Attribute paths to wrap.
    """

    prefix: str
    paths: tuple[tuple[str, ...], ...]


def provider_for(client: object) -> LiteLLMProvider | None:
    """Return the governing rule for a LiteLLM client.

    Args:
        client: Object the caller wants to wrap.

    Returns:
        The provider rule, or `None` when the object is not a LiteLLM client.
    """
    if type(client).__module__.split(".", maxsplit=1)[0] != "litellm":
        return None
    return LiteLLMProvider("litellm", _PATHS)


def bind_method(
    tessera: Tessera,
    provider: LiteLLMProvider,
) -> Callable[[tuple[str, ...], object], object]:
    """Build the function that wraps one intercepted method.

    Args:
        tessera: Client that enforces the call.
        provider: Rule for this provider client.

    Returns:
        A binder from a method path to a governed callable.
    """

    def bind(path: tuple[str, ...], method: object) -> object:
        return governed_method(
            tessera,
            method,
            prefix=provider.prefix,
            style="chat",
            asynchronous=path == ("acompletion",),
        )

    return bind


def import_litellm() -> object:
    """Import LiteLLM, or raise a message that names the extra.

    Returns:
        The `litellm` module.

    Raises:
        ImportError: If the `litellm` extra is not installed.

    Example:
        ```python
        module = import_litellm()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module("litellm")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern LiteLLM calls") from err


__all__ = ["LiteLLMProvider", "bind_method", "import_litellm", "provider_for"]
