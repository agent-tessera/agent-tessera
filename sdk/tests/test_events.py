"""Server mode uploads events and spools them when the server is down."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import httpx
import pytest

from agent_tessera import Tessera
from agent_tessera.remote import events as events_module
from agent_tessera.remote.events import EventUploader
from agent_tessera.types import Action


class _Server(Protocol):
    """The fields the event tests read from the live server."""

    url: str
    headers: dict[str, str]


def test_events_arrive_in_order_and_close_uploads_stop(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    recorded = _record(monkeypatch)
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token) as client:
        client.check(Action.tool("files.read"))
        client.check(Action.tool("files.write"))
        assert client.flush(2)
    assert not (tmp_path / ".tessera" / "audit.jsonl").exists()
    wanted = [str(event["id"]) for event in recorded]
    assert _uploaded_ids(_audit(live_server), set(wanted)) == wanted
    assert recorded[0]["event"] == "start"
    assert recorded[-1]["event"] == "stop"


def test_batch_wakes_the_uploader(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(events_module, "BATCH_LIMIT", 1)
    monkeypatch.setattr(events_module, "FLUSH_INTERVAL", 30)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token) as client:
        client.check(Action.tool("files.read"))
        deadline = time.monotonic() + 2
        found = False
        while time.monotonic() < deadline:
            found = _has_resource(_audit(live_server), "files.read")
            if found:
                break
            time.sleep(0.05)
        assert found


def test_outage_spools_then_uploads_without_duplicates(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TESSERA_CONNECT_TIMEOUT", "0.3")
    recorded = _record(monkeypatch)
    client = Tessera("billing", server_url=live_server.url, agent_token=live_server.token)
    try:
        client.check(Action.tool("files.read"))
        assert client.flush(2)
        live_server.stop()
        client.check(Action.tool("files.write"))
        client.check(Action.tool("files.list"))
        assert client.flush(0.5) is False
        assert list((tmp_path / "spool" / "billing").glob("*.jsonl"))
        live_server.restart()
        # After a restart the port can take a moment; flush until empty or timeout.
        flushed = False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if client.flush(1.5):
                flushed = True
                break
            time.sleep(0.2)
        assert flushed
        wanted = [str(event["id"]) for event in recorded]
        uploaded = _uploaded_ids(_audit(live_server), set(wanted))
        assert uploaded == wanted
        assert len(uploaded) == len(set(uploaded))
    finally:
        client.close()


def test_previous_instance_spool_is_uploaded_at_startup(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    event_id = "ab" * 16
    directory = tmp_path / "spool" / "billing"
    directory.mkdir(parents=True)
    document = {
        "id": event_id,
        "ts": "2026-01-01T00:00:00.000Z",
        "type": "lifecycle",
        "event": "start",
        "agent": "other",
        "environment": "dev",
        "instance": "old",
        "task": None,
    }
    path = directory / "old-0.jsonl"
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token):
        pass
    assert _uploaded_ids(_audit(live_server), {event_id}) == [event_id]
    assert not path.exists()


def test_local_audit_is_written_when_configured(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    path = tmp_path / "audit.jsonl"
    with Tessera(
        "billing",
        server_url=live_server.url,
        agent_token=live_server.token,
        audit_path=path,
    ) as client:
        client.check(Action.tool("files.read"))
    text = path.read_text(encoding="utf-8")
    assert '"event":"start"' in text or '"event": "start"' in text
    assert "stop" in text
    assert client.instance_id


def test_spool_cap_drops_oldest_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(events_module, "SEGMENT_BYTES", 220)
    monkeypatch.setattr(events_module, "SPOOL_BYTES", 700)

    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    http = httpx.Client(transport=httpx.MockTransport(fail))
    uploader = EventUploader(http, lambda: "token", "billing", "a" * 32, tmp_path / "spool")
    caplog.set_level(logging.WARNING, logger="agent_tessera")
    ids: list[str] = []
    for index in range(8):
        event_id = f"{index:032x}"
        ids.append(event_id)
        uploader.enqueue(
            {
                "id": event_id,
                "ts": "2026-01-01T00:00:00.000Z",
                "type": "decision",
                "pad": "x" * 80,
            }
        )
    assert uploader.flush(0.4) is False
    messages = [record.message for record in caplog.records if "dropped" in record.message]
    assert messages
    assert any(character.isdigit() for message in messages for character in message)
    remaining = list((tmp_path / "spool" / "billing").glob("*.jsonl"))
    assert sum(path.stat().st_size for path in remaining) <= 700
    text = "".join(path.read_text(encoding="utf-8") for path in remaining)
    assert ids[0] not in text
    assert ids[-1] in text


def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture events as they are queued.

    Args:
        monkeypatch: Patcher for `EventUploader.enqueue`.

    Returns:
        The list that receives each queued event.
    """
    recorded: list[dict[str, object]] = []
    original = EventUploader.enqueue

    def spy(self: EventUploader, event: Mapping[str, object]) -> None:
        recorded.append(dict(event))
        original(self, event)

    monkeypatch.setattr(EventUploader, "enqueue", spy)
    return recorded


def _audit(server: _Server) -> list[dict[str, object]]:
    """Read the server audit chain.

    Args:
        server: Live control plane.

    Returns:
        Audit rows in sequence order.
    """
    with httpx.Client(base_url=server.url, timeout=5) as client:
        response = client.get("/v1/audit/log", headers=server.headers, params={"limit": 500})
    assert response.status_code == 200
    items = response.json()["items"]
    return [item for item in items if isinstance(item, dict)]


def _uploaded_ids(rows: list[dict[str, object]], wanted: set[str]) -> list[str]:
    """Return wanted event ids in audit order.

    Args:
        rows: Audit rows.
        wanted: Event ids to keep.

    Returns:
        Matching ids, oldest first.
    """
    found: list[str] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        event_id = payload.get("id")
        if isinstance(event_id, str) and event_id in wanted:
            found.append(event_id)
    return found


def _has_resource(rows: list[dict[str, object]], resource: str) -> bool:
    """Return whether a decision for a resource was stored.

    Args:
        rows: Audit rows.
        resource: Tool name.

    Returns:
        `True` when one decision names that resource.
    """
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        action = payload.get("action")
        if isinstance(action, dict) and action.get("resource") == resource:
            return True
    return False
