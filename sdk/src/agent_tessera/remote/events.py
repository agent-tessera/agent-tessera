"""Queue audit events for upload and spool them when the server is down."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from agent_tessera.remote.http import RemoteError, request

logger = logging.getLogger("agent_tessera")

SEGMENT_BYTES = 5 * 1024 * 1024
SPOOL_BYTES = 100 * 1024 * 1024
BATCH_LIMIT = 200
FLUSH_INTERVAL = 2.0
_MAX_BATCH_BYTES = 900_000
_RETRY_PAUSE = 0.05


@dataclass(frozen=True, slots=True)
class _Taken:
    """One batch read from the spool and the memory queue.

    Attributes:
        events: Events to upload, oldest first.
        files: How many events were taken from each spool file.
        memory: How many events were taken from the memory queue.
    """

    events: tuple[dict[str, object], ...]
    files: tuple[tuple[Path, int], ...]
    memory: int


class EventUploader:
    """Send events to the control plane and keep a disk spool per agent.

    Segments are `<spool>/<agent>/<instance>-<n>.jsonl`, each at most 5 MB.
    The spool for one agent is capped at 100 MB; the oldest segment is deleted
    past that cap. Segments left by earlier instances are uploaded on start.

    Args:
        http: Open control-plane client.
        token: Returns the current runtime JWT, or `None` when there is none.
        agent: Agent slug. Used as the spool directory name.
        instance_id: This process's instance id.
        spool_dir: Root spool directory.
    """

    def __init__(
        self,
        http: httpx.Client,
        token: Callable[[], str | None],
        agent: str,
        instance_id: str,
        spool_dir: Path,
    ) -> None:
        """Remember the client and open this instance's next segment index.

        Args:
            http: Open control-plane client.
            token: Returns the current runtime JWT.
            agent: Agent slug.
            instance_id: This process's instance id.
            spool_dir: Root spool directory.
        """
        self._http = http
        self._token = token
        self._instance_id = instance_id
        self._directory = spool_dir / agent
        self._index = _next_index(self._directory, instance_id)
        self._memory: list[dict[str, object]] = []
        self._state = threading.Lock()
        self._upload = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(self, event: Mapping[str, object]) -> None:
        """Queue one event. Two hundred queued events wake the flusher.

        Args:
            event: Event document, including `id`, `ts`, and `type`.
        """
        with self._state:
            self._memory.append(dict(event))
            pending = len(self._memory) >= BATCH_LIMIT
        if pending:
            self._wake.set()

    def start(self) -> None:
        """Upload events left by earlier instances, then start the flusher."""
        self._recover()
        thread = threading.Thread(target=self._loop, name="tessera-events", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Stop the flusher thread. Queued events stay until `flush`."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1)
            self._thread = None

    def flush(self, timeout: float) -> bool:
        """Upload queued and spooled events.

        Args:
            timeout: How long to keep trying, in seconds.

        Returns:
            `True` when nothing remains queued or spooled.
        """
        deadline = time.monotonic() + max(timeout, 0)
        while self._pending():
            if self._upload_once():
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(_RETRY_PAUSE)
        return True

    def _recover(self) -> None:
        """Upload spool files left on disk until one attempt fails."""
        while self._pending():
            if not self._upload_once():
                return

    def _loop(self) -> None:
        """Upload on a timer and when the queue reaches a batch."""
        while not self._stop.is_set():
            self._wake.wait(FLUSH_INTERVAL)
            self._wake.clear()
            if self._stop.is_set():
                return
            while self._pending() and not self._stop.is_set():
                if not self._upload_once():
                    break

    def _upload_once(self) -> bool:
        """Send one batch. Failed memory events are written to the spool.

        Returns:
            `True` when the batch was accepted or there was nothing to send.
        """
        with self._upload:
            with self._state:
                taken = self._peek()
            if not taken.events:
                return True
            if not self._post(taken.events):
                with self._state:
                    self._spill()
                return False
            with self._state:
                self._commit(taken)
            return True

    def _post(self, events: tuple[dict[str, object], ...] | list[dict[str, object]]) -> bool:
        """Post one batch.

        Args:
            events: Event documents.

        Returns:
            `True` when the server accepts the batch.
        """
        token = self._token()
        if token is None:
            return False
        try:
            response = request(
                self._http,
                "POST",
                "/v1/events",
                json_body={"events": list(events)},
                headers={"Authorization": f"Bearer {token}"},
            )
        except RemoteError:
            return False
        return response.status_code == httpx.codes.OK

    def _pending(self) -> bool:
        """Return whether memory or the spool still holds events.

        Returns:
            `True` when an upload could make progress.
        """
        with self._state:
            return bool(self._memory) or bool(self._segments())

    def _peek(self) -> _Taken:
        """Read the oldest events without removing them.

        Returns:
            The batch and where it came from. Call with the state lock held.
        """
        events: list[dict[str, object]] = []
        files: list[tuple[Path, int]] = []
        used = 0
        for path in self._segments():
            take, used = _take_rows(_rows(path), events, used)
            if take:
                files.append((path, take))
            if len(events) >= BATCH_LIMIT or used >= _MAX_BATCH_BYTES:
                break
        memory = 0
        if len(events) < BATCH_LIMIT and used < _MAX_BATCH_BYTES:
            memory, _used = _take_rows(self._memory, events, used)
        return _Taken(tuple(events), tuple(files), memory)

    def _commit(self, taken: _Taken) -> None:
        """Drop a batch that the server accepted.

        Args:
            taken: Batch returned by `_peek`. Call with the state lock held.
        """
        del self._memory[: taken.memory]
        for path, count in taken.files:
            _drop_prefix(path, count)

    def _spill(self) -> None:
        """Append the memory queue to the spool. Call with the state lock held."""
        if not self._memory:
            return
        pending = self._memory
        self._memory = []
        self._directory.mkdir(parents=True, exist_ok=True)
        for event in pending:
            line = _line(event)
            path = self._segment_path()
            size = path.stat().st_size if path.is_file() else 0
            encoded = len(line.encode())
            if size > 0 and size + encoded > SEGMENT_BYTES:
                self._index += 1
                path = self._segment_path()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            path.chmod(0o600)
        self._trim()

    def _segment_path(self) -> Path:
        """Return the segment currently being written.

        Returns:
            `<instance>-<n>.jsonl` inside the agent directory.
        """
        return self._directory / f"{self._instance_id}-{self._index}.jsonl"

    def _segments(self) -> list[Path]:
        """List spool files, oldest first.

        Returns:
            JSONL segments in this agent's directory.
        """
        if not self._directory.is_dir():
            return []
        paths = [path for path in self._directory.glob("*.jsonl") if path.is_file()]
        return sorted(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))

    def _trim(self) -> None:
        """Delete oldest segments until the agent spool is within the cap."""
        files = self._segments()
        total = sum(path.stat().st_size for path in files)
        while total > SPOOL_BYTES and files:
            oldest = files.pop(0)
            dropped = _count(oldest)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
            logger.warning("dropped %s spooled events", dropped)


def remote_event(
    event_type: str, payload: Mapping[str, object], record: Mapping[str, object] | None
) -> dict[str, object]:
    """Build the document uploaded for one local audit event.

    Args:
        event_type: Event type, such as `decision` or `lifecycle`.
        payload: Identity and type-specific fields.
        record: Local audit record, when one was written. Its timestamp is
            reused. Chain fields are left out.

    Returns:
        An event with a new `id`, ready for `POST /v1/events`.
    """
    if record is None:
        body: dict[str, object] = {"ts": _timestamp(), "type": event_type}
        body.update(payload)
    else:
        body = {
            key: value
            for key, value in record.items()
            if key not in {"schema", "seq", "prev_hash", "hash"}
        }
    body["id"] = uuid4().hex
    return body


def _take_rows(
    rows: list[dict[str, object]],
    events: list[dict[str, object]],
    used: int,
) -> tuple[int, int]:
    """Append rows until the batch limits.

    Args:
        rows: Candidate events, oldest first.
        events: Batch being built.
        used: Bytes already in the batch.

    Returns:
        How many rows were taken, and the new byte total.
    """
    taken = 0
    for row in rows:
        size = len(_line(row).encode())
        if events and (len(events) >= BATCH_LIMIT or used + size > _MAX_BATCH_BYTES):
            break
        events.append(row)
        used += size
        taken += 1
        if len(events) >= BATCH_LIMIT or used >= _MAX_BATCH_BYTES:
            break
    return taken, used


def _rows(path: Path) -> list[dict[str, object]]:
    """Read events from one segment.

    Args:
        path: JSONL file.

    Returns:
        The objects in the file. Lines that are not objects are skipped.
    """
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded: object = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipped a spool line that is not JSON")
            continue
        if isinstance(loaded, dict):
            rows.append({str(key): value for key, value in loaded.items()})
    return rows


def _drop_prefix(path: Path, count: int) -> None:
    """Remove the first events from a segment.

    Args:
        path: JSONL file. Missing files are ignored.
        count: How many non-empty lines to drop.
    """
    if not path.is_file():
        return
    kept: list[str] = []
    seen = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if seen < count:
            seen += 1
            continue
        kept.append(line)
    if not kept:
        path.unlink(missing_ok=True)
        return
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    path.chmod(0o600)


def _count(path: Path) -> int:
    """Count events in a segment.

    Args:
        path: JSONL file.

    Returns:
        The number of non-empty lines.
    """
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _next_index(directory: Path, instance_id: str) -> int:
    """Choose a segment index that does not overwrite an existing file.

    Args:
        directory: Agent spool directory. It may not exist yet.
        instance_id: This process's instance id.

    Returns:
        One past the highest `<instance>-<n>.jsonl` index, or zero.
    """
    if not directory.is_dir():
        return 0
    prefix = f"{instance_id}-"
    highest = -1
    for path in directory.glob(f"{prefix}*.jsonl"):
        suffix = path.name[len(prefix) : -len(".jsonl")]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def _line(event: Mapping[str, object]) -> str:
    """Serialize one event as a single JSON line.

    Args:
        event: Event document.

    Returns:
        JSON with a trailing newline.
    """
    return (
        json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        + "\n"
    )


def _timestamp() -> str:
    """Return the current UTC time with millisecond precision.

    Returns:
        An ISO-8601 timestamp ending in `Z`.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "BATCH_LIMIT",
    "FLUSH_INTERVAL",
    "SEGMENT_BYTES",
    "SPOOL_BYTES",
    "EventUploader",
    "remote_event",
]
