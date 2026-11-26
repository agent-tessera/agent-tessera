"""Run revocation and the revocation stream."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import (
    Agent,
    Event,
    Instance,
    Revocation,
    Run,
    RunParticipant,
    utcnow,
)
from tessera_server.services.audit import append_audit
from tessera_server.services.keys import verify_runtime_token

_POLL_SECONDS = 1.0
_KEEPALIVE_SECONDS = 15.0
_PAGE_DEFAULT = 50
_AGENT_KINDS = frozenset({"agent_suspended", "agent_activated", "policy_updated"})


def revoke_run(
    session: Session,
    run_id: str,
    *,
    actor: str,
    reason: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Revoke a run and publish `run_revoked`.

    Args:
        session: Open database session.
        run_id: Run to revoke.
        actor: Audit actor.
        reason: Optional operator note stored on the audit row.
        now: Clock for the revocation.

    Returns:
        The run, including participants and the event count.

    Raises:
        ApiError: Status 404 when the run does not exist, or 409 when it is
            already revoked.
    """
    row = session.get(Run, run_id)
    if row is None:
        raise ApiError(404, "not_found", "run not found")
    if row.status == "revoked":
        raise ApiError(409, "conflict", "run is already revoked")
    moment = (now or utcnow)()
    row.status = "revoked"
    row.revoked_at = moment
    row.revoked_by = actor
    revocation = Revocation(kind="run_revoked", agent_id=None, subject=row.id, created_at=moment)
    session.add(revocation)
    session.flush()
    payload: dict[str, object] = {"id": row.id, "revocation_id": revocation.id}
    if reason:
        payload["reason"] = reason
    append_audit(
        session,
        actor=actor,
        entry_type="run_revoked",
        payload=payload,
        now=now,
    )
    return run_document(session, row)


def list_runs(
    session: Session,
    *,
    limit: int = _PAGE_DEFAULT,
    cursor: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    environment: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    has_denials: bool | None = None,
    has_errors: bool | None = None,
) -> dict[str, object]:
    """List runs.

    Args:
        session: Open database session.
        limit: Maximum number of rows to return.
        cursor: Return rows whose id is greater than this value.
        status: Exact run status filter.
        agent_id: Limit to runs that include this agent.
        environment: Limit to runs with a participant agent in this
            environment.
        since: Inclusive lower bound on `first_seen_at`.
        until: Inclusive upper bound on `first_seen_at`.
        has_denials: When true/false, require (or exclude) deny events.
        has_errors: When true/false, require (or exclude) error-status events.

    Returns:
        `items` and `next_cursor`. Each item includes denial/error/span counts.
    """
    query = select(Run).order_by(Run.id)
    if cursor is not None:
        query = query.where(Run.id > cursor)
    if status is not None:
        query = query.where(Run.status == status)
    if since is not None:
        query = query.where(Run.first_seen_at >= since)
    if until is not None:
        query = query.where(Run.first_seen_at <= until)
    if agent_id is not None:
        query = query.where(
            Run.id.in_(select(RunParticipant.run_id).where(RunParticipant.agent_id == agent_id))
        )
    if environment is not None:
        env_runs = (
            select(RunParticipant.run_id)
            .join(Agent, Agent.id == RunParticipant.agent_id)
            .where(Agent.environment == environment)
        )
        query = query.where(Run.id.in_(env_runs))
    if has_denials is not None:
        denial_runs = select(Event.run_id).where(Event.effect == "deny").distinct()
        query = (
            query.where(Run.id.in_(denial_runs))
            if has_denials
            else query.where(Run.id.not_in(denial_runs))
        )
    if has_errors is not None:
        error_runs = select(Event.run_id).where(Event.status == "error").distinct()
        query = (
            query.where(Run.id.in_(error_runs))
            if has_errors
            else query.where(Run.id.not_in(error_runs))
        )
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = page[-1].id if len(rows) > limit else None
    return {
        "items": [run_document(session, row, counts=True) for row in page],
        "next_cursor": next_cursor,
    }


def get_run(session: Session, run_id: str) -> dict[str, object]:
    """Return one run.

    Args:
        session: Open database session.
        run_id: Run to read.

    Returns:
        The run, including participants and the event count.

    Raises:
        ApiError: Status 404 when the run does not exist.
    """
    row = session.get(Run, run_id)
    if row is None:
        raise ApiError(404, "not_found", "run not found")
    return run_document(session, row)


def run_document(session: Session, row: Run, *, counts: bool = False) -> dict[str, object]:
    """Serialize a run with its participants and event count.

    Args:
        session: Open database session.
        row: Stored run.
        counts: When true, add span/denial/error/approval counts for the
            console traces table.

    Returns:
        The run document.
    """
    participants = session.scalars(
        select(RunParticipant)
        .where(RunParticipant.run_id == row.id)
        .order_by(RunParticipant.instance_id)
    ).all()
    count = session.scalar(
        select(func.count())
        .select_from(Event)
        .where((Event.task == row.id) | (Event.run_id == row.id))
    )
    body: dict[str, object] = {
        "id": row.id,
        "status": row.status,
        "first_seen_at": _iso(row.first_seen_at),
        "last_seen_at": _iso(row.last_seen_at),
        "revoked_at": None if row.revoked_at is None else _iso(row.revoked_at),
        "revoked_by": row.revoked_by,
        "supervisor": row.supervisor,
        "principal": row.principal,
        "participants": [
            {"instance_id": item.instance_id, "agent_id": item.agent_id} for item in participants
        ],
        "event_count": int(count or 0),
    }
    if counts:
        body["span_count"] = int(
            session.scalar(
                select(func.count(func.distinct(Event.span_id))).where(
                    ((Event.task == row.id) | (Event.run_id == row.id)) & Event.span_id.is_not(None)
                )
            )
            or 0
        )
        body["denial_count"] = int(
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    ((Event.task == row.id) | (Event.run_id == row.id)) & (Event.effect == "deny")
                )
            )
            or 0
        )
        body["error_count"] = int(
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    ((Event.task == row.id) | (Event.run_id == row.id)) & (Event.status == "error")
                )
            )
            or 0
        )
        body["approval_count"] = int(
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    ((Event.task == row.id) | (Event.run_id == row.id))
                    & (Event.event_type == "approval")
                )
            )
            or 0
        )
    return body


def stream_identity(
    sessions: sessionmaker[Session], settings: Settings, token: str
) -> tuple[str, str]:
    """Read the agent and instance from a runtime token.

    Args:
        sessions: Session factory.
        settings: Expected issuer.
        token: Compact JWT.

    Returns:
        Agent id and instance id.

    Raises:
        ApiError: Status 401 when the token is invalid, or 403 when the
            instance is revoked.
    """
    return _identity(sessions, settings, token)


def iter_revocations(
    sessions: sessionmaker[Session],
    *,
    agent_id: str,
    instance_id: str,
    last_event_id: int | None,
    poll: float = _POLL_SECONDS,
    keepalive: float = _KEEPALIVE_SECONDS,
) -> Iterator[str]:
    """Yield server-sent revocation events for one instance.

    Args:
        sessions: Session factory. Each poll opens and closes its own session.
        agent_id: Calling agent.
        instance_id: Calling instance.
        last_event_id: Resume after this revocation id. `None` starts after the
            newest row.
        poll: Seconds between table polls.
        keepalive: Seconds between `: keepalive` comments when nothing is sent.

    Yields:
        SSE frames.
    """
    cursor = _max_id(sessions) if last_event_id is None else last_event_id
    sent = time.monotonic()
    while True:
        rows = _pending(sessions, cursor, agent_id, instance_id)
        if rows:
            for revocation_id, kind, subject, created_at in rows:
                cursor = revocation_id
                yield _frame(revocation_id, kind, subject, created_at)
            sent = time.monotonic()
        elif time.monotonic() - sent >= keepalive:
            yield ": keepalive\n\n"
            sent = time.monotonic()
        time.sleep(poll)


def _identity(sessions: sessionmaker[Session], settings: Settings, token: str) -> tuple[str, str]:
    """Read the agent and instance from a runtime token.

    Args:
        sessions: Session factory.
        settings: Expected issuer.
        token: Compact JWT.

    Returns:
        Agent id and instance id.

    Raises:
        ApiError: Status 401 when the token is invalid, or 403 when the
            instance is revoked.
    """
    with sessions() as session:
        try:
            claims = verify_runtime_token(session, settings, token)
        except ValueError as err:
            raise ApiError(401, "invalid_token", "runtime token is not valid") from err
        agent_id = claims.get("agent_id")
        instance_id = claims.get("inst")
        if not isinstance(agent_id, str) or not isinstance(instance_id, str):
            raise ApiError(401, "invalid_token", "runtime token is not valid")
        instance = session.get(Instance, instance_id)
        if instance is None or instance.agent_id != agent_id:
            raise ApiError(401, "invalid_token", "runtime token is not valid")
        if instance.revoked:
            raise ApiError(403, "instance_revoked", "instance is revoked")
    return agent_id, instance_id


def _pending(
    sessions: sessionmaker[Session], cursor: int, agent_id: str, instance_id: str
) -> list[tuple[int, str, str, str]]:
    """Return new revocation rows this instance should see.

    Args:
        sessions: Session factory.
        cursor: Last delivered id.
        agent_id: Calling agent.
        instance_id: Calling instance.

    Returns:
        Id, kind, subject, and timestamp for each new row.
    """
    visible = or_(
        Revocation.kind == "run_revoked",
        and_(Revocation.agent_id == agent_id, Revocation.kind.in_(_AGENT_KINDS)),
        and_(
            Revocation.kind == "instance_revoked",
            Revocation.agent_id == agent_id,
            Revocation.subject == instance_id,
        ),
    )
    with sessions() as session:
        rows = session.scalars(
            select(Revocation).where(Revocation.id > cursor, visible).order_by(Revocation.id)
        ).all()
        return [(row.id, row.kind, row.subject, _iso(row.created_at)) for row in rows]


def _max_id(sessions: sessionmaker[Session]) -> int:
    """Return the newest revocation id.

    Args:
        sessions: Session factory.

    Returns:
        The highest id, or 0 when the table is empty.
    """
    with sessions() as session:
        current = session.scalar(select(func.max(Revocation.id)))
    return int(current or 0)


def _frame(revocation_id: int, kind: str, subject: str, created_at: str) -> str:
    """Format one server-sent event.

    Args:
        revocation_id: Event id.
        kind: Event name.
        subject: Agent, instance, or run the event names.
        created_at: Timestamp text.

    Returns:
        One SSE frame.
    """
    data = json.dumps({"subject": subject, "created_at": created_at}, separators=(",", ":"))
    return f"id: {revocation_id}\nevent: {kind}\ndata: {data}\n\n"


def list_revocations(
    session: Session,
    *,
    limit: int = _PAGE_DEFAULT,
    cursor: str | None = None,
    kind: str | None = None,
    agent_id: str | None = None,
) -> dict[str, object]:
    """List revocations newest first for admin consoles.

    Args:
        session: Open database session.
        limit: Maximum number of rows to return.
        cursor: Return rows whose id is less than this value (previous page's
            last id when paging newest-first).
        kind: Exact kind filter (`agent_suspended`, `run_revoked`, …).
        agent_id: Limit to revocations whose `agent_id` matches.

    Returns:
        `items` and `next_cursor`. Each item is
        `{id, kind, agent_id, subject, created_at}`.

    Raises:
        ValueError: When `cursor` is not an integer.
    """
    query = select(Revocation).order_by(Revocation.id.desc())
    if cursor is not None:
        query = query.where(Revocation.id < int(cursor))
    if kind is not None:
        query = query.where(Revocation.kind == kind)
    if agent_id is not None:
        query = query.where(Revocation.agent_id == agent_id)
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = str(page[-1].id) if len(rows) > limit else None
    return {
        "items": [_revocation_document(row) for row in page],
        "next_cursor": next_cursor,
    }


def _revocation_document(row: Revocation) -> dict[str, object]:
    """Public admin document for one revocation row.

    Args:
        row: Stored revocation.

    Returns:
        JSON-serializable fields for the console.
    """
    return {
        "id": row.id,
        "kind": row.kind,
        "agent_id": row.agent_id,
        "subject": row.subject,
        "created_at": _iso(row.created_at),
    }


def _iso(moment: datetime) -> str:
    """Format a timestamp as UTC ISO-8601 with millisecond precision.

    Args:
        moment: Stored timestamp. Naive values are treated as UTC.

    Returns:
        A string ending in `Z`.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "get_run",
    "iter_revocations",
    "list_revocations",
    "list_runs",
    "revoke_run",
    "run_document",
    "stream_identity",
]
