"""Build a governance trace for one run."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import Agent, Event, Run
from tessera_server.services.revocations import run_document

_SPAN_CAP = 5000


def run_trace(session: Session, run_id: str) -> dict[str, object]:
    """Return the governance span tree for a run.

    Args:
        session: Open database session.
        run_id: Run id (`events.run_id` / task).

    Returns:
        `run`, `agents`, `spans`, `unparented`, and `truncated`.

    Raises:
        ApiError: Status 404 when the run does not exist.
    """
    row = session.get(Run, run_id)
    if row is None:
        raise ApiError(404, "not_found", "run not found")
    events = session.scalars(
        select(Event)
        .where((Event.run_id == run_id) | (Event.task == run_id))
        .order_by(Event.ts.asc(), Event.id.asc())
    ).all()
    by_span: dict[str, list[Event]] = defaultdict(list)
    unparented_events: list[Event] = []
    for event in events:
        if event.span_id:
            by_span[event.span_id].append(event)
        else:
            unparented_events.append(event)
    span_ids = list(by_span.keys())
    truncated = len(span_ids) > _SPAN_CAP
    if truncated:
        span_ids = span_ids[:_SPAN_CAP]
    spans = [_merge_span(span_id, by_span[span_id]) for span_id in span_ids]
    known = {span["span_id"] for span in spans}
    for span in spans:
        parent = span.get("parent_span_id")
        if parent is not None and parent not in known:
            span["parent_span_id"] = None
    agent_ids = sorted({str(span["agent_id"]) for span in spans if span.get("agent_id")})
    agents: list[dict[str, str]] = []
    if agent_ids:
        agents.extend(
            {
                "id": agent.id,
                "name": agent.name,
                "environment": agent.environment,
            }
            for agent in session.scalars(select(Agent).where(Agent.id.in_(agent_ids))).all()
        )
    return {
        "run": run_document(session, row),
        "agents": agents,
        "spans": spans,
        "unparented": [_event_summary(event) for event in unparented_events],
        "truncated": truncated,
    }


def _merge_span(span_id: str, events: list[Event]) -> dict[str, Any]:
    """Merge decision/call/approval/effect events that share a span id.

    Args:
        span_id: Shared span id.
        events: Events for that span, time-ordered.

    Returns:
        Span document for the trace API.
    """
    decision = next((event for event in events if event.event_type == "decision"), None)
    call = next((event for event in events if event.event_type == "call"), None)
    approval = next((event for event in events if event.event_type == "approval"), None)
    effects = [
        str(event.payload.get("effect") or event.effect or "")
        for event in events
        if event.event_type == "effect"
    ]
    primary = decision or call or events[0]
    start = _aware(decision.ts) if decision is not None else _aware(primary.ts)
    end = _aware(call.ts) if call is not None else None
    duration_ms = None if end is None else int((end - start).total_seconds() * 1000)
    approval_wait_ms = None
    if approval is not None and decision is not None:
        approval_wait_ms = int((_aware(approval.ts) - _aware(decision.ts)).total_seconds() * 1000)
    payload = primary.payload if isinstance(primary.payload, dict) else {}
    decision_payload = decision.payload if decision is not None else {}
    status = (
        (call.status if call is not None else None)
        or primary.status
        or str(payload.get("status") or "")
        or None
    )
    return {
        "span_id": span_id,
        "parent_span_id": primary.parent_span_id,
        "trace_id": payload.get("trace_id"),
        "agent_id": primary.agent_id,
        "instance_id": primary.instance_id,
        "kind": primary.kind,
        "resource": primary.resource,
        "start": _iso(start),
        "end": None if end is None else _iso(end),
        "duration_ms": duration_ms,
        "approval_wait_ms": approval_wait_ms,
        "status": status,
        "error_type": payload.get("error_type")
        or (call.payload.get("error_type") if call is not None else None),
        "decision": {
            "effect": (decision.effect if decision is not None else None)
            or decision_payload.get("effect"),
            "rule": (decision.rule if decision is not None else None)
            or decision_payload.get("rule"),
            "reason": decision_payload.get("reason"),
        },
        "principal": _principal_label(decision_payload.get("principal")),
        "approval_id": (
            str(approval.payload.get("id") or approval.id) if approval is not None else None
        ),
        "effects": [effect for effect in effects if effect],
        "event_ids": [event.id for event in events],
    }


def _principal_label(value: object) -> str | None:
    """Normalize a decision principal field to a display string.

    Args:
        value: Event payload principal (string or ``{user, groups}``).

    Returns:
        The principal user string, or ``None`` when absent.
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        user = value.get("user")
        if isinstance(user, str) and user.strip():
            return user.strip()
    return None


def _event_summary(event: Event) -> dict[str, object]:
    """Shape an unparented event for the trace response.

    Args:
        event: Stored event without a span id.

    Returns:
        Compact event document.
    """
    return {
        "id": event.id,
        "type": event.event_type,
        "ts": _iso(_aware(event.ts)),
        "agent_id": event.agent_id,
        "kind": event.kind,
        "resource": event.resource,
        "effect": event.effect,
        "status": event.status,
    }


def _aware(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    Args:
        moment: Timestamp from the database.

    Returns:
        Timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    """Format a timestamp as UTC ISO-8601 with `Z`.

    Args:
        moment: Aware datetime.

    Returns:
        ISO string.
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["run_trace"]
