"""Hash-chained audit log for control-plane actions."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_tessera.audit import GENESIS_HASH
from tessera_server.db.models import AuditEntry, utcnow
from tessera_server.db.session import advisory_xact_lock

_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class AuditCheck:
    """Outcome of recomputing the server audit chain.

    Attributes:
        ok: Whether every row is intact and in order.
        events: How many rows were accepted before the first problem.
        error_seq: Sequence number of the first bad row, when there is one.
        error: Why verification failed, when it failed.
    """

    ok: bool
    events: int
    error_seq: int | None = None
    error: str | None = None


def append_audit(
    session: Session,
    *,
    actor: str,
    entry_type: str,
    payload: Mapping[str, object],
    now: Callable[[], datetime] | None = None,
) -> AuditEntry:
    """Append one row and commit the session.

    Args:
        session: Open session. Uncommitted work in it is committed with the row.
        actor: Who performed the action, such as `admin:<id>`, `dev`, or `cli`.
        entry_type: Short name of the action.
        payload: JSON details. Secrets do not belong here.
        now: Clock for the row timestamp.

    Returns:
        The stored row.
    """
    with exclusive_audit(session):
        return _insert(session, actor, entry_type, dict(payload), now or utcnow)


@contextmanager
def exclusive_audit(session: Session) -> Iterator[None]:
    """Serialize audit writes and commit the session.

    Args:
        session: Open session. Uncommitted work is committed with the new rows.

    Yields:
        Control while the process lock and the database lock are held.
    """
    with _LOCK:
        try:
            advisory_xact_lock(session)
            yield
            session.commit()
        except Exception:
            session.rollback()
            raise


def record_audit(
    session: Session,
    *,
    actor: str,
    entry_type: str,
    payload: Mapping[str, object],
    now: Callable[[], datetime] | None = None,
) -> AuditEntry:
    """Add one audit row without committing.

    Args:
        session: Open session inside `exclusive_audit`.
        actor: Who performed the action.
        entry_type: Short name of the action.
        payload: JSON details.
        now: Clock for the row timestamp.

    Returns:
        The pending row.
    """
    return _insert(session, actor, entry_type, dict(payload), now or utcnow)


def list_audit(
    session: Session, *, limit: int = 50, cursor: str | None = None
) -> dict[str, object]:
    """List the server audit chain in sequence order.

    Args:
        session: Open database session.
        limit: Maximum number of rows to return.
        cursor: Return rows whose sequence number is greater than this value.

    Returns:
        `items` and `next_cursor`.

    Raises:
        ValueError: If `cursor` is not an integer.
    """
    query = select(AuditEntry).order_by(AuditEntry.seq)
    if cursor is not None:
        query = query.where(AuditEntry.seq > int(cursor))
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = str(page[-1].seq) if len(rows) > limit else None
    return {"items": [_public(row) for row in page], "next_cursor": next_cursor}


def verify_audit(session: Session) -> AuditCheck:
    """Recompute every audit hash.

    Args:
        session: Open database session.

    Returns:
        A result that names the first bad sequence number, or confirms the chain.
    """
    rows = session.scalars(select(AuditEntry).order_by(AuditEntry.seq)).all()
    previous = GENESIS_HASH
    events = 0
    for row in rows:
        record = _record(row, previous)
        digest = _digest(previous, record)
        if row.seq != events + 1:
            return AuditCheck(False, events, row.seq, "sequence mismatch")
        if row.prev_hash != previous or row.row_hash != digest:
            problem = "prev_hash mismatch" if row.prev_hash != previous else "hash mismatch"
            return AuditCheck(False, events, row.seq, problem)
        previous = digest
        events += 1
    return AuditCheck(True, events)


def _insert(
    session: Session,
    actor: str,
    entry_type: str,
    payload: dict[str, object],
    clock: Callable[[], datetime],
) -> AuditEntry:
    """Add the next audit row without committing.

    Args:
        session: Open database session.
        actor: Who performed the action.
        entry_type: Short name of the action.
        payload: JSON details.
        clock: Clock for the row timestamp.

    Returns:
        The pending row.
    """
    current = session.scalar(select(func.max(AuditEntry.seq)))
    seq = 1 if current is None else int(current) + 1
    previous = GENESIS_HASH
    if current is not None:
        prior = session.get(AuditEntry, int(current))
        previous = GENESIS_HASH if prior is None else prior.row_hash
    moment = _aware(clock())
    row = AuditEntry(
        seq=seq,
        ts=moment,
        actor=actor,
        entry_type=entry_type,
        payload=payload,
        prev_hash=previous,
        row_hash="",
    )
    row.row_hash = _digest(previous, _record(row, previous))
    session.add(row)
    return row


def _public(row: AuditEntry) -> dict[str, object]:
    """Serialize one audit row.

    Args:
        row: Stored audit row.

    Returns:
        The row's public fields, including its hash.
    """
    return {
        "seq": row.seq,
        "ts": _text(_aware(row.ts)),
        "actor": row.actor,
        "type": row.entry_type,
        "payload": row.payload,
        "prev_hash": row.prev_hash,
        "hash": row.row_hash,
    }


def _record(row: AuditEntry, previous: str) -> dict[str, object]:
    """Build the hashed view of one row.

    Args:
        row: Stored audit row.
        previous: Hash the row must chain from.

    Returns:
        The record without its own hash.
    """
    return {
        "actor": row.actor,
        "payload": row.payload,
        "prev_hash": previous,
        "seq": row.seq,
        "ts": _text(_aware(row.ts)),
        "type": row.entry_type,
    }


def _digest(previous: str, record: Mapping[str, object]) -> str:
    """Hash a record chained to the previous digest.

    Args:
        previous: Hash of the previous row, or the genesis hash.
        record: Row fields, without the hash.

    Returns:
        A 64-character hex digest.
    """
    body = previous + json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _aware(moment: datetime) -> datetime:
    """Return a UTC timestamp with millisecond precision.

    Args:
        moment: Clock reading. Naive values are treated as UTC.

    Returns:
        A timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


def _text(moment: datetime) -> str:
    """Format a UTC timestamp for the hash.

    Args:
        moment: Timezone-aware UTC datetime.

    Returns:
        An ISO-8601 string ending in `Z`.
    """
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "AuditCheck",
    "append_audit",
    "exclusive_audit",
    "list_audit",
    "record_audit",
    "verify_audit",
]
