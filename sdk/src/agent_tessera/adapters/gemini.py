"""Govern Google GenAI clients without importing the SDK."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_tessera.adapters._proxy import governed_method

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

_PATHS = (("models", "generate_content"),)
_EXTRA = "agent-tessera[gemini]"
_MODULE = "google.genai"


@dataclass(frozen=True, slots=True)
class GeminiProvider:
    """How to govern one Google GenAI client.

    Attributes:
        prefix: Resource prefix, always `gemini`.
        paths: Attribute paths to wrap.
    """

    prefix: str
    paths: tuple[tuple[str, ...], ...]


def provider_for(client: object) -> GeminiProvider | None:
    """Return the governing rule for a Google GenAI client.

    Args:
        client: Object the caller wants to wrap.

    Returns:
        The provider rule, or `None` when the object is not a GenAI client.
    """
    if not type(client).__module__.startswith(_MODULE):
        return None
    return GeminiProvider("gemini", _PATHS)


def bind_method(
    tessera: Tessera,
    provider: GeminiProvider,
) -> Callable[[tuple[str, ...], object], object]:
    """Build the function that wraps one intercepted method.

    Args:
        tessera: Client that enforces the call.
        provider: Rule for this provider client.

    Returns:
        A binder from a method path to a governed callable.
    """

    def bind(_path: tuple[str, ...], method: object) -> object:
        return governed_method(
            tessera,
            method,
            prefix=provider.prefix,
            style="gemini",
            asynchronous=False,
        )

    return bind


def import_gemini() -> object:
    """Import Google GenAI, or raise a message that names the extra.

    Returns:
        The `google.genai` module.

    Raises:
        ImportError: If the `gemini` extra is not installed.

    Example:
        ```python
        module = import_gemini()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module(_MODULE)
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern Gemini calls") from err


__all__ = ["GeminiProvider", "bind_method", "import_gemini", "provider_for"]
