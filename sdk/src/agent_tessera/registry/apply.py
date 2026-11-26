"""Plan and apply a desired registry against a local file or a server."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agent_tessera.policy.loader import load_baseline_policy
from agent_tessera.registry.backends import LocalBackend, RegistryBackend, ServerBackend
from agent_tessera.registry.models import AgentRegistry, AgentStatus, RegistryEntry
from agent_tessera.registry.store import _TRANSITIONS

_METADATA = ("owner", "description", "risk_tier", "labels", "autonomy", "supervisors")


@dataclass(frozen=True)
class Change:
    """One difference between a registry file and a backend.

    Attributes:
        action: `baseline`, `register`, `update`, `policy`, `status`,
            `unchanged`, `error`, or `unmanaged`.
        name: Agent or baseline name.
        environment: Agent environment. Empty for a baseline.
        fields: Metadata fields an update will replace.
        entry: Agent to register, or the desired agent for an update.
        policy_path: Policy file to upload.
        status: Status to store.
        old_status: Status before a change.
        message: Explanation for an error.
        baseline_path: Baseline file to upload.

    Example:
        ```python
        change = Change("unchanged", name="helper", environment="dev")
        assert change.action == "unchanged"
        ```
    """

    action: str
    name: str = ""
    environment: str = ""
    fields: tuple[str, ...] = ()
    entry: RegistryEntry | None = None
    policy_path: Path | None = None
    status: str | None = None
    old_status: str | None = None
    message: str = ""
    baseline_path: Path | None = None


@dataclass(frozen=True)
class ApplyResult:
    """The changes that were applied.

    Attributes:
        changes: The plan, including errors that were not applied.
        errors: How many changes are errors.

    Example:
        ```python
        result = ApplyResult(changes=(), errors=0)
        assert result.errors == 0
        ```
    """

    changes: tuple[Change, ...]
    errors: int


def plan(desired: AgentRegistry, base_dir: Path, backend: RegistryBackend) -> list[Change]:
    """Describe how to make a backend match a registry file.

    Args:
        desired: Registry document.
        base_dir: Directory the file's policy and baseline paths are relative to.
        backend: Local file or control plane to compare.

    Returns:
        Baseline uploads, then one agent's changes in file order, then agents
        that exist only on the backend.
    """
    current = {(item.name, item.environment): item for item in backend.list()}
    changes = _baseline_changes(desired.baselines, base_dir, backend)
    wanted: set[tuple[str, str]] = set()
    for entry in desired.agents:
        wanted.add((entry.name, entry.environment))
        found = current.get((entry.name, entry.environment))
        changes.extend(_agent_changes(entry, found, base_dir, backend))
    for key, entry in current.items():
        if key not in wanted:
            changes.append(Change("unmanaged", name=entry.name, environment=entry.environment))
    return changes


def apply_plan(changes: list[Change], backend: RegistryBackend) -> ApplyResult:
    """Perform a plan.

    Args:
        changes: Changes from `plan`, in the order they must run.
        backend: Backend to change.

    Returns:
        The same changes and how many were errors.
    """
    errors = 0
    for change in changes:
        if change.action == "error":
            errors += 1
            continue
        _apply_one(change, backend)
    return ApplyResult(changes=tuple(changes), errors=errors)


def render(change: Change) -> str:
    """Format one agent change for the command output.

    Args:
        change: Change to print. Baseline uploads have no line.

    Returns:
        `<name> (<env>)` followed by the change.
    """
    prefix = f"{change.name} ({change.environment})"
    detail = {
        "register": "+ register",
        "update": f"~ update {', '.join(change.fields)}",
        "policy": "~ policy",
        "status": f"~ status {change.old_status} -> {change.status}",
        "unchanged": "= unchanged",
        "unmanaged": "? unmanaged",
    }.get(change.action, f"! error {change.message}")
    return f"{prefix} {detail}"


def _baseline_changes(
    baselines: tuple[str, ...],
    base_dir: Path,
    backend: RegistryBackend,
) -> list[Change]:
    """Plan baseline uploads whose stored content differs.

    Args:
        baselines: Paths relative to `base_dir`, in file order.
        base_dir: Directory those paths are relative to.
        backend: Backend to compare.

    Returns:
        One change per baseline that would be written.
    """
    changes: list[Change] = []
    for relative in baselines:
        path = _source(base_dir, relative)
        loaded = load_baseline_policy(path)
        if _baseline_differs(backend, loaded.metadata.name, path):
            changes.append(Change("baseline", name=loaded.metadata.name, baseline_path=path))
    return changes


def _agent_changes(
    desired: RegistryEntry,
    current: RegistryEntry | None,
    base_dir: Path,
    backend: RegistryBackend,
) -> list[Change]:
    """Plan the changes for one agent.

    Args:
        desired: Entry from the file.
        current: Stored entry, or `None` when the agent is missing.
        base_dir: Directory policy paths are relative to.
        backend: Backend to compare.

    Returns:
        The changes for this agent, or one error when the status cannot move.
    """
    policy = None if desired.policy is None else _source(base_dir, desired.policy)
    if current is None:
        return _create_changes(desired, policy)
    has_policy = policy is not None or current.policy is not None
    blocked = _status_error(desired.name, current.status, desired.status, has_policy)
    if blocked is not None:
        return [
            Change(
                "error",
                name=desired.name,
                environment=desired.environment,
                message=blocked,
            )
        ]
    changes: list[Change] = []
    fields = _metadata_fields(current, desired)
    if fields:
        changes.append(
            Change(
                "update",
                name=desired.name,
                environment=desired.environment,
                fields=fields,
                entry=desired,
            )
        )
    if policy is not None and _policy_differs(backend, current, policy):
        changes.append(
            Change("policy", name=desired.name, environment=desired.environment, policy_path=policy)
        )
    if current.status != desired.status:
        changes.append(
            Change(
                "status",
                name=desired.name,
                environment=desired.environment,
                status=desired.status,
                old_status=current.status,
            )
        )
    if not changes:
        changes.append(Change("unchanged", name=desired.name, environment=desired.environment))
    return changes


def _create_changes(desired: RegistryEntry, policy: Path | None) -> list[Change]:
    """Plan creation of a missing agent.

    Args:
        desired: Entry from the file.
        policy: Policy file, when the entry names one.

    Returns:
        A register, and a policy or status change when needed. An unreachable
        status is an error and does not register the agent.
    """
    blocked = _status_error(desired.name, "draft", desired.status, policy is not None)
    if blocked is not None:
        return [
            Change(
                "error",
                name=desired.name,
                environment=desired.environment,
                message=blocked,
            )
        ]
    draft = desired.model_copy(
        update={"status": "draft", "policy": None, "registered_at": None, "updated_at": None}
    )
    changes = [Change("register", name=desired.name, environment=desired.environment, entry=draft)]
    if policy is not None:
        changes.append(
            Change("policy", name=desired.name, environment=desired.environment, policy_path=policy)
        )
    if desired.status != "draft":
        changes.append(
            Change(
                "status",
                name=desired.name,
                environment=desired.environment,
                status=desired.status,
                old_status="draft",
            )
        )
    return changes


def _apply_one(change: Change, backend: RegistryBackend) -> None:
    """Perform one change that is not an error.

    Args:
        change: Change to perform.
        backend: Backend to change.
    """
    if change.action == "baseline" and change.baseline_path is not None:
        backend.put_baseline(change.name, change.baseline_path)
    elif change.action == "register" and change.entry is not None:
        backend.register(change.entry)
    elif change.action == "update" and change.entry is not None:
        values = {field: getattr(change.entry, field) for field in change.fields}
        backend.update(change.name, change.environment, **values)
    elif change.action == "policy" and change.policy_path is not None:
        backend.set_policy(change.name, change.environment, change.policy_path)
    elif change.action == "status" and change.status is not None:
        backend.set_status(change.name, change.environment, _status(change.status))


def _metadata_fields(current: RegistryEntry, desired: RegistryEntry) -> tuple[str, ...]:
    """Return metadata fields whose values differ.

    Args:
        current: Stored entry.
        desired: Entry from the file.

    Returns:
        Field names, sorted.
    """
    return tuple(
        field for field in sorted(_METADATA) if getattr(current, field) != getattr(desired, field)
    )


def _status_error(name: str, current: str, desired: str, has_policy: bool) -> str | None:
    """Return why a status change is impossible.

    Args:
        name: Agent name.
        current: Status before the change. A missing agent is treated as `draft`.
        desired: Status in the file.
        has_policy: Whether the agent will have a policy before the change.

    Returns:
        The error text, or `None` when the change is allowed or unnecessary.
    """
    if current == desired:
        return None
    if desired not in _TRANSITIONS[current]:
        return f"{name}: cannot change status from {current} to {desired}"
    if desired == "active" and not has_policy:
        return f"{name}: activation needs a policy"
    return None


def _policy_differs(backend: RegistryBackend, current: RegistryEntry, source: Path) -> bool:
    """Report whether a policy file differs from the stored policy.

    Args:
        backend: Backend to compare.
        current: Stored entry.
        source: Policy file from the registry document.

    Returns:
        `True` when the local path differs, or the server's YAML hash differs.
    """
    if isinstance(backend, LocalBackend):
        return current.policy != backend.stored_path(source)
    if isinstance(backend, ServerBackend):
        stored = backend.policy_bytes(current.name, current.environment)
        return stored is None or _digest(stored) != _digest(source.read_bytes())
    return True


def _baseline_differs(backend: RegistryBackend, name: str, source: Path) -> bool:
    """Report whether a baseline file differs from the stored baseline.

    Args:
        backend: Backend to compare.
        name: Baseline name.
        source: Baseline file.

    Returns:
        `True` when the local path is not recorded, or the server's YAML hash
        differs.
    """
    if isinstance(backend, LocalBackend):
        return backend.stored_path(source) not in backend.baselines()
    if isinstance(backend, ServerBackend):
        stored = backend.baseline_bytes(name)
        return stored is None or _digest(stored) != _digest(source.read_bytes())
    return True


def _source(base_dir: Path, relative: str) -> Path:
    """Resolve a path from a registry file.

    Args:
        base_dir: Directory the registry file is in.
        relative: Path stored in the file. Absolute paths are kept.

    Returns:
        The file to read.
    """
    path = Path(relative)
    if path.is_absolute():
        return path
    return base_dir / path


def _digest(content: bytes) -> str:
    """Hash YAML bytes.

    Args:
        content: File or response body.

    Returns:
        The SHA-256 hex digest.
    """
    return hashlib.sha256(content).hexdigest()


def _status(value: str) -> AgentStatus:
    """Return a status literal.

    Args:
        value: Status stored on a change.

    Returns:
        The same status.
    """
    if value == "active":
        return "active"
    if value == "suspended":
        return "suspended"
    if value == "retired":
        return "retired"
    return "draft"


__all__ = ["ApplyResult", "Change", "apply_plan", "plan", "render"]
