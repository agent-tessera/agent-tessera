"""Tests for the tessera command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tessera import __version__
from agent_tessera.audit import AuditLog
from agent_tessera.cli import main

EXAMPLES = Path(__file__).resolve().parents[2] / "policies" / "examples"
SUPPORT = EXAMPLES / "support-triage.yaml"
BASELINE = EXAMPLES / "prod-baseline.yaml"


def test_main_without_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: tessera" in capsys.readouterr().out


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_validate_examples_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    paths = sorted(str(path) for path in EXAMPLES.glob("*.yaml"))
    assert main(["policy", "validate", *paths]) == 0
    output = capsys.readouterr().out
    assert f"OK {SUPPORT} (AgentPolicy support-triage)" in output
    assert f"OK {BASELINE} (BaselinePolicy prod-baseline)" in output
    assert "ERROR" not in output


def test_validate_reports_each_file_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("kind: nope\n", encoding="utf-8")
    code = main(["policy", "validate", str(SUPPORT), str(broken)])
    captured = capsys.readouterr()
    assert code == 1
    assert f"OK {SUPPORT} (AgentPolicy support-triage)" in captured.out
    assert captured.err.startswith(f"ERROR {broken}: ")
    assert "Traceback" not in captured.err


def test_validate_missing_file_has_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.yaml"
    assert main(["policy", "validate", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"ERROR {missing}: ")
    assert "Traceback" not in captured.err


def test_check_refund_approval_parses_json_number(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--baseline",
            str(BASELINE),
            "--tool",
            "zendesk.refund",
            "--arg",
            "amount=250",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "effect:  require_approval",
        "reason:  approval_rule",
        "rule:    agent:tools.require_approval[0]",
        f"policy:  {_policy_line(captured.out)}",
    ]


def test_check_refund_under_threshold_allows(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--tool",
            "zendesk.refund",
            "--arg",
            "amount=50",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith("effect:  allow\n")


def test_check_arg_falls_back_to_raw_string(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "args.yaml"
    policy.write_text(
        """
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: arg-bot
spec:
  tools:
    allow: ["echo"]
    require_approval:
      - name: echo
        when:
          - arg: name
            op: eq
            value: bob
""".lstrip(),
        encoding="utf-8",
    )
    code = main(["policy", "check", "--policy", str(policy), "--tool", "echo", "--arg", "name=bob"])
    assert code == 3
    assert "reason:  approval_rule" in capsys.readouterr().out


def test_check_deny_shell(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["policy", "check", "--policy", str(SUPPORT), "--tool", "shell.exec"])
    captured = capsys.readouterr()
    assert code == 2
    assert "effect:  deny" in captured.out
    assert "reason:  deny_rule" in captured.out
    assert "rule:    agent:tools.deny[0]" in captured.out


def test_check_model_guardrail_and_steps(capsys: pytest.CaptureFixture[str]) -> None:
    denied = main(["policy", "check", "--policy", str(SUPPORT), "--model", "openai/gpt-4.1"])
    denied_out = capsys.readouterr().out
    assert denied == 2
    assert "reason:  guardrail_missing" in denied_out

    allowed = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--model",
            "openai/gpt-4.1",
            "--guardrail",
            "input.prompt_injection",
        ]
    )
    assert allowed == 0
    assert capsys.readouterr().out.startswith("effect:  allow\n")

    limited = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--tool",
            "zendesk.read_ticket",
            "--steps",
            "25",
        ]
    )
    limited_out = capsys.readouterr().out
    assert limited == 2
    assert "reason:  limit" in limited_out


def test_check_user_group_and_other_kinds(capsys: pytest.CaptureFixture[str]) -> None:
    allowed = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--tool",
            "zendesk.read_ticket",
            "--user",
            "ada",
            "--group",
            "support-staff",
        ]
    )
    assert allowed == 0
    capsys.readouterr()

    denied = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--tool",
            "zendesk.read_ticket",
            "--user",
            "ada",
            "--group",
            "guests",
        ]
    )
    assert denied == 2
    assert "reason:  delegation" in capsys.readouterr().out

    data = main(["policy", "check", "--policy", str(SUPPORT), "--data", "crm.customers:read"])
    assert data == 0
    capsys.readouterr()

    agent = main(["policy", "check", "--policy", str(SUPPORT), "--agent", "billing-agent"])
    assert agent == 0
    capsys.readouterr()

    missing = main(["policy", "check", "--policy", str(SUPPORT), "--tool", "crm.export"])
    assert missing == 2
    lines = capsys.readouterr().out.splitlines()
    assert "reason:  not_granted" in lines[1]
    assert lines[2] == "rule:    "


def test_check_usage_and_policy_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    grouped = main(
        [
            "policy",
            "check",
            "--policy",
            str(SUPPORT),
            "--tool",
            "zendesk.read_ticket",
            "--group",
            "staff",
        ]
    )
    assert grouped == 1
    captured = capsys.readouterr()
    assert "--group requires --user" in captured.err
    assert "Traceback" not in captured.err

    with pytest.raises(SystemExit) as exc_info:
        main(["policy", "check", "--policy", str(SUPPORT)])
    assert exc_info.value.code == 1
    assert "Traceback" not in capsys.readouterr().err

    missing = tmp_path / "nope.yaml"
    assert main(["policy", "check", "--policy", str(missing), "--tool", "echo"]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(missing) in captured.err

    assert (
        main(
            [
                "policy",
                "check",
                "--policy",
                str(SUPPORT),
                "--baseline",
                str(BASELINE),
                "--baseline",
                str(BASELINE),
                "--tool",
                "zendesk.read_ticket",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "duplicate baseline" in captured.err
    assert "Traceback" not in captured.err

    bad_arg = main(
        ["policy", "check", "--policy", str(SUPPORT), "--tool", "echo", "--arg", "amount"]
    )
    assert bad_arg == 1
    captured = capsys.readouterr()
    assert "KEY=VALUE" in captured.err
    assert "Traceback" not in captured.err


def test_incomplete_commands_exit_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["policy"]) == 1
    assert "tessera policy" in capsys.readouterr().err
    assert main(["audit"]) == 1
    assert "tessera audit" in capsys.readouterr().err


def test_audit_verify_ok_and_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("lifecycle", {"event": "start"})
    log.write("lifecycle", {"event": "stop"})
    log.close()

    assert main(["audit", "verify", str(path)]) == 0
    assert capsys.readouterr().out == "OK: 2 events\n"

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["event"] = "tampered"
    lines[0] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main(["audit", "verify", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("FAILED at line 1: ")
    assert "Traceback" not in captured.err


def test_audit_verify_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing.jsonl"
    assert main(["audit", "verify", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "FAILED: file not found\n"


def _policy_line(output: str) -> str:
    """Return the policy version printed on the policy line."""
    for line in output.splitlines():
        if line.startswith("policy:"):
            return line.split(maxsplit=1)[1]
    raise AssertionError(output)
