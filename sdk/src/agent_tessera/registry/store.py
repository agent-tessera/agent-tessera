"""File-backed registry with an exclusive lock and a hash-chained audit log."""

from __future__ import annotations

import getpass
import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from agent_tessera.audit import AuditLog
from agent_tessera.errors import PolicyError, RegistryError
from agent_tessera.policy.loader import load_agent_policy
from agent_tessera.registry.models import AgentRegistry, AgentStatus, RegistryEntry, load_registry

_LOCK_WAIT = 5.0
_LOCK_STALE = 30.0
_RETRY = 0.05
_MUTABLE = frozenset(
    {"owner", "policy", "description", "risk_tier", "labels", "autonomy", "supervisors"}
)
_AUDITED = (
    "name",
    "environment",
    "owner",
    "status",
    "policy",
    "description",
    "risk_tier",
    "labels",
    "autonomy",
    "supervisors",
)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active"}),
    "active": frozenset({"suspended", "retired"}),
    "suspended": frozenset({"active", "retired"}),
    "retired": frozenset(),
}


def _utc_now() -> datetime:
    """Return the current time in UTC.

    Returns:
        An aware timestamp.
    """
    return datetime.now(UTC)


class LocalRegistry:
    """Read and update one registry file.

    Writes take an exclusive lock, re-read the file, and replace it
    atomically. Every change is appended to `registry-audit.jsonl` beside
    the file.

    Args:
        path: Registry file. A missing file is read as an empty registry.
        now: Clock stored on `registered_at` and `updated_at`.

    Example:
        ```python
        from agent_tessera.registry import LocalRegistry, RegistryEntry

        registry = LocalRegistry(".tessera/registry.yaml")
        registry.register(RegistryEntry(name="helper", owner="dev@localhost"))
        assert registry.find("helper", "dev") is not None
        ```
    """

    def __init__(self, path: str | Path, *, now: Callable[[], datetime] = _utc_now) -> None:
        """Store the path and the clock.

        Args:
            path: Registry file.
            now: Clock stored on `registered_at` and `updated_at`.
        """
        self._path = Path(path)
        self._now = now

    @property
    def path(self) -> Path:
        """Return the registry file path.

        Returns:
            The path passed to the constructor.
        """
        return self._path

    def load(self) -> AgentRegistry:
        """Read the registry.

        Returns:
            The file's contents. A missing file is an empty registry.

        Raises:
            PolicyError: If the file exists and is not a registry document.
        """
        if not self._path.is_file():
            return AgentRegistry.model_validate(
                {"apiVersion": "tessera/v1", "kind": "AgentRegistry"}
            )
        return load_registry(self._path)

    def find(self, name: str, environment: str) -> RegistryEntry | None:
        """Return the entry for one agent, if it is registered.

        Args:
            name: Agent name.
            environment: Environment name.

        Returns:
            The entry, or `None`.
        """
        for entry in self.load().agents:
            if entry.name == name and entry.environment == environment:
                return entry
        return None

    def register(self, entry: RegistryEntry) -> RegistryEntry:
        """Add an agent.

        Args:
            entry: Agent to add. Timestamps are filled by this store.

        Returns:
            The stored entry.

        Raises:
            RegistryError: If the agent is already registered, the policy does
                not match, or an active entry has no policy.
        """
        if entry.status == "active" and entry.policy is None:
            raise RegistryError(f"{entry.name}: activation needs a policy")
        self._check_policy(entry)

        def change(current: AgentRegistry) -> tuple[AgentRegistry, Mapping[str, object]]:
            if any(
                item.name == entry.name and item.environment == entry.environment
                for item in current.agents
            ):
                raise RegistryError(
                    f"agent {entry.name} ({entry.environment}) is already registered"
                )
            moment = self._now()
            stored = entry.model_copy(update={"registered_at": moment, "updated_at": moment})
            return (
                current.model_copy(update={"agents": (*current.agents, stored)}),
                {
                    "action": "register",
                    "agent": stored.name,
                    "environment": stored.environment,
                    "changes": _created(stored),
                },
            )

        return self._commit(entry.name, entry.environment, change)

    def update(self, name: str, environment: str, **changes: object) -> RegistryEntry:
        """Replace metadata fields on one agent.

        Args:
            name: Agent name.
            environment: Environment name.
            **changes: Fields to replace. `status` is changed with
                `set_status`. Timestamps are not accepted.

        Returns:
            The stored entry.

        Raises:
            RegistryError: If the agent is missing, a field is unknown, or the
                new policy does not match the agent.
        """
        unknown = set(changes) - _MUTABLE
        if unknown:
            name_text = ", ".join(sorted(unknown))
            raise RegistryError(f"{name}: unknown field {name_text}")

        def change(current: AgentRegistry) -> tuple[AgentRegistry, Mapping[str, object]]:
            entry = _require(current, name, environment)
            updated = _apply(entry, changes, self._now())
            self._check_policy(updated)
            return (
                _replace(current, updated),
                {
                    "action": "update",
                    "agent": name,
                    "environment": environment,
                    "changes": _diff(entry, updated),
                },
            )

        return self._commit(name, environment, change)

    def set_status(self, name: str, environment: str, status: AgentStatus) -> RegistryEntry:
        """Move an agent to another lifecycle status.

        Args:
            name: Agent name.
            environment: Environment name.
            status: Status to store.

        Returns:
            The stored entry.

        Raises:
            RegistryError: If the agent is missing, the transition is not
                allowed, or activation has no policy.
        """

        def change(current: AgentRegistry) -> tuple[AgentRegistry, Mapping[str, object]]:
            entry = _require(current, name, environment)
            _require_transition(entry, status)
            updated = entry.model_copy(update={"status": status, "updated_at": self._now()})
            return (
                _replace(current, updated),
                {
                    "action": "status",
                    "agent": name,
                    "environment": environment,
                    "changes": {"status": [entry.status, status]},
                },
            )

        return self._commit(name, environment, change)

    def resolve_policy(self, entry: RegistryEntry) -> Path | None:
        """Return the policy file for an entry.

        Args:
            entry: Registry entry.

        Returns:
            The path relative to the registry file, or `None` when the entry
            has no policy.
        """
        if entry.policy is None:
            return None
        return self._path.parent / entry.policy

    def resolve_baselines(self) -> tuple[Path, ...]:
        """Return the baseline files listed by the registry.

        Returns:
            Paths relative to the registry file.
        """
        return tuple(self._path.parent / item for item in self.load().baselines)

    def _check_policy(self, entry: RegistryEntry) -> None:
        """Reject a policy that does not belong to this entry.

        Args:
            entry: Entry whose policy path should be loaded.

        Raises:
            RegistryError: If the file is not that agent's policy. The message
                is the policy error text when the file cannot be loaded.
        """
        path = self.resolve_policy(entry)
        if path is None:
            return
        try:
            loaded = load_agent_policy(path)
        except PolicyError as err:
            raise RegistryError(str(err)) from err
        if loaded.metadata.agent != entry.name or loaded.metadata.environment != entry.environment:
            raise RegistryError(
                f"{entry.name}: policy agent {loaded.metadata.agent} "
                f"({loaded.metadata.environment}) does not match"
            )

    def _commit(
        self,
        name: str,
        environment: str,
        change: Callable[[AgentRegistry], tuple[AgentRegistry, Mapping[str, object]]],
    ) -> RegistryEntry:
        """Lock, re-read, write, and append the audit event.

        Args:
            name: Agent the caller asked for.
            environment: Environment the caller asked for.
            change: Build the next registry and the audit fields.

        Returns:
            The stored entry.
        """
        with self._exclusive():
            updated, fields = change(self.load())
            self._write(updated)
            _append_audit(self._path.parent / "registry-audit.jsonl", fields)
            return _require(updated, name, environment)

    def _write(self, registry: AgentRegistry) -> None:
        """Replace the file with a deterministic document.

        Args:
            registry: Registry to store. Agents are sorted first.
        """
        agents = tuple(sorted(registry.agents, key=lambda item: (item.environment, item.name)))
        ordered = registry.model_copy(update={"agents": agents})
        text = yaml.safe_dump(
            ordered.model_dump(mode="json", by_alias=True),
            sort_keys=False,
            allow_unicode=True,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(str(self._path) + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self._path)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Hold the registry lock.

        Yields:
            Control while the lock file exists.

        Raises:
            RegistryError: If the lock is still held after five seconds.
        """
        self._acquire()
        try:
            yield
        finally:
            self._lock_path.unlink(missing_ok=True)

    def _acquire(self) -> None:
        """Create the lock file, removing a stale one.

        Raises:
            RegistryError: If the lock is still held after five seconds.
        """
        deadline = time.monotonic() + _LOCK_WAIT
        while True:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if _stale(self._lock_path):
                    self._lock_path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise RegistryError("registry is locked") from None
                time.sleep(_RETRY)
                continue
            os.close(fd)
            return

    @property
    def _lock_path(self) -> Path:
        """Return the lock file path.

        Returns:
            `<registry>.lock`.
        """
        return Path(str(self._path) + ".lock")


def _require(registry: AgentRegistry, name: str, environment: str) -> RegistryEntry:
    """Return one entry or raise.

    Args:
        registry: Current registry.
        name: Agent name.
        environment: Environment name.

    Returns:
        The matching entry.

    Raises:
        RegistryError: If the agent is not registered.
    """
    for entry in registry.agents:
        if entry.name == name and entry.environment == environment:
            return entry
    raise RegistryError(f"agent {name} ({environment}) is not registered")


def _require_transition(entry: RegistryEntry, status: AgentStatus) -> None:
    """Reject a lifecycle edge the server would reject.

    Args:
        entry: Current entry.
        status: Requested status.

    Raises:
        RegistryError: If the edge is not allowed, or activation has no policy.
    """
    if status not in _TRANSITIONS[entry.status]:
        raise RegistryError(f"{entry.name}: cannot change status from {entry.status} to {status}")
    if status == "active" and entry.policy is None:
        raise RegistryError(f"{entry.name}: activation needs a policy")


def _apply(entry: RegistryEntry, changes: Mapping[str, object], moment: datetime) -> RegistryEntry:
    """Copy an entry with new field values.

    Args:
        entry: Current entry.
        changes: Replacement values.
        moment: Timestamp stored on `updated_at`.

    Returns:
        The updated entry.
    """
    data = entry.model_dump()
    for key, value in changes.items():
        data[key] = tuple(value) if key == "supervisors" and isinstance(value, list) else value
    data["updated_at"] = moment
    return RegistryEntry.model_validate(data)


def _replace(registry: AgentRegistry, entry: RegistryEntry) -> AgentRegistry:
    """Substitute one entry, matched by name and environment.

    Args:
        registry: Current registry.
        entry: Replacement entry.

    Returns:
        A registry containing the replacement.
    """
    agents = tuple(
        entry if item.name == entry.name and item.environment == entry.environment else item
        for item in registry.agents
    )
    return registry.model_copy(update={"agents": agents})


def _created(entry: RegistryEntry) -> dict[str, list[object]]:
    """Build the audit changes for a new entry.

    Args:
        entry: Stored entry.

    Returns:
        Each audited field as `[None, new]`. Timestamps are omitted.
    """
    dumped = entry.model_dump(mode="json")
    return {field: [None, _plain(dumped[field])] for field in _AUDITED}


def _diff(before: RegistryEntry, after: RegistryEntry) -> dict[str, list[object]]:
    """Build the audit changes for fields that differ.

    Args:
        before: Entry before the write.
        after: Entry after the write.

    Returns:
        Changed fields as `[old, new]`. Timestamps are omitted.
    """
    old = before.model_dump(mode="json")
    new = after.model_dump(mode="json")
    return {
        field: [_plain(old[field]), _plain(new[field])]
        for field in _AUDITED
        if old[field] != new[field]
    }


def _plain(value: object) -> object:
    """Return a JSON value.

    Args:
        value: Field value from `model_dump`.

    Returns:
        The same value. Tuples are lists.
    """
    if isinstance(value, tuple):
        return list(value)
    return value


def _append_audit(path: Path, fields: Mapping[str, object]) -> None:
    """Append one registry event and close the log.

    Args:
        path: Audit file.
        fields: Event fields, without the actor.
    """
    payload = dict(fields)
    payload["actor"] = _actor()
    log = AuditLog(path)
    try:
        log.write("registry", payload)
    finally:
        log.close()


def _actor() -> str:
    """Return the OS user, or `unknown`.

    Returns:
        The login name, or `unknown` when it cannot be read.
    """
    try:
        return getpass.getuser()
    except OSError:
        return "unknown"


def _stale(path: Path) -> bool:
    """Return whether a lock file is older than 30 seconds.

    Args:
        path: Lock file.

    Returns:
        `True` when the file is missing or its age is over 30 seconds.
    """
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return True
    return datetime.now(UTC) - modified > timedelta(seconds=_LOCK_STALE)
