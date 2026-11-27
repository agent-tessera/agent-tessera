"""Agent Tessera SDK: identity, permissions, and governance for AI agents.

The SDK gives an agent a verifiable identity, enforces its YAML policy on model
and tool calls, and records an audit trail, regardless of which LLM provider it
uses.

Public names are imported the first time they are used, so importing this
package does not load the client, the policy schema, or the HTTP stack.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_tessera.client import Tessera
    from agent_tessera.context import Task
    from agent_tessera.errors import (
        ApprovalRequired,
        ConfigError,
        Denied,
        GovernanceError,
        PolicyError,
        RegistryError,
        ServerError,
        TesseraError,
    )
    from agent_tessera.policy import AgentPolicy, BaselinePolicy, load_policy
    from agent_tessera.registry import AgentRegistry, LocalRegistry
    from agent_tessera.types import Action, ActionKind, Decision, Effect, Principal, Reason

__version__ = "0.9.0"

__all__ = [
    "Action",
    "ActionKind",
    "AgentPolicy",
    "AgentRegistry",
    "ApprovalRequired",
    "BaselinePolicy",
    "ConfigError",
    "Decision",
    "Denied",
    "Effect",
    "GovernanceError",
    "LocalRegistry",
    "PolicyError",
    "Principal",
    "Reason",
    "RegistryError",
    "ServerError",
    "Task",
    "Tessera",
    "TesseraError",
    "__version__",
    "load_policy",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "Action": ("agent_tessera.types", "Action"),
    "ActionKind": ("agent_tessera.types", "ActionKind"),
    "AgentPolicy": ("agent_tessera.policy", "AgentPolicy"),
    "AgentRegistry": ("agent_tessera.registry", "AgentRegistry"),
    "ApprovalRequired": ("agent_tessera.errors", "ApprovalRequired"),
    "BaselinePolicy": ("agent_tessera.policy", "BaselinePolicy"),
    "ConfigError": ("agent_tessera.errors", "ConfigError"),
    "Decision": ("agent_tessera.types", "Decision"),
    "Denied": ("agent_tessera.errors", "Denied"),
    "Effect": ("agent_tessera.types", "Effect"),
    "GovernanceError": ("agent_tessera.errors", "GovernanceError"),
    "LocalRegistry": ("agent_tessera.registry", "LocalRegistry"),
    "PolicyError": ("agent_tessera.errors", "PolicyError"),
    "RegistryError": ("agent_tessera.errors", "RegistryError"),
    "Principal": ("agent_tessera.types", "Principal"),
    "Reason": ("agent_tessera.types", "Reason"),
    "ServerError": ("agent_tessera.errors", "ServerError"),
    "Task": ("agent_tessera.context", "Task"),
    "Tessera": ("agent_tessera.client", "Tessera"),
    "TesseraError": ("agent_tessera.errors", "TesseraError"),
    "load_policy": ("agent_tessera.policy", "load_policy"),
}


def __getattr__(name: str) -> object:
    """Import a public name the first time it is used.

    Args:
        name: Attribute requested on this package.

    Returns:
        The exported object.

    Raises:
        AttributeError: If `name` is not a public export.
    """
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    value: object = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List the public names, including those not imported yet.

    Returns:
        Sorted attribute names.
    """
    return sorted(set(globals()) | set(__all__))
