"""Run reads, run revocation, and the revocation stream."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.event_query import parse_time_bound
from tessera_server.services.revocations import (
    get_run,
    iter_revocations,
    list_revocations,
    list_runs,
    revoke_run,
    stream_identity,
)
from tessera_server.services.traces import run_trace

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_LIMIT = Query(default=50, ge=1, le=500)


class RevokeRunBody(BaseModel):
    """Body for `POST /v1/runs/{id}:revoke`."""

    reason: str | None = Field(default=None, max_length=500)


_EMPTY_REVOKE = RevokeRunBody()


def runs_router() -> APIRouter:
    """Build the run and revocation-stream routes.

    Returns:
        A router for run reads, run revocation, and the event stream.
    """
    router = APIRouter(tags=["runs"])

    @router.get("/v1/runs")
    def read_runs(
        *,
        limit: int = _LIMIT,
        cursor: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        environment: str | None = None,
        since: str | None = None,
        until: str | None = None,
        has_denials: bool | None = None,
        has_errors: bool | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List runs.

        Args:
            limit: Page size.
            cursor: Id of the last run already seen.
            status: Exact status filter.
            agent: Agent id filter.
            environment: Exact agent environment filter.
            since: ISO-8601 or relative lower bound on `first_seen_at`.
            until: ISO-8601 or relative upper bound.
            has_denials: Require or exclude deny events.
            has_errors: Require or exclude error-status events.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of runs with span/denial/error/approval counts.
        """
        return list_runs(
            session,
            limit=limit,
            cursor=cursor,
            status=status,
            agent_id=agent,
            environment=environment,
            since=parse_time_bound(since, field="since"),
            until=parse_time_bound(until, field="until"),
            has_denials=has_denials,
            has_errors=has_errors,
        )

    @router.get("/v1/runs/{run_id}")
    def read_run(
        run_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return one run.

        Args:
            run_id: Run to read.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The run, its participants, and how many events name it.
        """
        return get_run(session, run_id)

    @router.get("/v1/runs/{run_id}/trace", summary="Governance trace for a run")
    def read_run_trace(
        run_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return the span tree for a run.

        Args:
            run_id: Run to inspect.
            _admin: Authenticated admin.
            session: Database session.

        Returns:
            Trace document with spans and unparented events.
        """
        return run_trace(session, run_id)

    @router.post("/v1/runs/{run_id}:revoke")
    def post_revoke(
        run_id: str,
        body: RevokeRunBody = _EMPTY_REVOKE,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Revoke a run.

        Args:
            run_id: Run to revoke.
            body: Optional JSON with an operator `reason` (max 500 chars).
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The revoked run.
        """
        reason = None
        if body.reason is not None:
            trimmed = body.reason.strip()
            reason = trimmed or None
        return revoke_run(session, run_id, actor=_actor(admin), reason=reason)

    @router.get("/v1/revocations", summary="List recent revocations")
    def read_revocations(
        *,
        limit: int = _LIMIT,
        cursor: str | None = None,
        kind: str | None = None,
        agent_id: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List revocations newest first for operators.

        Args:
            limit: Page size.
            cursor: Id of the last row already seen (newer pages use lower ids).
            kind: Exact kind filter.
            agent_id: Limit to one agent.
            _admin: Authenticated admin key or session.
            session: Database session.

        Returns:
            A page of `{id, kind, agent_id, subject, created_at}` rows.
        """
        try:
            return list_revocations(
                session,
                limit=limit,
                cursor=cursor,
                kind=kind,
                agent_id=agent_id,
            )
        except ValueError as err:
            raise ApiError(422, "invalid_request", "cursor must be an integer") from err

    @router.get("/v1/revocations/stream")
    def stream(request: Request) -> StreamingResponse:
        """Stream revocation events for the calling instance.

        Args:
            request: Request whose bearer token is a runtime JWT. `Last-Event-ID`
                resumes the stream.

        Returns:
            A `text/event-stream` response.
        """
        agent_id, instance_id = stream_identity(
            request.app.state.sessions,
            request.app.state.settings,
            _bearer(request),
        )
        return StreamingResponse(
            iter_revocations(
                request.app.state.sessions,
                agent_id=agent_id,
                instance_id=instance_id,
                last_event_id=_last_event_id(request),
                poll=float(request.app.state.revocation_poll),
                keepalive=float(request.app.state.revocation_keepalive),
            ),
            media_type="text/event-stream",
        )

    return router


def _actor(admin: AdminKey | None) -> str:
    """Name the admin key for the audit log.

    Args:
        admin: Authenticated key, or `None` in development mode.

    Returns:
        `admin:<id>` or `dev`.
    """
    if admin is None:
        return "dev"
    return f"admin:{admin.id}"


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


def _last_event_id(request: Request) -> int | None:
    """Read the resume cursor.

    Args:
        request: Current HTTP request.

    Returns:
        The last delivered revocation id, or `None` to start at the current end.

    Raises:
        ApiError: Status 422 when the header is not an integer.
    """
    header = request.headers.get("last-event-id")
    if header is None or header == "":
        return None
    try:
        return int(header)
    except ValueError as err:
        raise ApiError(422, "invalid_request", "Last-Event-ID must be an integer") from err


__all__ = ["runs_router"]
