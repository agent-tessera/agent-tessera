"""Enforce MCP client tool calls.

`GovernedSession` forwards `call_tool` through the Tessera tool decorator.
A denial is raised before the underlying session is called.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, cast

from agent_tessera.client import Tessera


class _Caller(Protocol):
    """An MCP session that can call a tool."""

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        """Call one tool."""
        ...


_EXTRA = "agent-tessera[mcp]"


class GovernedSession:
    """A session whose tool calls are enforced.

    Args:
        tessera: Client that enforces the call.
        session: Object with `call_tool(name, arguments)`.
    """

    def __init__(self, tessera: Tessera, session: object) -> None:
        """Store the client and the underlying session.

        Args:
            tessera: Client that enforces the call.
            session: Object with `call_tool(name, arguments)`.
        """
        self._tessera = tessera
        self._session = cast(_Caller, session)

    def call_tool(self, name: str, arguments: Mapping[str, object] | None = None) -> object:
        """Enforce one tool call and then forward it.

        Args:
            name: Tool name in the policy and on the session.
            arguments: Arguments forwarded to the session. `None` sends an
                empty mapping.

        Returns:
            Whatever the session returns. When the session method is a
            coroutine function, this returns a coroutine.

        Example:
            ```python
            governed = GovernedSession(tessera, session)
            governed.call_tool("files.read", {"path": "notes.txt"})
            ```
        """
        method = cast(Callable[..., object], self._session.call_tool)
        payload: Mapping[str, object] = {} if arguments is None else arguments
        if inspect.iscoroutinefunction(method):
            async_method = cast(Callable[..., Awaitable[object]], method)

            async def async_invoke() -> object:
                return await async_method(name, payload)

            return self._tessera.tool(name)(async_invoke)()

        def sync_invoke() -> object:
            return method(name, payload)

        return self._tessera.tool(name)(sync_invoke)()


def import_mcp() -> object:
    """Import the MCP SDK, or raise a message that names the extra.

    Returns:
        The `mcp` module.

    Raises:
        ImportError: If the `mcp` extra is not installed.

    Example:
        ```python
        module = import_mcp()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module("mcp")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern MCP tool calls") from err


__all__ = ["GovernedSession", "import_mcp"]
