"""Process-global registry of LLM client adapters for `wrap`."""

from __future__ import annotations

from agent_tessera.adapters.types import Adapter

_adapters: list[Adapter] = []
_state: dict[str, bool] = {"builtins_loaded": False}


def mark_builtins_loaded(loaded: bool = True) -> None:
    """Record whether built-in adapters are present in the registry.

    Args:
        loaded: New flag value.
    """
    _state["builtins_loaded"] = loaded


def builtins_are_loaded() -> bool:
    """Return whether built-ins have been registered this process.

    Returns:
        `True` after the first successful builtin registration.
    """
    return _state["builtins_loaded"]


def register_adapter(adapter: Adapter, *, prepend: bool = True) -> None:
    """Register an adapter for `wrap` to consider.

    Args:
        adapter: Object implementing the `Adapter` Protocol.
        prepend: When `True` (default), insert at the front so the adapter
            can override builtins. When `False`, append (used for builtins).

    Example:
        ```python
        from agent_tessera.adapters import register_adapter

        register_adapter(MyAdapter())
        ```
    """
    if prepend:
        _adapters.insert(0, adapter)
    else:
        _adapters.append(adapter)


def unregister_adapter(adapter: Adapter) -> None:
    """Remove a previously registered adapter by identity.

    Args:
        adapter: The same object passed to `register_adapter`.

    Raises:
        ValueError: When `adapter` is not in the registry.
    """
    try:
        _adapters.remove(adapter)
    except ValueError as err:
        raise ValueError("adapter is not registered") from err


def listed_adapters() -> tuple[Adapter, ...]:
    """Return the current adapters in match order.

    Returns:
        A snapshot of the registry (first match wins in `wrap`).
    """
    return tuple(_adapters)


def clear_adapters() -> None:
    """Remove every adapter. Intended for tests only.

    The next `wrap` call re-registers the built-in adapters.
    """
    _adapters.clear()
    mark_builtins_loaded(False)


__all__ = [
    "clear_adapters",
    "listed_adapters",
    "register_adapter",
    "unregister_adapter",
]
