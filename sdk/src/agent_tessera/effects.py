"""Classify tool effects and remember what a run changed."""

from __future__ import annotations

from dataclasses import dataclass

from agent_tessera.errors import ConfigError
from agent_tessera.policy.effective import EffectivePolicy
from agent_tessera.policy.matching import matches_pattern

_REF_LIMIT = 500
_UNDO_LIMIT = 200


@dataclass(frozen=True, slots=True)
class EffectRecord:
    """One opaque change a run reported.

    Attributes:
        ref: Caller-supplied id, such as `zendesk:ticket/123`.
        reversible: Whether the caller says the change can be undone.
        undo: Name of an undo tool. `None` when the caller did not name one.
        run_id: Task that was current. `None` outside a task.

    Example:
        ```python
        record = EffectRecord("zendesk:ticket/123", True, "zendesk.reopen", "run-1")
        assert record.ref == "zendesk:ticket/123"
        ```
    """

    ref: str
    reversible: bool
    undo: str | None
    run_id: str | None


class EffectLedger:
    """In-memory list of effects recorded by one client.

    Example:
        ```python
        ledger = EffectLedger()
        ledger.append("zendesk:ticket/123", reversible=True, undo=None, run_id="run-1")
        assert ledger.records[0].ref == "zendesk:ticket/123"
        ```
    """

    def __init__(self) -> None:
        """Start with an empty list."""
        self._records: list[EffectRecord] = []

    @property
    def records(self) -> tuple[EffectRecord, ...]:
        """Return the recorded effects, oldest first.

        Returns:
            A tuple the caller cannot mutate.
        """
        return tuple(self._records)

    def append(
        self,
        ref: str,
        *,
        reversible: bool,
        undo: str | None,
        run_id: str | None,
    ) -> EffectRecord:
        """Remember one effect.

        Args:
            ref: Opaque reference. It must be a non-empty string of at most
                500 characters.
            reversible: Whether the caller says the change can be undone.
            undo: Undo tool name, or `None`. A blank string is stored as `None`.
            run_id: Current task id, or `None`.

        Returns:
            The stored record.

        Raises:
            ConfigError: If `ref` or `undo` is not usable.
        """
        checked = _ref(ref)
        named = _undo(undo)
        record = EffectRecord(checked, reversible, named, run_id)
        self._records.append(record)
        return record


def classify_effect(policy: EffectivePolicy | None, resource: str) -> tuple[str, ...]:
    """Return the effect tags for a tool resource.

    Args:
        policy: Effective policy, or `None` when none is loaded.
        resource: Tool name.

    Returns:
        Sorted tags from every matching classification. `unknown` when no
        pattern matches.

    Example:
        ```python
        assert classify_effect(None, "files.read") == ("unknown",)
        ```
    """
    if policy is None:
        return ("unknown",)
    found: set[str] = set()
    matched = False
    for pattern, tags, _rule in policy.controls.effect_classify:
        if matches_pattern(resource, pattern):
            matched = True
            found.update(tags)
    if not matched:
        return ("unknown",)
    return tuple(sorted(found))


def _ref(value: str) -> str:
    """Reject a reference that is empty or too long.

    Args:
        value: Submitted reference.

    Returns:
        The same reference.

    Raises:
        ConfigError: If it is empty or longer than 500 characters.
    """
    if value == "" or len(value) > _REF_LIMIT:
        raise ConfigError("effect ref must be 1 to 500 characters")
    return value


def _undo(value: str | None) -> str | None:
    """Reject an undo name that is too long.

    Args:
        value: Submitted undo tool. `None` and `""` store no name.

    Returns:
        The name to store, or `None`.

    Raises:
        ConfigError: If the name is longer than 200 characters.
    """
    if value is None or value == "":
        return None
    if len(value) > _UNDO_LIMIT:
        raise ConfigError("effect undo must be at most 200 characters")
    return value


__all__ = ["EffectLedger", "EffectRecord", "classify_effect"]
