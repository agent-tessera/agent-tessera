"""Roll up token, cost, call, and denial totals from stored events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.errors import PolicyError
from agent_tessera.footprint import compute_footprint
from agent_tessera.policy.effective import EffectivePolicy, build_effective_policy
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.pricing.energy import EnergyTable, estimate_energy, load_energy_table
from tessera_server.api.errors import ApiError
from tessera_server.db.models import Agent, Baseline, Event, Policy, utcnow
from tessera_server.services.supervision import _one_document


def footprint_totals(
    session: Session,
    *,
    agent_id: str | None,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, object]:
    """Sum resource use for one agent, or for every agent.

    Args:
        session: Open database session.
        agent_id: Agent to total. `None` includes every agent.
        start: Include events at or after this time. `None` has no lower bound.
        end: Include events at or before this time. `None` has no upper bound.

    Returns:
        `tokens_in`, `tokens_out`, `cost_usd`, `model_calls`, `tool_calls`,
        `denials`, `energy_j`, `co2e_g`, and `estimate`. Energy is `null`
        when a usage event names a model the energy table does not list.

    Raises:
        ApiError: Status 404 when `agent_id` is set and the agent does not
            exist, or 422 when `end` is before `start`.

    Example:
        ```python
        totals = footprint_totals(session, agent_id=None, start=None, end=None)
        assert totals["denials"] >= 0
        ```
    """
    if start is not None and end is not None and end < start:
        raise ApiError(422, "invalid_request", "to is before from")
    if agent_id is not None and session.get(Agent, agent_id) is None:
        raise ApiError(404, "not_found", "agent not found")
    query = select(Event)
    if agent_id is not None:
        query = query.where(Event.agent_id == agent_id)
    if start is not None:
        query = query.where(Event.ts >= start)
    if end is not None:
        query = query.where(Event.ts <= end)
    tokens_in = 0
    tokens_out = 0
    cost = 0.0
    model_calls = 0
    tool_calls = 0
    denials = 0
    energy = 0.0
    carbon = 0.0
    energy_known = True
    table = load_energy_table()
    for row in session.scalars(query).all():
        payload = row.payload
        if row.event_type == "usage":
            tokens_in += _whole(payload.get("input_tokens"))
            tokens_out += _whole(payload.get("output_tokens"))
            cost += _number(payload.get("cost_usd"))
            estimated = _usage_energy(payload, table)
            if estimated is None:
                energy_known = False
            else:
                energy += estimated[0]
                carbon += estimated[1]
        elif row.event_type == "call" and payload.get("kind") == "model":
            model_calls += 1
        elif row.event_type == "call" and payload.get("kind") == "tool":
            tool_calls += 1
        elif row.event_type == "decision" and payload.get("effect") == "deny":
            denials += 1
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost, 6),
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "denials": denials,
        "energy_j": None if not energy_known else round(energy, 6),
        "co2e_g": None if not energy_known else round(carbon, 6),
        "estimate": True,
    }


def _usage_energy(payload: dict[str, object], table: EnergyTable) -> tuple[float, float] | None:
    """Estimate one usage event.

    Args:
        payload: Stored usage event.
        table: Energy table from `load_energy_table`.

    Returns:
        Joules and grams, or `None` when the model is missing or unknown.
    """
    model = payload.get("model")
    if not isinstance(model, str):
        return None
    return estimate_energy(
        model,
        _whole(payload.get("input_tokens")),
        _whole(payload.get("output_tokens")),
        table,
    )


_ACCESS_WINDOW = timedelta(days=30)


def access_footprint(
    session: Session,
    agent_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Compare declared grants with events from the last 30 days.

    Args:
        session: Open database session.
        agent_id: Agent to report.
        now: Clock for the window. Defaults to the current time.

    Returns:
        `declared`, `observed`, `unused`, and `denied`, using the same rules
        as `tessera footprint`. An agent with no readable policy has empty
        lists.

    Raises:
        ApiError: Status 404 when the agent does not exist.

    Example:
        ```python
        report = access_footprint(session, agent_id)
        assert "unused" in report
        ```
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(404, "not_found", "agent was not found")
    policy = _effective(session, agent)
    if policy is None:
        return {"declared": [], "observed": [], "unused": [], "denied": []}
    moment = now or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    report = compute_footprint(policy, _audit_events(session, agent), since=moment - _ACCESS_WINDOW)
    return report.as_dict()


def _effective(session: Session, agent: Agent) -> EffectivePolicy | None:
    """Build the effective policy, or return `None` when it cannot be read.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.

    Returns:
        The effective policy, or `None`.
    """
    if agent.policy_id is None:
        return None
    stored = session.get(Policy, agent.policy_id)
    if stored is None:
        return None
    try:
        document = parse_policy(_one_document(stored.yaml))
        if not isinstance(document, AgentPolicy):
            return None
        return build_effective_policy(document, _baselines(session))
    except (PolicyError, ApiError):
        return None


def _baselines(session: Session) -> list[BaselinePolicy]:
    """Load stored baselines in name order.

    Args:
        session: Open database session.

    Returns:
        Parsed baselines. An unreadable baseline is skipped.
    """
    rows = session.scalars(select(Baseline).order_by(Baseline.name)).all()
    parsed: list[BaselinePolicy] = []
    for row in rows:
        try:
            document = parse_policy(_one_document(row.yaml))
        except (PolicyError, ApiError):
            continue
        if isinstance(document, BaselinePolicy):
            parsed.append(document)
    return parsed


def _audit_events(session: Session, agent: Agent) -> list[dict[str, object]]:
    """Return stored events in the shape the offline footprint reads.

    Args:
        session: Open database session.
        agent: Agent whose events to read.

    Returns:
        Payload copies with `type`, `agent`, and an ISO-8601 `ts`.
    """
    rows = session.scalars(select(Event).where(Event.agent_id == agent.id)).all()
    events: list[dict[str, object]] = []
    for row in rows:
        payload = dict(row.payload)
        payload.setdefault("type", row.event_type)
        payload.setdefault("agent", agent.name)
        if not isinstance(payload.get("ts"), str):
            moment = row.ts
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            payload["ts"] = moment.astimezone(UTC).isoformat()
        events.append(payload)
    return events


def _whole(value: object) -> int:
    """Read a non-negative integer from an event field.

    Args:
        value: Field value. Floats are truncated. Other types count as zero.

    Returns:
        The integer, or zero.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def _number(value: object) -> float:
    """Read a finite number from an event field.

    Args:
        value: Field value. Numeric strings are accepted.

    Returns:
        The number, or zero.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


__all__ = ["access_footprint", "footprint_totals"]
