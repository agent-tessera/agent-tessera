"""Admin query helpers for indexed events (console explorer)."""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Float, Select, and_, case, func, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.orm import Mapped, Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import Agent, Event

_RELATIVE = re.compile(r"^(\d+)(h|d)$")
_LIMIT_DEFAULT = 100
_LIMIT_MAX = 500
_FACET_TOP = 20
_FACET_KEYS = ("agent", "type", "effect", "status", "kind")


@dataclass(frozen=True)
class EventFilters:
    """Normalized filters for event list, facets, histogram, and stream.

    Args:
        agent_ids: Resolved agent ids (empty means no agent filter).
        types: Event type values.
        kind: Exact kind, or `None`.
        effects: Effect values.
        status: Exact status, or `None`.
        resource: Case-insensitive resource substring, or `None`.
        rule: Exact rule, or `None`.
        run: Exact run id, or `None`.
        span: Exact span id, or `None`.
        since: Inclusive lower bound on `ts`, or `None`.
        until: Inclusive upper bound on `ts`, or `None`.
    """

    agent_ids: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    kind: str | None = None
    effects: tuple[str, ...] = ()
    status: str | None = None
    resource: str | None = None
    rule: str | None = None
    run: str | None = None
    span: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def parse_time_bound(value: str | None, *, field: str) -> datetime | None:
    """Parse an ISO-8601 or relative (`1h` / `24h` / `7d`) time bound.

    Args:
        value: Query string, or `None`.
        field: Name used in `422` messages.

    Returns:
        A timezone-aware datetime, or `None`.

    Raises:
        ApiError: Status 422 when the value is malformed.
    """
    if value is None or value == "":
        return None
    text = value.strip()
    match = _RELATIVE.fullmatch(text)
    if match is not None:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
        return datetime.now(UTC) - delta
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as err:
        raise ApiError(422, "invalid_request", f"{field} must be a timestamp") from err
    if moment.tzinfo is None:
        raise ApiError(422, "invalid_request", f"{field} must include a timezone")
    return moment.astimezone(UTC)


def resolve_agent_ids(session: Session, agents: Sequence[str]) -> tuple[str, ...]:
    """Resolve agent id or name filters to ids.

    Unknown values yield an empty id list that matches nothing when combined
    with an agent filter (caller passes them through as a non-empty filter
    with no matching ids).

    Args:
        session: Open database session.
        agents: Raw `agent` query values (id or name).

    Returns:
        Distinct agent ids that exist. When `agents` is empty, returns `()`.
        When every name/id is unknown, returns a sentinel empty-but-filtered
        marker by returning `("__none__",)` so queries match zero rows.
    """
    if not agents:
        return ()
    ids: list[str] = []
    for raw in agents:
        text = raw.strip()
        if not text:
            continue
        row = session.get(Agent, text)
        if row is None:
            row = session.scalars(select(Agent).where(Agent.name == text).limit(1)).first()
        if row is not None:
            ids.append(row.id)
    if not ids:
        return ("__none__",)
    return tuple(dict.fromkeys(ids))


def encode_cursor(ts: datetime, event_id: str) -> str:
    """Encode a list cursor as base64url of `ts|id`.

    Args:
        ts: Event timestamp.
        event_id: Event id.

    Returns:
        Opaque cursor string.
    """
    stamp = _aware(ts).isoformat().replace("+00:00", "Z")
    raw = f"{stamp}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode a list cursor.

    Args:
        cursor: Opaque value from a previous page.

    Returns:
        The timestamp and id.

    Raises:
        ApiError: Status 422 when the cursor is not valid.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, event_id = raw.split("|", 1)
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, UnicodeDecodeError) as err:
        raise ApiError(422, "invalid_request", "cursor is invalid") from err
    return _aware(moment), event_id


def _aware(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC (SQLite round-trip).

    Args:
        moment: Timestamp from the database or a cursor.

    Returns:
        A timezone-aware UTC timestamp.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def apply_filters(query: Select[Any], filters: EventFilters) -> Select[Any]:
    """Apply `EventFilters` to a select of `Event` rows.

    Args:
        query: Base select.
        filters: Normalized filters.

    Returns:
        The narrowed select.
    """
    if filters.agent_ids:
        query = query.where(Event.agent_id.in_(filters.agent_ids))
    if filters.types:
        query = query.where(Event.event_type.in_(filters.types))
    if filters.kind is not None:
        query = query.where(Event.kind == filters.kind)
    if filters.effects:
        query = query.where(Event.effect.in_(filters.effects))
    if filters.status is not None:
        query = query.where(Event.status == filters.status)
    if filters.resource is not None:
        query = query.where(Event.resource.ilike(f"%{filters.resource}%"))
    if filters.rule is not None:
        query = query.where(Event.rule == filters.rule)
    if filters.run is not None:
        query = query.where(Event.run_id == filters.run)
    if filters.span is not None:
        query = query.where(Event.span_id == filters.span)
    if filters.since is not None:
        query = query.where(Event.ts >= filters.since)
    if filters.until is not None:
        query = query.where(Event.ts <= filters.until)
    return query


def query_events(
    session: Session,
    filters: EventFilters,
    *,
    limit: int = _LIMIT_DEFAULT,
    cursor: str | None = None,
) -> dict[str, object]:
    """List events newest-first for the console explorer.

    Args:
        session: Open database session.
        filters: Normalized filters.
        limit: Page size (1-500).
        cursor: Opaque continuation token.

    Returns:
        `items` (payload + `id` + `agent_name`) and `next_cursor`.
    """
    limit = max(1, min(limit, _LIMIT_MAX))
    query = apply_filters(select(Event), filters).order_by(Event.ts.desc(), Event.id.desc())
    if cursor is not None:
        stamp, event_id = decode_cursor(cursor)
        query = query.where(
            or_(
                Event.ts < stamp,
                and_(Event.ts == stamp, Event.id < event_id),
            )
        )
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    names = _agent_names(session, [row.agent_id for row in page])
    items = [_document(row, names.get(row.agent_id)) for row in page]
    next_cursor = encode_cursor(page[-1].ts, page[-1].id) if len(rows) > limit else None
    return {"items": items, "next_cursor": next_cursor}


def event_histogram(
    session: Session,
    filters: EventFilters,
    *,
    buckets: int = 60,
) -> dict[str, object]:
    """Bucket event counts for the explorer histogram.

    Args:
        session: Open database session.
        filters: Normalized filters.
        buckets: Number of buckets (10-200).

    Returns:
        `from`, `to`, `bucket_seconds`, and `series` rows with `t`, `total`,
        `deny`, `require_approval`, and `error` counts.
    """
    buckets = max(10, min(buckets, 200))
    now = datetime.now(UTC)
    start = _aware(filters.since) if filters.since is not None else (now - timedelta(hours=24))
    end = _aware(filters.until) if filters.until is not None else now
    if end <= start:
        end = start + timedelta(seconds=1)
    span_seconds = max(1.0, (end - start).total_seconds())
    width = max(1, int(span_seconds // buckets) or 1)
    bucket_count = max(1, int((span_seconds + width - 1) // width))
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    epoch_expr = (
        func.extract("epoch", Event.ts)
        if dialect == "postgresql"
        else sa_cast(func.strftime("%s", Event.ts), Float)
    )
    bucket_expr = func.floor((epoch_expr - start.timestamp()) / float(width))
    query = apply_filters(
        cast(
            "Select[Any]",
            select(
                bucket_expr.label("bucket"),
                func.count().label("total"),
                func.sum(case((Event.effect == "deny", 1), else_=0)).label("deny"),
                func.sum(case((Event.effect == "require_approval", 1), else_=0)).label(
                    "require_approval"
                ),
                func.sum(case((Event.status == "error", 1), else_=0)).label("error"),
            ).select_from(Event),
        ),
        filters,
    ).group_by(bucket_expr)
    counts: dict[int, dict[str, int]] = {}
    for row in session.execute(query):
        index = int(row.bucket) if row.bucket is not None else 0
        counts[index] = {
            "total": int(row.total or 0),
            "deny": int(row.deny or 0),
            "require_approval": int(row.require_approval or 0),
            "error": int(row.error or 0),
        }
    series: list[dict[str, object]] = []
    for index in range(bucket_count):
        moment = start + timedelta(seconds=index * width)
        if moment > end:
            break
        cell = counts.get(index, {"total": 0, "deny": 0, "require_approval": 0, "error": 0})
        series.append(
            {
                "t": moment.isoformat().replace("+00:00", "Z"),
                "total": cell["total"],
                "deny": cell["deny"],
                "require_approval": cell["require_approval"],
                "error": cell["error"],
            }
        )
    return {
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": end.isoformat().replace("+00:00", "Z"),
        "bucket_seconds": width,
        "series": series,
    }


def event_facets(session: Session, filters: EventFilters) -> dict[str, object]:
    """Return top facet values with counts for the current filters.

    Args:
        session: Open database session.
        filters: Normalized filters (facet dimension is not excluded).

    Returns:
        Mapping of facet name to `[{"value", "count"}, ...]` (top 20).
    """
    result: dict[str, object] = {}
    for key in _FACET_KEYS:
        column = _facet_column(key)
        base = cast("Select[Any]", select(column, func.count()).select_from(Event))
        query = apply_filters(base, filters)
        query = query.where(column.is_not(None)).group_by(column).order_by(func.count().desc())
        rows = session.execute(query.limit(_FACET_TOP)).all()
        if key == "agent":
            names = _agent_names(session, [str(value) for value, _count in rows])
            result[key] = [
                {
                    "value": names.get(str(value), str(value)),
                    "id": str(value),
                    "count": int(count),
                }
                for value, count in rows
            ]
        else:
            result[key] = [{"value": str(value), "count": int(count)} for value, count in rows]
    return {"facets": result}


def _facet_column(key: str) -> Mapped[str | None] | Mapped[str]:
    """Return the SQLAlchemy column for a facet key.

    Args:
        key: Facet name.

    Returns:
        Column expression.
    """
    mapping: dict[str, Mapped[str | None] | Mapped[str]] = {
        "agent": Event.agent_id,
        "type": Event.event_type,
        "effect": Event.effect,
        "status": Event.status,
        "kind": Event.kind,
    }
    return mapping[key]


def _agent_names(session: Session, agent_ids: Sequence[str]) -> dict[str, str]:
    """Load agent display names.

    Args:
        session: Open database session.
        agent_ids: Agent ids to resolve.

    Returns:
        Id → name map.
    """
    unique = [agent_id for agent_id in dict.fromkeys(agent_ids) if agent_id]
    if not unique:
        return {}
    rows = session.scalars(select(Agent).where(Agent.id.in_(unique))).all()
    return {row.id: row.name for row in rows}


def _document(row: Event, agent_name: str | None) -> dict[str, object]:
    """Shape an event row for the JSON response.

    Args:
        row: Database event.
        agent_name: Display name when known.

    Returns:
        Payload fields plus `id` and `agent_name`.
    """
    body = dict(row.payload)
    body["id"] = row.id
    body["agent_name"] = agent_name or row.agent_id
    return body


def list_events_after(
    session: Session,
    filters: EventFilters,
    *,
    after: str | None = None,
    limit: int = 200,
) -> list[dict[str, object]]:
    """Return events newer than a cursor for the SSE stream.

    Args:
        session: Open database session.
        filters: Normalized filters.
        after: Opaque cursor of the last delivered event, or `None` for the
            newest page (at most `limit` events ending at now).
        limit: Maximum events to return (hard cap 200).

    Returns:
        Events in ascending time order (oldest of the page first).
    """
    limit = max(1, min(limit, 200))
    query = apply_filters(select(Event), filters).order_by(Event.ts.asc(), Event.id.asc())
    if after is not None:
        stamp, event_id = decode_cursor(after)
        query = query.where(
            or_(
                Event.ts > stamp,
                and_(Event.ts == stamp, Event.id > event_id),
            )
        )
        rows = list(session.scalars(query.limit(limit + 1)).all())
        truncated = len(rows) > limit
        page = rows[:limit]
    else:
        # First connect: deliver the newest `limit` events oldest-first.
        newest = apply_filters(select(Event), filters).order_by(Event.ts.desc(), Event.id.desc())
        rows = list(session.scalars(newest.limit(limit)).all())
        page = list(reversed(rows))
        truncated = False
    names = _agent_names(session, [row.agent_id for row in page])
    items = [_document(row, names.get(row.agent_id)) for row in page]
    if truncated:
        items.append({"_lag": True})
    return items


__all__ = [
    "EventFilters",
    "apply_filters",
    "decode_cursor",
    "encode_cursor",
    "event_facets",
    "event_histogram",
    "list_events_after",
    "parse_time_bound",
    "query_events",
    "resolve_agent_ids",
]
