"""Console command-palette search (never reads event payloads)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tessera_server.db.models import Agent, Approval, Event, Run

_SPAN_HEX = re.compile(r"^[0-9a-f]{16}$")
_MIN_QUERY = 2


def console_search(session: Session, query: str, *, limit: int = 8) -> dict[str, object]:
    """Search agents and other governance entities for the palette.

    Args:
        session: Open database session.
        query: Free-text query.
        limit: Max hits per type (1-20).

    Returns:
        `results` with `type`, `id`, `title`, `subtitle`, and `href`. Never
        reads `events.payload`.
    """
    limit = max(1, min(limit, 20))
    needle = query.strip()
    if len(needle) < _MIN_QUERY:
        return {"results": []}
    results: list[dict[str, str]] = []
    results.extend(_agents(session, needle, limit))
    results.extend(_runs(session, needle, limit))
    results.extend(_approvals(session, needle, limit))
    results.extend(_spans(session, needle, limit))
    results.extend(_resources(session, needle, limit))
    results.extend(_rules(session, needle, limit))
    return {"results": results}


def _agents(session: Session, needle: str, limit: int) -> list[dict[str, str]]:
    pattern = f"%{needle}%"
    rows = session.scalars(
        select(Agent)
        .where(
            or_(
                Agent.name.ilike(pattern),
                Agent.description.ilike(pattern),
            )
        )
        .order_by(Agent.name)
        .limit(limit)
    ).all()
    return [
        {
            "type": "agent",
            "id": row.id,
            "title": row.name,
            "subtitle": row.environment,
            "href": f"/app/agents/{row.id}",
        }
        for row in rows
    ]


def _runs(session: Session, needle: str, limit: int) -> list[dict[str, str]]:
    rows = session.scalars(
        select(Run).where(Run.id.startswith(needle)).order_by(Run.id.desc()).limit(limit)
    ).all()
    return [
        {
            "type": "run",
            "id": row.id,
            "title": row.id,
            "subtitle": row.status,
            "href": f"/app/traces/{row.id}",
        }
        for row in rows
    ]


def _approvals(session: Session, needle: str, limit: int) -> list[dict[str, str]]:
    pattern = f"%{needle}%"
    rows = session.scalars(
        select(Approval)
        .join(Agent, Agent.id == Approval.agent_id)
        .where(Approval.id.startswith(needle) | Agent.name.ilike(pattern))
        .order_by(Approval.id.desc())
        .limit(limit)
    ).all()
    out: list[dict[str, str]] = []
    for row in rows:
        agent = session.get(Agent, row.agent_id)
        name = agent.name if agent is not None else row.agent_id
        out.append(
            {
                "type": "approval",
                "id": row.id,
                "title": row.id,
                "subtitle": name,
                "href": f"/app/approvals/{row.id}",
            }
        )
    return out


def _spans(session: Session, needle: str, limit: int) -> list[dict[str, str]]:
    if not _SPAN_HEX.fullmatch(needle.lower()):
        return []
    rows = session.scalars(
        select(Event).where(Event.span_id == needle.lower()).order_by(Event.ts.desc()).limit(limit)
    ).all()
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        if row.span_id in seen:
            continue
        seen.add(str(row.span_id))
        run = row.run_id or row.task or ""
        out.append(
            {
                "type": "span",
                "id": str(row.span_id),
                "title": str(row.span_id),
                "subtitle": row.resource or row.event_type,
                "href": f"/app/traces/{run}?span={row.span_id}" if run else "/app/explorer",
            }
        )
    return out


def _resources(session: Session, needle: str, limit: int) -> list[dict[str, str]]:
    since = datetime.now(UTC) - timedelta(days=7)
    pattern = f"%{needle}%"
    rows = session.execute(
        select(Event.resource, func.count())
        .where(Event.resource.ilike(pattern), Event.ts >= since)
        .group_by(Event.resource)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {
            "type": "resource",
            "id": str(value),
            "title": str(value),
            "subtitle": f"{count} events (7d)",
            "href": f"/app/explorer?resource={value}",
        }
        for value, count in rows
        if value
    ]


def _rules(session: Session, needle: str, limit: int) -> list[dict[str, str]]:
    since = datetime.now(UTC) - timedelta(days=7)
    pattern = f"%{needle}%"
    rows = session.execute(
        select(Event.rule, func.count())
        .where(Event.rule.ilike(pattern), Event.ts >= since)
        .group_by(Event.rule)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {
            "type": "rule",
            "id": str(value),
            "title": str(value),
            "subtitle": f"{count} events (7d)",
            "href": f"/app/explorer?rule={value}",
        }
        for value, count in rows
        if value
    ]


__all__ = ["console_search"]
