"""Ingest agent events and list them."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, Event, Instance, Run, RunParticipant, utcnow
from tessera_server.services.audit import exclusive_audit, record_audit
from tessera_server.services.effects import store_effect
from tessera_server.services.event_index import index_fields
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.quotas import trip_circuit
from tessera_server.services.registry import _check_supervisor

_MAX_EVENTS = 500
_ID_LENGTH = 32
_TYPE_LIMIT = 64
_PAGE_DEFAULT = 50


def ingest_events(
    session: Session,
    settings: Settings,
    token: str,
    events: Sequence[Mapping[str, object]],
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, int]:
    """Store new events and append each one to the audit chain.

    Args:
        session: Open database session.
        settings: Issuer used to verify the runtime token.
        token: Runtime JWT for the uploading instance.
        events: Event documents. At most 500.
        now: Clock for `received_at` and audit rows.

    Returns:
        How many events were stored and how many were duplicates.

    Raises:
        ApiError: Status 401 when the token is invalid, 403 when the instance
            is revoked, 413 when the batch is too large, or 422 when an event
            is malformed. After a stored batch that contains a denial, the
            circuit breaker may suspend the agent or revoke the run.
    """
    if len(events) > _MAX_EVENTS:
        raise ApiError(413, "payload_too_large", "event batch exceeds 500 events")
    moment = (now or utcnow)()
    saw_denial = False
    with exclusive_audit(session):
        claims = _claims(session, settings, token, moment)
        agent = session.get(Agent, claims["agent_id"])
        instance = session.get(Instance, claims["instance_id"])
        if agent is None or instance is None or instance.agent_id != agent.id:
            raise ApiError(401, "invalid_token", "runtime token is not valid")
        if instance.revoked:
            raise ApiError(403, "instance_revoked", "instance is revoked")
        prepared = [_prepare(event, agent, instance.id) for event in events]
        known = _known_ids(session, [str(item["id"]) for item in prepared])
        accepted = 0
        duplicates = 0
        actor = claims["sub"]
        agent_id = agent.id
        for item in prepared:
            if _is_denial(item):
                saw_denial = True
            event_id = str(item["id"])
            if event_id in known:
                duplicates += 1
                continue
            known.add(event_id)
            _store(session, item, agent.id, moment)
            record_audit(
                session,
                actor=actor,
                entry_type=str(item["type"]),
                payload=item,
                now=lambda: moment,
            )
            accepted += 1
        result = {"accepted": accepted, "duplicates": duplicates}
    if saw_denial:
        trip_circuit(session, agent_id, moment)
    return result


def list_events(
    session: Session,
    *,
    agent_id: str | None = None,
    event_type: str | None = None,
    task: str | None = None,
    since: datetime | None = None,
    limit: int = _PAGE_DEFAULT,
    cursor: str | None = None,
    direction: str = "asc",
) -> dict[str, object]:
    """List ingested events.

    Args:
        session: Open database session.
        agent_id: Filter by agent.
        event_type: Filter by event type.
        task: Filter by task id.
        since: Include events at or after this time.
        limit: Maximum number of rows to return.
        cursor: Continue after (or before, when `direction` is `desc`) this id.
        direction: `asc` for ingestion order (API default), `desc` for newest
            first (admin UI).

    Returns:
        `items` and `next_cursor`.
    """
    newest_first = direction == "desc"
    if newest_first:
        query = select(Event).order_by(Event.received_at.desc(), Event.id.desc())
    else:
        query = select(Event).order_by(Event.received_at, Event.id)
    if agent_id is not None:
        query = query.where(Event.agent_id == agent_id)
    if event_type is not None:
        query = query.where(Event.event_type == event_type)
    if task is not None:
        query = query.where(Event.task == task)
    if since is not None:
        query = query.where(Event.ts >= _aware(since))
    if cursor is not None:
        marker = session.get(Event, cursor)
        if marker is None:
            query = (
                query.where(Event.id > cursor)
                if not newest_first
                else query.where(Event.id < cursor)
            )
        elif newest_first:
            query = query.where(
                (Event.received_at < marker.received_at)
                | ((Event.received_at == marker.received_at) & (Event.id < marker.id))
            )
        else:
            query = query.where(
                (Event.received_at > marker.received_at)
                | ((Event.received_at == marker.received_at) & (Event.id > marker.id))
            )
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = page[-1].id if len(rows) > limit else None
    return {"items": [dict(row.payload) for row in page], "next_cursor": next_cursor}


_LIVE_TYPES = ("call", "decision")
_LIVE_LIMIT = 50


def list_live_events(
    session: Session,
    *,
    agent_id: str | None = None,
    task: str | None = None,
) -> dict[str, object]:
    """Return the most recent call and decision events.

    Args:
        session: Open database session.
        agent_id: Limit the page to this agent.
        task: Limit the page to this run id.

    Returns:
        `items`, newest first, at most 50. Other event types are omitted.

    Example:
        ```python
        page = list_live_events(session, agent_id=None, task=None)
        assert len(page["items"]) <= 50
        ```
    """
    query = (
        select(Event)
        .where(Event.event_type.in_(_LIVE_TYPES))
        .order_by(Event.ts.desc(), Event.id.desc())
    )
    if agent_id is not None:
        query = query.where(Event.agent_id == agent_id)
    if task is not None:
        query = query.where(Event.task == task)
    rows = session.scalars(query.limit(_LIVE_LIMIT)).all()
    return {"items": [dict(row.payload) for row in rows]}


def _is_denial(event: Mapping[str, object]) -> bool:
    """Return whether an event counts toward the circuit breaker.

    Args:
        event: Prepared event document.

    Returns:
        `True` for a decision whose effect is `deny` and whose reason is not
        `circuit_open`.
    """
    return (
        event.get("type") == "decision"
        and event.get("effect") == "deny"
        and event.get("reason") != "circuit_open"
    )


def _claims(session: Session, settings: Settings, token: str, moment: datetime) -> dict[str, str]:
    """Verify a runtime token and read its identity claims.

    Args:
        session: Open database session.
        settings: Expected issuer.
        token: Compact JWT.
        moment: Current time.

    Returns:
        `agent_id`, `instance_id`, and `sub`.

    Raises:
        ApiError: Status 401 when the token or its claims are not usable.
    """
    try:
        claims = verify_runtime_token(session, settings, token, now=lambda: moment)
    except ValueError as err:
        raise ApiError(401, "invalid_token", "runtime token is not valid") from err
    agent_id = claims.get("agent_id")
    instance_id = claims.get("inst")
    subject = claims.get("sub")
    if not isinstance(agent_id, str) or not isinstance(instance_id, str):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    if not isinstance(subject, str):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return {"agent_id": agent_id, "instance_id": instance_id, "sub": subject}


def _prepare(event: Mapping[str, object], agent: Agent, instance_id: str) -> dict[str, object]:
    """Copy an event and replace its identity with the token's.

    Args:
        event: Uploaded event.
        agent: Agent the token represents.
        instance_id: Instance id from the token.

    Returns:
        The event that will be stored.

    Raises:
        ApiError: Status 422 when `id`, `ts`, or `type` is unusable.
    """
    event_id = event.get("id")
    event_type = event.get("type")
    if not isinstance(event_id, str) or len(event_id) != _ID_LENGTH or not _hex(event_id):
        raise ApiError(422, "invalid_request", "event id must be 32 hex characters")
    if not isinstance(event_type, str) or event_type == "" or len(event_type) > _TYPE_LIMIT:
        raise ApiError(422, "invalid_request", "event type is required")
    _parse_ts(event.get("ts"))
    task = event.get("task")
    if task is not None and not isinstance(task, str):
        raise ApiError(422, "invalid_request", "event task must be a string")
    raw_supervisor = event.get("supervisor")
    if raw_supervisor is not None:
        if not isinstance(raw_supervisor, str):
            raise ApiError(422, "invalid_request", "supervisor must be user: or group:")
        _check_supervisor(raw_supervisor)
    stored = dict(event)
    stored["agent"] = agent.name
    stored["environment"] = agent.environment
    stored["instance"] = instance_id
    return stored


def _store(session: Session, event: Mapping[str, object], agent_id: str, moment: datetime) -> None:
    """Insert an event and update its run.

    Args:
        session: Open database session.
        event: Prepared event.
        agent_id: Agent the event belongs to.
        moment: Receipt time.
    """
    task = event.get("task")
    task_id = task if isinstance(task, str) else None
    indexed = index_fields(event)
    session.add(
        Event(
            id=str(event["id"]),
            agent_id=agent_id,
            instance_id=str(event["instance"]),
            event_type=str(event["type"]),
            ts=_parse_ts(event.get("ts")),
            task=task_id,
            run_id=indexed["run_id"],
            span_id=indexed["span_id"],
            parent_span_id=indexed["parent_span_id"],
            kind=indexed["kind"],
            resource=indexed["resource"],
            effect=indexed["effect"],
            status=indexed["status"],
            rule=indexed["rule"],
            payload=dict(event),
            received_at=moment,
        )
    )
    if task_id is None:
        return
    run = session.get(Run, task_id)
    if run is None:
        run = Run(id=task_id, status="running", first_seen_at=moment, last_seen_at=moment)
        session.add(run)
    else:
        run.last_seen_at = moment
    _bind_supervisor(run, event)
    _bind_principal(run, event)
    instance_id = str(event["instance"])
    key = {"run_id": task_id, "instance_id": instance_id}
    if session.get(RunParticipant, key) is None:
        session.add(RunParticipant(run_id=task_id, instance_id=instance_id, agent_id=agent_id))
    store_effect(session, dict(event), agent_id, moment)


def _bind_supervisor(run: Run, event: Mapping[str, object]) -> None:
    """Set a run supervisor from the first event that names one.

    Args:
        run: Run for the event's task.
        event: Prepared event. A later event does not replace a supervisor.
    """
    if run.supervisor:
        return
    value = event.get("supervisor")
    if isinstance(value, str) and value:
        run.supervisor = value


def _bind_principal(run: Run, event: Mapping[str, object]) -> None:
    """Set a run principal from the first event that names one.

    Args:
        run: Run for the event's task.
        event: Prepared event. A later event does not replace a principal.
            String principals are stored as-is; object principals use
            ``user``.
    """
    if run.principal:
        return
    value = event.get("principal")
    if isinstance(value, str) and value.strip():
        run.principal = value.strip()
        return
    if isinstance(value, Mapping):
        user = value.get("user")
        if isinstance(user, str) and user.strip():
            run.principal = user.strip()


def _known_ids(session: Session, ids: list[str]) -> set[str]:
    """Return which event ids are already stored.

    Args:
        session: Open database session.
        ids: Ids in the current batch.

    Returns:
        The subset that already exists.
    """
    if not ids:
        return set()
    found = session.scalars(select(Event.id).where(Event.id.in_(ids))).all()
    return set(found)


def _parse_ts(value: object) -> datetime:
    """Parse an event timestamp.

    Args:
        value: ISO-8601 string. A trailing `Z` is UTC.

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        ApiError: Status 422 when the value is not a timestamp.
    """
    if not isinstance(value, str):
        raise ApiError(422, "invalid_request", "event ts is required")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ApiError(422, "invalid_request", "event ts is required") from err
    return _aware(moment)


def _aware(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    Args:
        moment: Parsed time.

    Returns:
        A timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _hex(value: str) -> bool:
    """Return whether a string is lowercase hexadecimal.

    Args:
        value: Candidate event id.

    Returns:
        `True` when every character is a hex digit.
    """
    return all(character in "0123456789abcdef" for character in value)


__all__ = ["ingest_events", "list_events", "list_live_events"]
