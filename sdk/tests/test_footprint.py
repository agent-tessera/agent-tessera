"""Offline footprint of grants against an audit log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_tessera.cli import main
from agent_tessera.footprint import compute_footprint
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy

_SINCE = datetime(2026, 10, 1, tzinfo=UTC)


def test_sections_cover_grants_use_and_denials() -> None:
    policy = _policy()
    events = [
        _decision("files.read", "allow", "allow_rule", "2026-10-02T00:00:00Z"),
        _decision("files.read", "allow", "allow_rule", "2026-10-03T00:00:00Z"),
        _decision("files.delete", "require_approval", "approval_rule", "2026-10-03T00:00:00Z"),
        _decision("shell.exec", "deny", "not_granted", "2026-10-04T00:00:00Z"),
        _decision("shell.exec", "deny", "not_granted", "2026-10-04T01:00:00Z"),
        _decision("shell.exec", "deny", "deny_rule", "2026-10-04T02:00:00Z"),
        {
            "type": "call",
            "kind": "tool",
            "resource": "files.read",
            "agent": "bot",
            "ts": "2026-10-05T00:00:00Z",
        },
        _decision("files.read", "allow", "allow_rule", "2026-09-01T00:00:00Z"),
        _decision("files.read", "allow", "allow_rule", "2026-10-02T00:00:00Z", agent="other"),
    ]
    report = compute_footprint(build_effective_policy(policy, [_baseline()]), events, since=_SINCE)
    assert [(item.kind, item.pattern, item.rule) for item in report.declared] == [
        ("tool", "files.*", "agent:tools.allow[0]"),
        ("tool", "crm.*", "agent:tools.allow[1]"),
        ("tool", "files.delete", "agent:tools.require_approval[0]"),
    ]
    assert [(item.resource, item.count) for item in report.observed] == [
        ("files.delete", 1),
        ("files.read", 3),
    ]
    assert [(item.pattern, item.rule) for item in report.unused] == [
        ("crm.*", "agent:tools.allow[1]"),
    ]
    assert [(item.resource, item.count) for item in report.denied] == [("shell.exec", 2)]
    assert set(report.as_dict()) == {"declared", "observed", "unused", "denied"}


def test_cli_filters_days_and_prints_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "policy.yaml"
    audit = tmp_path / "audit.jsonl"
    policy.write_text(
        "apiVersion: tessera/v1\nkind: AgentPolicy\nmetadata:\n  agent: bot\n"
        "spec:\n  tools:\n    allow: [files.*]\n",
        encoding="utf-8",
    )
    recent = datetime.now(UTC) - timedelta(days=1)
    stale = datetime.now(UTC) - timedelta(days=40)
    _write_audit(
        audit,
        [
            _decision("files.read", "allow", "allow_rule", _stamp(recent)),
            _decision("files.write", "allow", "allow_rule", _stamp(stale)),
        ],
    )
    assert main(["footprint", "--policy", str(policy), "--audit", str(audit), "--days", "30"]) == 0
    text = capsys.readouterr().out
    assert "declared" in text
    assert "observed" in text
    assert "unused" in text
    assert "denied" in text
    assert "files.read 1" in text
    assert "files.write" not in text

    assert (
        main(
            [
                "footprint",
                "--policy",
                str(policy),
                "--audit",
                str(audit),
                "--days",
                "7",
                "--json",
            ]
        )
        == 0
    )
    body = json.loads(capsys.readouterr().out)
    assert set(body) == {"declared", "observed", "unused", "denied"}
    assert body["observed"] == [{"kind": "tool", "resource": "files.read", "count": 1}]


def test_bad_audit_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "apiVersion: tessera/v1\nkind: AgentPolicy\nmetadata:\n  agent: bot\n",
        encoding="utf-8",
    )
    missing = tmp_path / "missing.jsonl"
    assert main(["footprint", "--policy", str(policy), "--audit", str(missing)]) == 1
    assert "file not found" in capsys.readouterr().err
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"type": "call"}\nnot-json\n', encoding="utf-8")
    assert main(["footprint", "--policy", str(policy), "--audit", str(broken)]) == 1
    assert "invalid json" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc_info:
        main(["footprint", "--policy", str(policy), "--audit", str(broken), "--days", "0"])
    assert exc_info.value.code == 1


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot"},
            "spec": {
                "tools": {
                    "allow": ["files.*", "crm.*"],
                    "require_approval": ["files.delete"],
                }
            },
        }
    )


def _baseline() -> BaselinePolicy:
    return BaselinePolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "BaselinePolicy",
            "metadata": {"name": "prod"},
            "spec": {"tools": {"require_approval": ["wire.*"]}},
        }
    )


def _decision(
    resource: str,
    effect: str,
    reason: str,
    ts: str,
    *,
    agent: str = "bot",
) -> dict[str, object]:
    return {
        "type": "decision",
        "agent": agent,
        "ts": ts,
        "effect": effect,
        "reason": reason,
        "action": {"kind": "tool", "resource": resource},
    }


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_audit(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
