"""Inventory, spend, and denial totals for the dashboards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import Agent, Event, utcnow
from tessera_server.services.overview import overview_counts
from tessera_server.services.registry import _orphaned

_DAY = timedelta(hours=24)
_DORMANT = timedelta(days=7)
_GROUPS = frozenset({"agent", "model"})


def dashboard_summary(session: Session, *, now: datetime | None = None) -> dict[str, object]:
    """Summarize inventory, quiet agents, and the last 24 hours.

    Args:
        session: Open database session.
        now: Clock for the windows. Defaults to the current time. Spend and
            denials use the inclusive window `[now - 24h, now]`, matching the
            dashboard UI pages.

    Returns:
        Inventory by status, orphaned and dormant counts, pending approvals,
        spend, and denials. Dormant agents are not retired and have no event
        in the last seven days.

    Example:
        ```python
        body = dashboard_summary(session)
        assert body["pending_approvals"] >= 0
        ```
    """
    moment = _aware(now or utcnow())
    summary = overview_counts(session, now=moment)
    spend = 0.0
    denials = 0
    for row in _events(session, moment - _DAY, moment):
        if row.event_type == "usage":
            spend += _number(row.payload.get("cost_usd"))
        elif row.event_type == "decision" and row.payload.get("effect") == "deny":
            denials += 1
    return {
        "inventory": summary["agents"],
        "orphaned": _orphaned_count(session),
        "dormant": _dormant_count(session, moment),
        "pending_approvals": summary["pending_approvals"],
        "spend_24h": round(spend, 6),
        "denials_24h": denials,
    }


def dashboard_spend(
    session: Session,
    *,
    start: datetime | None,
    end: datetime | None,
    group_by: str,
) -> dict[str, object]:
    """Sum usage cost, grouped by agent or model.

    Args:
        session: Open database session.
        start: Include events at or after this time.
        end: Include events at or before this time.
        group_by: `agent` or `model`.

    Returns:
        The grouping and one row per key, with `cost_usd`.

    Raises:
        ApiError: Status 422 when the grouping is unknown or `end` is before
            `start`.
    """
    _bounds(start, end)
    if group_by not in _GROUPS:
        raise ApiError(422, "invalid_request", "group_by must be agent or model")
    totals: dict[str, float] = {}
    for row in _events(session, start, end):
        if row.event_type != "usage":
            continue
        key = _spend_key(row, group_by)
        totals[key] = totals.get(key, 0.0) + _number(row.payload.get("cost_usd"))
    items: list[dict[str, object]] = []
    for key, amount in sorted(totals.items()):
        if amount == 0.0:
            continue
        if group_by == "agent":
            items.append(
                {
                    "agent_id": key,
                    "name": _agent_name(session, key),
                    "cost_usd": round(amount, 6),
                }
            )
        else:
            items.append({"model": key, "cost_usd": round(amount, 6)})
    return {"group_by": group_by, "items": items}


def dashboard_denials(
    session: Session,
    *,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, object]:
    """Count denied decisions, grouped by agent.

    Args:
        session: Open database session.
        start: Include events at or after this time.
        end: Include events at or before this time.

    Returns:
        The total and one row per agent that has a denial.

    Raises:
        ApiError: Status 422 when `end` is before `start`.
    """
    _bounds(start, end)
    counts: dict[str, int] = {}
    for row in _events(session, start, end):
        if row.event_type == "decision" and row.payload.get("effect") == "deny":
            counts[row.agent_id] = counts.get(row.agent_id, 0) + 1
    items = [
        {"agent_id": agent_id, "name": _agent_name(session, agent_id), "denials": count}
        for agent_id, count in sorted(counts.items())
    ]
    return {"total": sum(counts.values()), "items": items}


def _events(session: Session, start: datetime | None, end: datetime | None) -> list[Event]:
    """Load events inside an inclusive window.

    Args:
        session: Open database session.
        start: Lower bound. `None` has no lower bound.
        end: Upper bound. `None` has no upper bound.

    Returns:
        Matching event rows.
    """
    rows = session.scalars(select(Event)).all()
    kept: list[Event] = []
    for row in rows:
        moment = _aware(row.ts)
        if start is not None and moment < _aware(start):
            continue
        if end is not None and moment > _aware(end):
            continue
        kept.append(row)
    return kept


def _orphaned_count(session: Session) -> int:
    """Count agents whose owner is missing or inactive.

    Args:
        session: Open database session.

    Returns:
        The number of orphaned agents.
    """
    rows = session.scalars(select(Agent)).all()
    return sum(1 for row in rows if _orphaned(session, row))


def _dormant_count(session: Session, moment: datetime) -> int:
    """Count non-retired agents with no event in the last seven days.

    Args:
        session: Open database session.
        moment: End of the window.

    Returns:
        The number of dormant agents.
    """
    start = moment - _DORMANT
    recent: set[str] = set()
    for row in session.scalars(select(Event)).all():
        if _aware(row.ts) >= start:
            recent.add(row.agent_id)
    agents = session.scalars(select(Agent).where(Agent.status != "retired")).all()
    return sum(1 for row in agents if row.id not in recent)


def _spend_key(row: Event, group_by: str) -> str:
    """Choose the spend bucket for one usage event.

    Args:
        row: Usage event.
        group_by: `agent` or `model`.

    Returns:
        The agent id, or the model name. An unnamed model is an empty string.
    """
    if group_by == "agent":
        return row.agent_id
    model = row.payload.get("model")
    return model if isinstance(model, str) else ""


def _agent_name(session: Session, agent_id: str) -> str:
    """Return an agent's name, or its id when the row is gone.

    Args:
        session: Open database session.
        agent_id: Agent id stored on the event.

    Returns:
        The display name.
    """
    row = session.get(Agent, agent_id)
    return row.name if row is not None else agent_id


def _bounds(start: datetime | None, end: datetime | None) -> None:
    """Reject a window whose end is before its start.

    Args:
        start: Lower bound.
        end: Upper bound.

    Raises:
        ApiError: Status 422 when `end` is before `start`.
    """
    if start is not None and end is not None and _aware(end) < _aware(start):
        raise ApiError(422, "invalid_request", "to is before from")


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


def _aware(moment: datetime) -> datetime:
    """Return a UTC timestamp.

    Args:
        moment: Timestamp from the database or the clock.

    Returns:
        The same moment with a UTC offset.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


__all__ = ["dashboard_denials", "dashboard_spend", "dashboard_summary"]
