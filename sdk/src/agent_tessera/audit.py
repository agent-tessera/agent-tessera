"""Append-only hash-chained audit log."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

GENESIS_HASH = "0" * 64
EVENT_SCHEMA = "tessera.event/v1"
_RESERVED = frozenset({"schema", "seq", "ts", "type", "prev_hash", "hash"})


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Outcome of checking an audit log's hash chain.

    Attributes:
        ok: Whether every event is intact and in order.
        events: How many events were accepted before the first problem.
        error_line: 1-based line of the first problem, when there is one.
        error: Why verification failed, when it failed.
    """

    ok: bool
    events: int
    error_line: int | None = None
    error: str | None = None


class AuditLog:
    """Append JSON events to a file and chain each one to the previous hash.

    The log creates missing parent directories. Reopening a file continues
    the sequence number and the hash chain.

    Args:
        path: File to append events to. Created if it does not exist.

    Example:
        ```python
        from agent_tessera.audit import AuditLog, verify_audit_log

        log = AuditLog("audit.jsonl")
        log.write("lifecycle", {"event": "start"})
        log.close()
        assert verify_audit_log("audit.jsonl").ok
        ```
    """

    def __init__(self, path: str | Path) -> None:
        """Open an audit log, creating parent directories when needed.

        Args:
            path: File to append events to.
        """
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq, self._prev_hash = _resume(self._path)
        self._handle = self._path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        """Return the log file path.

        Returns:
            The path passed to the constructor, as a `Path`.
        """
        return self._path

    def write(self, event_type: str, fields: Mapping[str, object]) -> dict[str, object]:
        """Append one event and return the stored record.

        Args:
            event_type: Event type, such as `decision` or `lifecycle`.
            fields: Extra fields. Must not use reserved record keys.

        Returns:
            The record that was written, including its hash.

        Raises:
            ValueError: If `fields` contains a reserved key.
        """
        overlap = _RESERVED.intersection(fields)
        if overlap:
            name = sorted(overlap)[0]
            raise ValueError(f"reserved field {name}")
        with self._lock:
            record: dict[str, object] = {
                "schema": EVENT_SCHEMA,
                "seq": self._seq,
                "ts": _timestamp(),
                "type": event_type,
                "prev_hash": self._prev_hash,
            }
            record.update(fields)
            digest = _hash(self._prev_hash, record)
            record["hash"] = digest
            self._handle.write(_canonical_json(record) + "\n")
            self._handle.flush()
            self._seq += 1
            self._prev_hash = digest
            return dict(record)

    def close(self) -> None:
        """Close the log file."""
        self._handle.close()


def verify_audit_log(path: str | Path) -> VerifyResult:
    """Recompute every hash and check that the sequence is intact.

    Args:
        path: Audit log to read.

    Returns:
        A result that names the first bad line, or confirms the chain.

    Example:
        ```python
        from agent_tessera.audit import verify_audit_log

        result = verify_audit_log("missing.jsonl")
        assert result.ok is False
        assert result.error == "file not found"
        ```
    """
    file = Path(path)
    if not file.is_file():
        return VerifyResult(ok=False, events=0, error="file not found")
    previous = GENESIS_HASH
    expected_seq = 1
    events = 0
    with file.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            failure = _check_line(line, line_number, expected_seq, previous, events)
            if isinstance(failure, VerifyResult):
                return failure
            previous = failure
            expected_seq += 1
            events += 1
    return VerifyResult(ok=True, events=events)


def _check_line(
    line: str,
    line_number: int,
    expected_seq: int,
    previous: str,
    events: int,
) -> VerifyResult | str:
    """Validate one log line.

    Args:
        line: Raw line, including its trailing newline if present.
        line_number: 1-based position in the file.
        expected_seq: Sequence number this event must have.
        previous: Hash the event must chain from.
        events: Events accepted before this line.

    Returns:
        The event hash when the line is valid, otherwise a failed result.
    """
    record = _parse_record(line)
    error = _line_error(record, expected_seq, previous)
    if error is not None or record is None:
        return _failed(events, line_number, error or "invalid JSON")
    return cast(str, record["hash"])


def _line_error(
    record: dict[str, object] | None,
    expected_seq: int,
    previous: str,
) -> str | None:
    """Return why a parsed event is invalid.

    Args:
        record: Parsed event, or `None` when the line is not JSON.
        expected_seq: Sequence number this event must have.
        previous: Hash the event must chain from.

    Returns:
        An error message, or `None` when the event is valid.
    """
    if record is None:
        return "invalid JSON"
    seq = record.get("seq")
    prev_hash = record.get("prev_hash")
    stored = record.get("hash")
    payload = {key: value for key, value in record.items() if key != "hash"}
    if isinstance(seq, bool) or not isinstance(seq, int) or seq != expected_seq:
        return "sequence mismatch"
    if not isinstance(prev_hash, str) or prev_hash != previous:
        return "prev_hash mismatch"
    if not isinstance(stored, str) or stored != _hash(previous, payload):
        return "hash mismatch"
    return None


def _failed(events: int, line_number: int, error: str) -> VerifyResult:
    """Build a failed verification result.

    Args:
        events: Events accepted before the failure.
        line_number: 1-based line that failed.
        error: Why the line failed.

    Returns:
        A result with `ok` set to false.
    """
    return VerifyResult(ok=False, events=events, error_line=line_number, error=error)


def _parse_record(line: str) -> dict[str, object] | None:
    """Parse one JSON object line.

    Args:
        line: Raw log line.

    Returns:
        The object, or `None` when the line is not a JSON object.
    """
    try:
        loaded: object = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    return {str(key): value for key, value in loaded.items()}


def _resume(path: Path) -> tuple[int, str]:
    """Read the last event so a reopened log continues the chain.

    Args:
        path: Log file. May be missing or empty.

    Returns:
        The next sequence number and the previous event hash.

    Raises:
        ValueError: If the last non-empty line is not a finished event.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return 1, GENESIS_HASH
    last = ""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = line
    record = _parse_record(last)
    if record is None:
        raise ValueError("audit log is corrupt")
    seq = record.get("seq")
    digest = record.get("hash")
    if isinstance(seq, bool) or not isinstance(seq, int) or not isinstance(digest, str):
        raise ValueError("audit log is corrupt")
    return seq + 1, digest


def _timestamp() -> str:
    """Return the current UTC time with millisecond precision.

    Returns:
        An ISO-8601 timestamp ending in `Z`.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    """Serialize a value with sorted keys and compact separators.

    Args:
        value: JSON-compatible value. Other objects are stringified.

    Returns:
        The canonical JSON text.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _hash(previous: str, record: Mapping[str, object]) -> str:
    """Hash a record chained to the previous digest.

    Args:
        previous: Hash of the previous event, or the genesis hash.
        record: Event fields, without the `hash` field.

    Returns:
        A 64-character hex digest.
    """
    payload = previous + _canonical_json(record)
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = ["EVENT_SCHEMA", "GENESIS_HASH", "AuditLog", "VerifyResult", "verify_audit_log"]
