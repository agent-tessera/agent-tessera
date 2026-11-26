"""Counts for the admin overview page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tessera_server.db.models import Agent, Approval, Run
from tessera_server.services.footprint import footprint_totals

_STATUSES = ("active", "suspended", "draft", "retired")
_WINDOW = timedelta(hours=24)


def overview_counts(session: Session, *, now: datetime | None = None) -> dict[str, object]:
    """Count agents, pending approvals, recent runs, and recent denials.

    Args:
        session: Open database session.
        now: Clock for the 24 hour window. Defaults to the current time.

    Returns:
        Agent counts by status, `pending_approvals`, `runs_24h`,
        `denials_24h`, and `checklist` when the org has no agents.

    Example:
        ```python
        summary = overview_counts(session)
        assert summary["pending_approvals"] >= 0
        ```
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    agents = {status: _count_status(session, status) for status in _STATUSES}
    agents["total"] = sum(agents.values())
    pending = session.scalar(
        select(func.count()).select_from(Approval).where(Approval.status == "pending")
    )
    runs = session.scalar(
        select(func.count()).select_from(Run).where(Run.last_seen_at >= moment - _WINDOW)
    )
    totals = footprint_totals(session, agent_id=None, start=moment - _WINDOW, end=None)
    denials = totals["denials"]
    return {
        "agents": agents,
        "pending_approvals": int(pending or 0),
        "runs_24h": int(runs or 0),
        "denials_24h": denials if isinstance(denials, int) else 0,
        "checklist": agents["total"] == 0,
    }


def _count_status(session: Session, status: str) -> int:
    """Count agents in one status.

    Args:
        session: Open database session.
        status: Agent status.

    Returns:
        The number of matching agents.
    """
    counted = session.scalar(select(func.count()).select_from(Agent).where(Agent.status == status))
    return int(counted or 0)


__all__ = ["overview_counts"]
