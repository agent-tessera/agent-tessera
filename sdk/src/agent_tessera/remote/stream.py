"""Server-sent revocation stream."""

from __future__ import annotations

import json
import logging
import random
import threading
from collections.abc import Callable

import httpx

logger = logging.getLogger("agent_tessera")

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0
_READ_TIMEOUT = 60.0


class RevocationListener:
    """Follow `GET /v1/revocations/stream` and reconnect after a drop.

    The first connection starts at the server's current end. Later connections
    send `Last-Event-ID`. The wait between attempts starts at one second and
    doubles up to thirty, with a random factor between one half and the full delay.

    Args:
        http: Open control-plane client.
        token: Returns the current runtime JWT, or `None`.
        on_event: Called with the event name and its `subject`.
    """

    def __init__(
        self,
        http: httpx.Client,
        token: Callable[[], str | None],
        on_event: Callable[[str, str], None],
    ) -> None:
        """Store the client and the callback.

        Args:
            http: Open control-plane client.
            token: Returns the current runtime JWT.
            on_event: Called for each event that names a subject.
        """
        self._http = http
        self._token = token
        self._on_event = on_event
        self._last_id: int | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background reader."""
        thread = threading.Thread(target=self._loop, name="tessera-revocations", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Ask the reader to exit. It unblocks when the HTTP client closes."""
        self._stop.set()

    def join(self) -> None:
        """Wait for the reader to exit."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
            self._thread = None

    def _loop(self) -> None:
        """Read until stopped, waiting longer after each failed connection."""
        delay = INITIAL_BACKOFF
        while not self._stop.is_set():
            try:
                self._consume()
            except httpx.HTTPError:
                logger.warning("revocation stream disconnected")
            if self._stop.is_set():
                return
            # Jitter spreads reconnects. It is not a secret.
            wait = delay * (0.5 + random.random() * 0.5)  # noqa: S311
            if self._stop.wait(wait):
                return
            delay = min(delay * 2, MAX_BACKOFF)

    def _consume(self) -> None:
        """Read one stream until it ends or the listener is stopped."""
        token = self._token()
        if token is None:
            return
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        }
        if self._last_id is not None:
            headers["Last-Event-ID"] = str(self._last_id)
        with self._http.stream(
            "GET",
            "/v1/revocations/stream",
            headers=headers,
            timeout=_timeout(self._http),
        ) as response:
            if response.status_code != httpx.codes.OK:
                return
            self._ready.set()
            frame: dict[str, str] = {}
            for line in response.iter_lines():
                if self._stop.is_set():
                    return
                if line == "":
                    self._dispatch(frame)
                    frame = {}
                    continue
                _read_field(line, frame)

    def _dispatch(self, frame: dict[str, str]) -> None:
        """Deliver one completed event.

        Args:
            frame: Fields collected since the previous blank line.
        """
        raw_id = frame.get("id")
        if raw_id is not None:
            try:
                self._last_id = int(raw_id)
            except ValueError:
                logger.warning("revocation stream sent a non-numeric id")
        kind = frame.get("event")
        raw = frame.get("data")
        if kind is None or raw is None:
            return
        try:
            loaded: object = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("revocation stream sent data that is not JSON")
            return
        if not isinstance(loaded, dict):
            return
        subject = loaded.get("subject")
        if isinstance(subject, str):
            self._on_event(kind, subject)


def _read_field(line: str, frame: dict[str, str]) -> None:
    """Store one SSE field.

    Args:
        line: One line, without its terminator.
        frame: Fields for the current event.
    """
    if line.startswith(":"):
        return
    field, separator, value = line.partition(":")
    if separator != ":":
        return
    if value.startswith(" "):
        value = value[1:]
    if field in {"id", "event", "data"}:
        frame[field] = value


def _timeout(client: httpx.Client) -> httpx.Timeout:
    """Build a stream timeout that waits through server keepalives.

    Args:
        client: Control-plane client.

    Returns:
        A timeout whose read budget is sixty seconds.
    """
    connect = client.timeout.connect
    budget = 10.0 if connect is None else float(connect)
    return httpx.Timeout(connect=budget, read=_READ_TIMEOUT, write=budget, pool=budget)


__all__ = ["INITIAL_BACKOFF", "MAX_BACKOFF", "RevocationListener"]
