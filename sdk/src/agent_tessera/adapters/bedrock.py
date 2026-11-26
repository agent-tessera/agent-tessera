"""Govern Amazon Bedrock runtime clients without importing boto3."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_tessera.adapters._proxy import governed_method

if TYPE_CHECKING:
    from agent_tessera.client import Tessera

_PATHS = (("converse",), ("invoke_model",))
_EXTRA = "agent-tessera[bedrock]"
_MODEL_KEY = "modelId"


@dataclass(frozen=True, slots=True)
class BedrockProvider:
    """How to govern one Bedrock runtime client.

    Attributes:
        prefix: Resource prefix, always `bedrock`.
        paths: Attribute paths to wrap.
    """

    prefix: str
    paths: tuple[tuple[str, ...], ...]


def provider_for(client: object) -> BedrockProvider | None:
    """Return the governing rule for a Bedrock runtime client.

    Args:
        client: Object the caller wants to wrap.

    Returns:
        The provider rule, or `None` when the object is not a Bedrock client.
    """
    module = type(client).__module__
    if not module.startswith("botocore"):
        return None
    if "Bedrock" not in type(client).__name__ and not _runtime(client):
        return None
    return BedrockProvider("bedrock", _PATHS)


def bind_method(
    tessera: Tessera,
    provider: BedrockProvider,
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
            style="tokens",
            asynchronous=False,
            model_key=_MODEL_KEY,
        )

    return bind


def import_bedrock() -> object:
    """Import boto3, or raise a message that names the extra.

    Returns:
        The `boto3` module.

    Raises:
        ImportError: If the `bedrock` extra is not installed.

    Example:
        ```python
        module = import_bedrock()
        assert module is not None
        ```
    """
    try:
        return importlib.import_module("boto3")
    except ImportError as err:
        raise ImportError(f"Install {_EXTRA} to govern Bedrock calls") from err


def _runtime(client: object) -> bool:
    """Return whether a botocore client is the Bedrock runtime.

    Args:
        client: Client object.

    Returns:
        `True` when the service name is `bedrock-runtime`.
    """
    meta = getattr(client, "meta", None)
    service = getattr(getattr(meta, "service_model", None), "service_name", None)
    return service == "bedrock-runtime"


__all__ = ["BedrockProvider", "bind_method", "import_bedrock", "provider_for"]
