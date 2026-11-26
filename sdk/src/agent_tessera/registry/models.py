"""YAML schema for a local agent registry."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent_tessera.errors import PolicyError
from agent_tessera.policy.loader import _format_validation_error, _read_document
from agent_tessera.policy.models import SLUG, RiskTier

AgentStatus = Literal["draft", "active", "suspended", "retired"]
Autonomy = Literal["assistive", "interactive", "supervised", "autonomous"]

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RegistryEntry(BaseModel):
    """One agent recorded in a local registry.

    Attributes:
        name: Agent name.
        environment: Environment name. Defaults to `dev`.
        owner: Email address or team id.
        status: Lifecycle status.
        policy: Policy path relative to the registry file, when one is set.
        description: Short description.
        risk_tier: Risk tier.
        labels: Free-form labels.
        autonomy: How independently the agent acts.
        supervisors: Supervisor identifiers.
        registered_at: When the store first wrote the entry, in UTC.
        updated_at: When the store last wrote the entry, in UTC.

    Example:
        ```python
        entry = RegistryEntry(name="helper", owner="dev@localhost")
        assert entry.status == "draft"
        assert entry.environment == "dev"
        ```
    """

    model_config = _MODEL_CONFIG

    name: str = Field(pattern=SLUG)
    environment: str = Field(default="dev", pattern=SLUG)
    owner: str = Field(min_length=1)
    status: AgentStatus = "draft"
    policy: str | None = None
    description: str = ""
    risk_tier: RiskTier = "low"
    labels: dict[str, str] = Field(default_factory=dict)
    autonomy: Autonomy = "interactive"
    supervisors: tuple[str, ...] = ()
    registered_at: datetime | None = None
    updated_at: datetime | None = None


class AgentRegistry(BaseModel):
    """A file that lists agents and the baselines that apply to them.

    Attributes:
        api_version: Always `tessera/v1`.
        kind: Always `AgentRegistry`.
        baselines: Baseline paths relative to the registry file.
        agents: Registered agents. Each name and environment pair is unique.

    Example:
        ```python
        registry = AgentRegistry.model_validate(
            {"apiVersion": "tessera/v1", "kind": "AgentRegistry"}
        )
        assert registry.agents == ()
        ```
    """

    model_config = _MODEL_CONFIG

    api_version: Literal["tessera/v1"] = Field(alias="apiVersion")
    kind: Literal["AgentRegistry"]
    baselines: tuple[str, ...] = ()
    agents: tuple[RegistryEntry, ...] = ()

    @model_validator(mode="after")
    def _unique_agents(self) -> AgentRegistry:
        """Reject two entries that share a name and environment.

        Returns:
            This registry.

        Raises:
            ValueError: If a name and environment pair is repeated. The
                message names the duplicate.
        """
        seen: set[tuple[str, str]] = set()
        for entry in self.agents:
            key = (entry.name, entry.environment)
            if key in seen:
                raise ValueError(f"duplicate agent {entry.name} ({entry.environment})")
            seen.add(key)
        return self


def load_registry(path: str | Path) -> AgentRegistry:
    """Read one registry document from disk.

    Args:
        path: Registry file.

    Returns:
        The registry.

    Raises:
        PolicyError: If the file cannot be read, the YAML is invalid, or the
            document is not one registry. The error's `path` is this file.
            A missing file is an error. `LocalRegistry.load` treats a missing
            file as an empty registry instead.

    Example:
        ```python
        from agent_tessera.registry import load_registry

        registry = load_registry("policies/examples/registry.yaml")
        assert registry.kind == "AgentRegistry"
        ```
    """
    file_path = Path(path)
    try:
        return AgentRegistry.model_validate(_read_document(file_path))
    except ValidationError as err:
        raise PolicyError(_format_validation_error(err), path=file_path) from err


__all__ = ["AgentRegistry", "AgentStatus", "Autonomy", "RegistryEntry", "load_registry"]
