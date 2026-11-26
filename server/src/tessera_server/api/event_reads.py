"""Console event query, facets, histogram, and SSE stream routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.event_query import (
    EventFilters,
    encode_cursor,
    event_facets,
    event_histogram,
    list_events_after,
    parse_time_bound,
    query_events,
    resolve_agent_ids,
)

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_EVENTS_LIMIT = Query(default=100, ge=1, le=500)
_KEEPALIVE_TICKS = 15


def event_reads_router() -> APIRouter:
    """Build console event read routes under `/v1/events`.

    Returns:
        A router with list, facets, histogram, and SSE stream endpoints.
    """
    router = APIRouter(tags=["events"])
    _add_list(router)
    _add_facets(router)
    _add_histogram(router)
    _add_stream(router)
    return router


def _add_list(router: APIRouter) -> None:
    """Register `GET /v1/events`."""

    @router.get("/v1/events", summary="Query ingested events")
    def read_events(
        *,
        agent: Annotated[list[str] | None, Query()] = None,
        event_type: Annotated[list[str] | None, Query(alias="type")] = None,
        kind: str | None = None,
        effect: Annotated[list[str] | None, Query()] = None,
        status: str | None = None,
        resource: str | None = None,
        rule: str | None = None,
        run: str | None = None,
        span: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = _EVENTS_LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Query indexed events for the console explorer."""
        filters = event_filters(
            session,
            agent=agent,
            types=event_type,
            kind=kind,
            effect=effect,
            status=status,
            resource=resource,
            rule=rule,
            run=run,
            span=span,
            since=since,
            until=until,
        )
        return query_events(session, filters, limit=limit, cursor=cursor)


def _add_facets(router: APIRouter) -> None:
    """Register `GET /v1/events/facets`."""

    @router.get("/v1/events/facets", summary="Facet counts for event filters")
    def read_event_facets(
        *,
        agent: Annotated[list[str] | None, Query()] = None,
        event_type: Annotated[list[str] | None, Query(alias="type")] = None,
        kind: str | None = None,
        effect: Annotated[list[str] | None, Query()] = None,
        status: str | None = None,
        resource: str | None = None,
        rule: str | None = None,
        run: str | None = None,
        span: str | None = None,
        since: str | None = None,
        until: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return top facet values with counts for the current filters."""
        filters = event_filters(
            session,
            agent=agent,
            types=event_type,
            kind=kind,
            effect=effect,
            status=status,
            resource=resource,
            rule=rule,
            run=run,
            span=span,
            since=since,
            until=until,
        )
        return event_facets(session, filters)


def _add_histogram(router: APIRouter) -> None:
    """Register `GET /v1/events/histogram`."""

    @router.get("/v1/events/histogram", summary="Event counts by time bucket")
    def read_event_histogram(
        *,
        agent: Annotated[list[str] | None, Query()] = None,
        event_type: Annotated[list[str] | None, Query(alias="type")] = None,
        kind: str | None = None,
        effect: Annotated[list[str] | None, Query()] = None,
        status: str | None = None,
        resource: str | None = None,
        rule: str | None = None,
        run: str | None = None,
        span: str | None = None,
        since: str | None = None,
        until: str | None = None,
        buckets: Annotated[int, Query(ge=10, le=200)] = 60,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return stacked histogram counts for the current filters."""
        filters = event_filters(
            session,
            agent=agent,
            types=event_type,
            kind=kind,
            effect=effect,
            status=status,
            resource=resource,
            rule=rule,
            run=run,
            span=span,
            since=since,
            until=until,
        )
        return event_histogram(session, filters, buckets=buckets)


def _add_stream(router: APIRouter) -> None:
    """Register `GET /v1/events/stream`."""

    @router.get("/v1/events/stream", summary="Live event stream (SSE)")
    async def stream_events(
        request: Request,
        *,
        agent: Annotated[list[str] | None, Query()] = None,
        event_type: Annotated[list[str] | None, Query(alias="type")] = None,
        kind: str | None = None,
        effect: Annotated[list[str] | None, Query()] = None,
        status: str | None = None,
        resource: str | None = None,
        rule: str | None = None,
        run: str | None = None,
        span: str | None = None,
        since: str | None = None,
        until: str | None = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        _admin: AdminKey | None = _ADMIN,
    ) -> StreamingResponse:
        """Stream matching events as server-sent events."""
        return StreamingResponse(
            _sse(
                request,
                agent=agent,
                event_type=event_type,
                kind=kind,
                effect=effect,
                status=status,
                resource=resource,
                rule=rule,
                run=run,
                span=span,
                since=since,
                until=until,
                last_event_id=last_event_id,
            ),
            media_type="text/event-stream",
        )


async def _sse(
    request: Request,
    *,
    agent: list[str] | None,
    event_type: list[str] | None,
    kind: str | None,
    effect: list[str] | None,
    status: str | None,
    resource: str | None,
    rule: str | None,
    run: str | None,
    span: str | None,
    since: str | None,
    until: str | None,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    """Yield SSE frames for matching events.

    Args:
        request: Current HTTP request.
        agent: Agent filters.
        event_type: Type filters.
        kind: Kind filter.
        effect: Effect filters.
        status: Status filter.
        resource: Resource substring.
        rule: Rule filter.
        run: Run filter.
        span: Span filter.
        since: Lower time bound.
        until: Upper time bound.
        last_event_id: Resume cursor.

    Yields:
        SSE text frames.
    """
    cursor = last_event_id
    keepalive = 0
    while True:
        if await request.is_disconnected():
            break
        with request.app.state.sessions() as session:
            filters = event_filters(
                session,
                agent=agent,
                types=event_type,
                kind=kind,
                effect=effect,
                status=status,
                resource=resource,
                rule=rule,
                run=run,
                span=span,
                since=since,
                until=until,
            )
            batch = list_events_after(session, filters, after=cursor, limit=200)
        lag = bool(batch and batch[-1].get("_lag"))
        if lag:
            batch = batch[:-1]
        for item in batch:
            event_id = str(item["id"])
            stamp = str(item.get("ts") or "")
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                moment = datetime.now(UTC)
            cursor = encode_cursor(moment, event_id)
            payload = json.dumps(item, separators=(",", ":"))
            yield f"id: {cursor}\ndata: {payload}\n\n"
        if lag:
            yield "event: lag\ndata: {}\n\n"
        keepalive += 1
        if keepalive >= _KEEPALIVE_TICKS:
            yield ": keepalive\n\n"
            keepalive = 0
        await asyncio.sleep(1)


def event_filters(
    session: Session,
    *,
    agent: Sequence[str] | None,
    types: Sequence[str] | None,
    kind: str | None,
    effect: Sequence[str] | None,
    status: str | None,
    resource: str | None,
    rule: str | None,
    run: str | None,
    span: str | None,
    since: str | None,
    until: str | None,
) -> EventFilters:
    """Build normalized event filters from query parameters.

    Args:
        session: Open database session for agent resolution.
        agent: Agent id or name filters.
        types: Event type filters.
        kind: Exact kind.
        effect: Effect filters.
        status: Exact status.
        resource: Resource substring.
        rule: Exact rule.
        run: Exact run id.
        span: Exact span id.
        since: Lower time bound.
        until: Upper time bound.

    Returns:
        Normalized `EventFilters`.
    """
    return EventFilters(
        agent_ids=resolve_agent_ids(session, agent or ()),
        types=tuple(types or ()),
        kind=kind,
        effects=tuple(effect or ()),
        status=status,
        resource=resource,
        rule=rule,
        run=run,
        span=span,
        since=parse_time_bound(since, field="since"),
        until=parse_time_bound(until, field="until"),
    )


__all__ = ["event_filters", "event_reads_router"]
