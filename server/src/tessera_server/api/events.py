"""Event ingestion and audit reads."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.api.event_reads import event_reads_router
from tessera_server.db.models import AdminKey
from tessera_server.services.audit import list_audit, verify_audit
from tessera_server.services.events import ingest_events, list_events

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_LIMIT = Query(default=50, ge=1, le=500)
_MAX_EVENTS = 500
_MAX_BYTES = 1_000_000


def events_router() -> APIRouter:
    """Build the event and audit routes.

    Returns:
        A router for `POST /v1/events`, console event queries, and the audit
        reads.
    """
    router = APIRouter(tags=["events"])

    @router.post("/v1/events")
    async def post_events(request: Request, session: Session = _SESSION) -> dict[str, int]:
        """Store a batch of events from an agent instance.

        Args:
            request: Request whose body is `{"events": [...]}` and whose
                bearer token is a runtime JWT.
            session: Database session.

        Returns:
            How many events were new and how many were duplicates.
        """
        raw = await request.body()
        if len(raw) > _MAX_BYTES:
            raise ApiError(413, "payload_too_large", "event batch is larger than 1 MB")
        events = _events(raw)
        return ingest_events(session, request.app.state.settings, _bearer(request), events)

    @router.get("/v1/audit")
    def read_audit(
        *,
        agent_id: str | None = None,
        event_type: Annotated[str | None, Query(alias="type")] = None,
        task: str | None = None,
        since: str | None = None,
        limit: int = _LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List ingested events.

        Args:
            agent_id: Filter by agent.
            event_type: Filter by event type.
            task: Filter by task id.
            since: Include events at or after this timestamp.
            limit: Page size.
            cursor: Id of the last event already seen.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of events.
        """
        return list_events(
            session,
            agent_id=agent_id,
            event_type=event_type,
            task=task,
            since=_since(since),
            limit=limit,
            cursor=cursor,
        )

    @router.get("/v1/audit/log")
    def read_log(
        *,
        limit: int = _LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List the server audit chain.

        Args:
            limit: Page size.
            cursor: Sequence number of the last row already seen.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of audit rows.
        """
        try:
            return list_audit(session, limit=limit, cursor=cursor)
        except ValueError as err:
            raise ApiError(422, "invalid_request", "cursor must be an integer") from err

    @router.get("/v1/audit/verify")
    def read_verify(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Recompute the server audit chain.

        Args:
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Whether the chain is intact, and the first problem when it is not.
        """
        result = verify_audit(session)
        body: dict[str, object] = {"ok": result.ok, "events": result.events}
        if not result.ok:
            body["error_seq"] = result.error_seq
            body["error"] = result.error
        return body

    router.include_router(event_reads_router())
    return router


def _events(raw: bytes) -> list[dict[str, object]]:
    """Decode an event batch.

    Args:
        raw: Request body.

    Returns:
        The event objects.

    Raises:
        ApiError: Status 413 when there are more than 500 events, or 422 when
            the body is not an event batch.
    """
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ApiError(422, "invalid_request", "request validation failed") from err
    if not isinstance(document, dict):
        raise ApiError(422, "invalid_request", "request validation failed")
    events = document.get("events")
    if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
        raise ApiError(422, "invalid_request", "request validation failed")
    if len(events) > _MAX_EVENTS:
        raise ApiError(413, "payload_too_large", "event batch exceeds 500 events")
    return events


def _bearer(request: Request) -> str:
    """Return the bearer credential.

    Args:
        request: Current HTTP request.

    Returns:
        The credential after `Bearer`.

    Raises:
        ApiError: Status 401 when the header is missing or not a bearer token.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or credential == "":
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return credential


def _since(value: str | None) -> datetime | None:
    """Parse the `since` filter.

    Args:
        value: ISO-8601 timestamp, or `None`.

    Returns:
        A timezone-aware datetime, or `None`.

    Raises:
        ApiError: Status 422 when the value is not a timestamp.
    """
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ApiError(422, "invalid_request", "since must be a timestamp") from err
    return moment


__all__ = ["events_router"]
