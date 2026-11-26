"""Span ids pair decisions with calls and nest under enclosing tools."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_tessera import Action, AgentPolicy, Effect, Tessera
from agent_tessera.audit import verify_audit_log
from agent_tessera.errors import Denied

_SPAN = re.compile(r"^[0-9a-f]{16}$")


def _policy(*, deny: str | None = None) -> AgentPolicy:
    tools: dict[str, list[str]] = {"allow": ["files.*", "outer", "inner"]}
    if deny is not None:
        tools["deny"] = [deny]
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {
                "tools": tools,
                "models": {"allow": ["local/*"]},
                "effects": {"classify": {"files.write": ["write"]}},
            },
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _of_type(path: Path, event_type: str) -> list[dict[str, object]]:
    return [event for event in _events(path) if event["type"] == event_type]


def test_decision_and_call_share_span_id(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)

    @client.tool("files.read")
    def read() -> str:
        return "ok"

    assert read() == "ok"
    decisions = _of_type(path, "decision")
    calls = _of_type(path, "call")
    assert len(decisions) == 1
    assert len(calls) == 1
    span_id = decisions[0]["span_id"]
    assert isinstance(span_id, str)
    assert _SPAN.fullmatch(span_id)
    assert decisions[0]["parent_span_id"] is None
    assert calls[0]["span_id"] == span_id
    assert calls[0]["parent_span_id"] is None
    client.close()
    assert verify_audit_log(path).ok is True


def test_nested_tool_parents_to_outer_span(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)

    @client.tool("inner")
    def inner() -> str:
        return "inner"

    @client.tool("outer")
    def outer() -> str:
        return inner()

    assert outer() == "inner"
    decisions = _of_type(path, "decision")
    calls = _of_type(path, "call")
    by_resource: dict[str, dict[str, object]] = {}
    for event in decisions:
        action = event["action"]
        assert isinstance(action, dict)
        resource = action["resource"]
        assert isinstance(resource, str)
        by_resource[resource] = event
    outer_span = by_resource["outer"]["span_id"]
    inner_span = by_resource["inner"]["span_id"]
    assert by_resource["outer"]["parent_span_id"] is None
    assert by_resource["inner"]["parent_span_id"] == outer_span
    call_by_resource = {str(event["resource"]): event for event in calls}
    assert call_by_resource["outer"]["span_id"] == outer_span
    assert call_by_resource["inner"]["span_id"] == inner_span
    assert call_by_resource["inner"]["parent_span_id"] == outer_span
    client.close()


def test_denied_call_has_span_without_call_event(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(deny="files.secret"), audit_path=path)

    @client.tool("files.secret")
    def secret() -> str:
        return "no"

    with pytest.raises(Denied):
        secret()
    decisions = _of_type(path, "decision")
    assert len(decisions) == 1
    assert decisions[0]["effect"] == Effect.DENY.value
    assert _SPAN.fullmatch(str(decisions[0]["span_id"]))
    assert decisions[0]["parent_span_id"] is None
    assert _of_type(path, "call") == []
    client.close()


def test_effect_and_approval_carry_span_id(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {
                "tools": {
                    "allow": ["files.write"],
                    "require_approval": ["files.write"],
                },
            },
        }
    )
    client = Tessera(
        "bot",
        policy=policy,
        audit_path=path,
        approval_handler=lambda _decision: True,
    )

    @client.tool("files.write")
    def write() -> str:
        client.record_effect("ticket:1", reversible=True, undo="files.undo")
        return "done"

    assert write() == "done"
    decisions = _of_type(path, "decision")
    approvals = _of_type(path, "approval")
    effects = _of_type(path, "effect")
    calls = _of_type(path, "call")
    span_id = decisions[0]["span_id"]
    assert approvals[0]["span_id"] == span_id
    assert effects[0]["span_id"] == span_id
    assert calls[0]["span_id"] == span_id
    client.close()


def test_standalone_check_records_span(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(deny="files.secret"), audit_path=path)
    decision = client.check(Action.tool("files.secret"))
    assert decision.effect is Effect.DENY
    events = _of_type(path, "decision")
    assert _SPAN.fullmatch(str(events[0]["span_id"]))
    assert events[0]["parent_span_id"] is None
    client.close()
