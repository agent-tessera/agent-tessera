"""Wrap provider SDK clients so their model calls are governed."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast

from agent_tessera.adapters._builtins import builtin_adapters
from agent_tessera.adapters._proxy import GovernedProxy, governed_method
from agent_tessera.adapters.registry import (
    builtins_are_loaded,
    clear_adapters,
    listed_adapters,
    mark_builtins_loaded,
    register_adapter,
    unregister_adapter,
)
from agent_tessera.adapters.types import Adapter
from agent_tessera.errors import ConfigError

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

T = TypeVar("T")


def _ensure_builtins() -> None:
    """Register built-in adapters once per process."""
    if builtins_are_loaded():
        return
    for adapter in builtin_adapters():
        register_adapter(adapter, prepend=False)
    mark_builtins_loaded(True)


def wrap(tessera: Tessera, client: T) -> T:
    """Return a governed proxy for a supported or registered provider client.

    Args:
        tessera: Client that enforces model calls.
        client: Provider SDK client.

    Returns:
        A proxy with the provider client's interface.

    Raises:
        ConfigError: If no registered adapter matches the client.

    Example:
        ```python
        governed = wrap(tessera, client)
        governed.chat.completions.create(model="gpt-4.1-mini")
        ```
    """
    _ensure_builtins()
    for adapter in listed_adapters():
        if not adapter.matches(client):
            continue
        proxy = GovernedProxy(
            client,
            (),
            adapter.paths(client),
            adapter.bind(tessera, client),
        )
        return cast(T, proxy)
    module = type(client).__module__
    name = type(client).__name__
    message = f"Unsupported client {module}.{name}; use @tessera.model_call instead"
    raise ConfigError(message)


_ensure_builtins()

__all__ = [
    "Adapter",
    "clear_adapters",
    "governed_method",
    "listed_adapters",
    "register_adapter",
    "unregister_adapter",
    "wrap",
]
