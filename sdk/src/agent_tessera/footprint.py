"""Compare an effective policy's grants with a local audit log."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime

from agent_tessera.policy.effective import EffectivePolicy
from agent_tessera.policy.matching import matches_pattern
from agent_tessera.types import ActionKind

_KINDS = tuple(kind.value for kind in ActionKind)
_OBSERVED_EFFECTS = frozenset({"allow", "require_approval"})


@dataclass(frozen=True, slots=True)
class DeclaredGrant:
    """One grant pattern from the effective policy.

    Attributes:
        kind: Action kind, such as `tool`.
        pattern: Shell-style glob.
        rule: Policy identifier, such as `agent:tools.allow[0]`.
    """

    kind: str
    pattern: str
    rule: str


@dataclass(frozen=True, slots=True)
class CountedResource:
    """How often one resource appeared in the audit window.

    Attributes:
        kind: Action kind, such as `tool`.
        resource: Resource name from the event.
        count: Number of matching events.
    """

    kind: str
    resource: str
    count: int


@dataclass(frozen=True, slots=True)
class Footprint:
    """Grants, what was used, what was not, and what was refused.

    Attributes:
        declared: Allow patterns and granting approval patterns.
        observed: Resources from allowing decisions and from call events.
        unused: Declared patterns that matched no observed resource.
        denied: Resources refused because they were not granted.
    """

    declared: tuple[DeclaredGrant, ...]
    observed: tuple[CountedResource, ...]
    unused: tuple[DeclaredGrant, ...]
    denied: tuple[CountedResource, ...]

    def as_dict(self) -> dict[str, object]:
        """Return the JSON object for this footprint.

        Returns:
            Keys `declared`, `observed`, `unused`, and `denied`.
        """
        return asdict(self)


def compute_footprint(
    policy: EffectivePolicy,
    events: Iterable[Mapping[str, object]],
    *,
    since: datetime,
) -> Footprint:
    """Summarize grants and audit events at or after `since`.

    Args:
        policy: Effective policy whose grants are the declared set.
        events: Audit records. Events for another agent, or with no timestamp,
            are skipped.
        since: Include events at or after this time. Must be timezone-aware.

    Returns:
        Declared grants, observed resources, unused grants, and denials for
        `not_granted`.

    Example:
        ```python
        from datetime import UTC, datetime

        from agent_tessera.footprint import compute_footprint
        from agent_tessera.policy.effective import build_effective_policy
        from agent_tessera.policy.models import AgentPolicy

        policy = AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {"tools": {"allow": ["files.*"]}},
            }
        )
        report = compute_footprint(
            build_effective_policy(policy),
            [],
            since=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert report.declared[0].pattern == "files.*"
        assert report.observed == ()
        ```
    """
    declared = _declared(policy)
    observed_counts: Counter[tuple[str, str]] = Counter()
    denied_counts: Counter[tuple[str, str]] = Counter()
    for event in events:
        if not _selected(event, policy.agent, since):
            continue
        event_type = event.get("type")
        if event_type == "decision":
            _tally_decision(event, observed_counts, denied_counts)
        elif event_type == "call":
            _tally_named(event.get("kind"), event.get("resource"), observed_counts)
    observed = _counted(observed_counts)
    return Footprint(
        declared=declared,
        observed=observed,
        unused=tuple(grant for grant in declared if not _matched(grant, observed)),
        denied=_counted(denied_counts),
    )


def _declared(policy: EffectivePolicy) -> tuple[DeclaredGrant, ...]:
    """List allow patterns and granting approval patterns.

    Args:
        policy: Effective policy.

    Returns:
        Grants in kind order, allows before granting approvals.
    """
    grants: list[DeclaredGrant] = []
    for kind in ActionKind:
        grants.extend(
            DeclaredGrant(kind.value, rule.pattern, rule.rule) for rule in policy.allow[kind]
        )
        grants.extend(
            DeclaredGrant(kind.value, rule.pattern, rule.rule)
            for rule in policy.approvals[kind]
            if rule.grants
        )
    return tuple(grants)


def _selected(event: Mapping[str, object], agent: str, since: datetime) -> bool:
    """Report whether an event belongs in the window.

    Args:
        event: One audit record.
        agent: Agent the report is about.
        since: Earliest timestamp to include.

    Returns:
        `True` when the event names this agent, or no agent, and its timestamp
        is at or after `since`.
    """
    named = event.get("agent")
    if isinstance(named, str) and named != agent:
        return False
    moment = _timestamp(event.get("ts"))
    return moment is not None and moment >= since


def _tally_decision(
    event: Mapping[str, object],
    observed: Counter[tuple[str, str]],
    denied: Counter[tuple[str, str]],
) -> None:
    """Count one decision toward observed use or a not-granted denial.

    Args:
        event: A `decision` audit record.
        observed: Counts of allowed or approval-required resources.
        denied: Counts of resources refused as not granted.
    """
    action = event.get("action")
    if not isinstance(action, Mapping):
        return
    kind = action.get("kind")
    resource = action.get("resource")
    if event.get("effect") in _OBSERVED_EFFECTS:
        _tally_named(kind, resource, observed)
    if event.get("reason") == "not_granted":
        _tally_named(kind, resource, denied)


def _tally_named(kind: object, resource: object, counts: Counter[tuple[str, str]]) -> None:
    """Add one resource when the kind and name are usable.

    Args:
        kind: Action kind string.
        resource: Resource name.
        counts: Counter to update.
    """
    if isinstance(kind, str) and kind in _KINDS and isinstance(resource, str) and resource:
        counts[(kind, resource)] += 1


def _counted(counts: Counter[tuple[str, str]]) -> tuple[CountedResource, ...]:
    """Sort resource counts by kind and name.

    Args:
        counts: Tallies keyed by kind and resource.

    Returns:
        One row per resource.
    """
    ordered = sorted(counts)
    return tuple(
        CountedResource(kind, resource, counts[(kind, resource)]) for kind, resource in ordered
    )


def _matched(grant: DeclaredGrant, observed: tuple[CountedResource, ...]) -> bool:
    """Report whether any observed resource of this kind matches the grant.

    Args:
        grant: Declared pattern.
        observed: Resources seen in the window.

    Returns:
        `True` when at least one resource matches the glob.
    """
    return any(
        item.kind == grant.kind and matches_pattern(item.resource, grant.pattern)
        for item in observed
    )


def _timestamp(value: object) -> datetime | None:
    """Parse an audit timestamp.

    Args:
        value: ISO-8601 string, possibly ending in `Z`.

    Returns:
        A timezone-aware time, or `None` when the value is not one.
    """
    if not isinstance(value, str) or value == "":
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment


__all__ = ["CountedResource", "DeclaredGrant", "Footprint", "compute_footprint"]
