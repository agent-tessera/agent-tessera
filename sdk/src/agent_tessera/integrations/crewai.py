"""Enforce CrewAI tool runs.

`govern_tool` replaces `_run` with a Tessera tool wrapper. A denial is raised
before the original run.
"""

from __future__ import annotations

import importlib
from typing import Protocol, cast

from agent_tessera.client import Tessera
from agent_tessera.errors import ConfigError

_EXTRA = "agent-tessera[crewai]"


class _Tool(Protocol):
    """A CrewAI tool whose run method can be replaced."""

    name: str
    _run: object


def govern_tool(tessera: Tessera, tool: object) -> object:
    """Wrap a CrewAI tool's `_run` method.

    Args:
        tessera: Client that enforces the call.
        tool: Object with `name` and `_run`.

    Returns:
        The same tool. `_run` is replaced.

    Raises:
        ConfigError: If `name` or `_run` is missing.

    Example:
        ```python
        govern_tool(tessera, tool)
        tool._run("hello")
        ```
    """
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or name == "":
        raise ConfigError("CrewAI tool needs a name")
    run = getattr(tool, "_run", None)
    if not callable(run):
        raise ConfigError("CrewAI tool needs _run")
    target = cast(_Tool, tool)
    target._run = tessera.tool(name)(run)
    return tool


def import_crewai() -> object:
    """Import CrewAI, or raise a message that names the extra.

    Returns:
        The `crewai` module.

    Raises:
        ImportError: If the `crewai` extra is not installed.

    Example:
        ```python
        module = import_crewai()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module("crewai")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern CrewAI tools") from err


__all__ = ["govern_tool", "import_crewai"]
