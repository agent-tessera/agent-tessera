"""Tests for the hash-chained audit log."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_tessera.audit import GENESIS_HASH, AuditLog, verify_audit_log


def _write_events(log: AuditLog, count: int) -> None:
    for index in range(count):
        log.write("lifecycle", {"event": "start", "n": index})


def test_chain_verifies_and_timestamp_ends_with_z(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    assert log.path == path
    first = log.write("lifecycle", {"event": "start"})
    second = log.write("decision", {"effect": "allow", "note": "café"})
    log.close()

    result = verify_audit_log(path)
    assert result.ok is True
    assert result.events == 2
    assert result.error is None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", str(first["ts"]))
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == first["hash"]
    assert first["seq"] == 1
    assert second["seq"] == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[1])["note"] == "café"


def test_reopening_continues_sequence_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    original = first.write("lifecycle", {"event": "start"})
    first.close()

    second = AuditLog(path)
    continued = second.write("lifecycle", {"event": "stop"})
    second.close()

    assert continued["seq"] == 2
    assert continued["prev_hash"] == original["hash"]
    result = verify_audit_log(path)
    assert result.ok is True
    assert result.events == 2


def test_editing_deleting_or_reordering_fails_at_that_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    _write_events(log, 3)
    log.close()
    original = path.read_text(encoding="utf-8").splitlines()

    edited = original.copy()
    record = json.loads(edited[1])
    record["n"] = 99
    edited[1] = json.dumps(record)
    path.write_text("\n".join(edited) + "\n", encoding="utf-8")
    edited_result = verify_audit_log(path)
    assert edited_result.ok is False
    assert edited_result.error_line == 2

    deleted = [original[0], original[2]]
    path.write_text("\n".join(deleted) + "\n", encoding="utf-8")
    deleted_result = verify_audit_log(path)
    assert deleted_result.ok is False
    assert deleted_result.error_line == 2

    swapped = [original[1], original[0], original[2]]
    path.write_text("\n".join(swapped) + "\n", encoding="utf-8")
    swapped_result = verify_audit_log(path)
    assert swapped_result.ok is False
    assert swapped_result.error_line == 1


def test_reserved_keys_raise_and_do_not_append(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("lifecycle", {"event": "start"})
    with pytest.raises(ValueError, match="reserved field hash"):
        log.write("lifecycle", {"hash": "nope"})
    log.close()
    assert verify_audit_log(path).events == 1


def test_parent_directories_are_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "audit.jsonl"
    log = AuditLog(path)
    log.write("lifecycle", {"event": "start"})
    log.close()
    assert path.is_file()
    assert verify_audit_log(path).ok is True


def test_missing_file_is_reported(tmp_path: Path) -> None:
    result = verify_audit_log(tmp_path / "does-not-exist.jsonl")
    assert result.ok is False
    assert result.events == 0
    assert result.error == "file not found"


def test_concurrent_writes_still_verify(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    def worker(worker_id: int) -> None:
        for index in range(50):
            log.write("lifecycle", {"worker": worker_id, "n": index})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(4)))
    log.close()
    result = verify_audit_log(path)
    assert result.ok is True
    assert result.events == 200


def test_non_json_values_are_stringified(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("note", {"item": Path("marker")})
    log.close()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["item"] == "marker"
    assert verify_audit_log(path).ok is True


def test_blank_lines_are_ignored_and_bad_lines_fail(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("lifecycle", {"event": "start"})
    log.close()
    text = path.read_text(encoding="utf-8")
    path.write_text("\n" + text, encoding="utf-8")
    reopened = AuditLog(path)
    reopened.write("lifecycle", {"event": "stop"})
    reopened.close()
    assert verify_audit_log(path).ok is True

    path.write_text("not-json\n", encoding="utf-8")
    invalid = verify_audit_log(path)
    assert invalid.ok is False
    assert invalid.error == "invalid JSON"
    assert invalid.error_line == 1

    path.write_text("[1]\n", encoding="utf-8")
    assert verify_audit_log(path).error == "invalid JSON"

    path.write_text('{"seq": true, "hash": "abc"}\n', encoding="utf-8")
    assert verify_audit_log(path).error == "sequence mismatch"


def test_prev_hash_and_unfinished_events_fail(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("lifecycle", {"event": "start"})
    log.write("lifecycle", {"event": "stop"})
    log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["prev_hash"] = GENESIS_HASH
    lines[1] = json.dumps(second)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = verify_audit_log(path)
    assert result.ok is False
    assert result.error == "prev_hash mismatch"
    assert result.error_line == 2

    path.write_text('{"seq": "1", "hash": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        AuditLog(path)


def test_empty_file_starts_at_the_first_event(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.touch()
    log = AuditLog(path)
    written = log.write("lifecycle", {"event": "start"})
    log.close()
    assert written["seq"] == 1
    assert verify_audit_log(path).events == 1


def test_corrupt_reopen_raises(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        AuditLog(path)
