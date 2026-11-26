"""Hooks for the OpenAI Agents SDK.

`TesseraRunHooks` has the same `on_tool_start` and `on_llm_start` methods as
`agents.RunHooks`. Pass `build_hooks(tessera)` to `Runner.run` when the
`openai-agents` extra is installed. The hook enforces the tool or model and
raises `Denied` before the SDK continues.
"""

from __future__ import annotations

import importlib
from typing import cast

from agent_tessera.client import Tessera
from agent_tessera.types import Action

_EXTRA = "agent-tessera[openai-agents]"


class TesseraRunHooks:
    """Enforce tool and model calls from an Agents SDK run.

    Args:
        tessera: Client that enforces the call.
    """

    def __init__(self, tessera: Tessera) -> None:
        """Store the client.

        Args:
            tessera: Client that enforces the call.
        """
        self._tessera = tessera

    async def on_tool_start(self, context: object, agent: object, tool: object) -> None:
        """Enforce the tool before the SDK runs it.

        Args:
            context: SDK run context. Unused.
            agent: SDK agent. Unused.
            tool: Tool object. `name` is the policy tool name.

        Raises:
            Denied: If the policy denies the tool.
        """
        del context, agent
        name = getattr(tool, "name", None)
        tool_name = name if isinstance(name, str) and name != "" else "unknown"
        self._tessera.enforce(Action.tool(tool_name))

    async def on_llm_start(
        self,
        context: object,
        agent: object,
        system_prompt: object,
        input_items: object,
    ) -> None:
        """Enforce the model before the SDK calls it.

        Args:
            context: SDK run context. Unused.
            agent: SDK agent. `model` is the model name.
            system_prompt: Prompt text. Unused.
            input_items: Model input. Unused.

        Raises:
            Denied: If the policy denies the model.
        """
        del context, system_prompt, input_items
        model = getattr(agent, "model", None)
        text = model if isinstance(model, str) and model != "" else "unknown"
        if "/" not in text:
            text = f"openai/{text}"
        self._tessera.enforce(Action.model(text))


def build_hooks(tessera: Tessera) -> TesseraRunHooks:
    """Return hooks that also subclass `agents.RunHooks`.

    Args:
        tessera: Client that enforces the call.

    Returns:
        A `TesseraRunHooks` instance that is a `RunHooks` subclass.

    Raises:
        ImportError: If the `openai-agents` extra is not installed.

    Example:
        ```python
        hooks = build_hooks(tessera)
        ```
    """
    try:
        agents = importlib.import_module("agents")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to use Runner hooks") from err
    run_hooks = agents.RunHooks

    def _init(self: TesseraRunHooks, client: Tessera) -> None:
        TesseraRunHooks.__init__(self, client)
        run_hooks.__init__(self)

    hooks_type = type("TesseraBoundHooks", (TesseraRunHooks, run_hooks), {"__init__": _init})
    return cast(TesseraRunHooks, hooks_type(tessera))


def import_openai_agents() -> object:
    """Import the Agents SDK, or raise a message that names the extra.

    Returns:
        The `agents` module.

    Raises:
        ImportError: If the `openai-agents` extra is not installed.

    Example:
        ```python
        module = import_openai_agents()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module("agents")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern the OpenAI Agents SDK") from err


__all__ = ["TesseraRunHooks", "build_hooks", "import_openai_agents"]
