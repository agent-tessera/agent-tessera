"""Wrap LangChain tools and LangGraph node functions.

A LangGraph node is an ordinary function. Pass it through `wrap_tool` before
you add it to the graph. `govern` does the same for a tool object that has
`name` and `func` or `coroutine`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Protocol, TypeVar, cast

from agent_tessera.client import Tessera
from agent_tessera.errors import ConfigError

F = TypeVar("F", bound=Callable[..., object])
_EXTRA = "agent-tessera[langchain]"


class _MutableTool(Protocol):
    """A tool whose callables can be replaced."""

    name: str
    func: object
    coroutine: object


def wrap_tool(tessera: Tessera, name: str, func: F) -> F:
    """Enforce a tool or LangGraph node function.

    Args:
        tessera: Client that enforces the call.
        name: Tool name in the policy.
        func: Sync or async function.

    Returns:
        The same function, wrapped so each call is enforced.

    Example:
        ```python
        read = wrap_tool(tessera, "files.read", read_file)
        ```
    """
    return tessera.tool(name)(func)


def govern(tessera: Tessera, tool: object) -> object:
    """Wrap a LangChain tool's callable attributes.

    Args:
        tessera: Client that enforces the call.
        tool: Object with `name` and `func` or `coroutine`.

    Returns:
        The same tool. `func` and `coroutine` are replaced when they are callable.

    Raises:
        ConfigError: If the tool has no name.

    Example:
        ```python
        govern(tessera, tool)
        tool.func("hello")
        ```
    """
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or name == "":
        raise ConfigError("LangChain tool needs a name")
    target = cast(_MutableTool, tool)
    func = getattr(tool, "func", None)
    if callable(func):
        target.func = tessera.tool(name)(func)
    coroutine = getattr(tool, "coroutine", None)
    if callable(coroutine):
        target.coroutine = tessera.tool(name)(coroutine)
    return tool


def import_langchain() -> object:
    """Import LangChain core tools, or raise a message that names the extra.

    Returns:
        The `langchain_core.tools` module.

    Raises:
        ImportError: If the `langchain` extra is not installed.

    Example:
        ```python
        module = import_langchain()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module("langchain_core.tools")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern LangChain tools") from err


__all__ = ["govern", "import_langchain", "wrap_tool"]
