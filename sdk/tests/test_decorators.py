"""Tests for tool, guardrail, and model-call decorators."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_tessera import AgentPolicy, Tessera
from agent_tessera import client as client_module
from agent_tessera.audit import verify_audit_log
from agent_tessera.context import guardrails_ran
from agent_tessera.errors import ApprovalRequired, Denied
from agent_tessera.types import Action, Decision, Reason


def _policy(**spec: object) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": spec,
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _calls(path: Path) -> list[dict[str, object]]:
    return [event for event in _events(path) if event["type"] == "call"]


def _client(
    tmp_path: Path,
    *,
    handler: Callable[[Decision], bool] | None = None,
    **spec: object,
) -> tuple[Tessera, Path]:
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(**spec),
        audit_path=path,
        approval_handler=handler,
    )
    return client, path


def test_tool_binds_defaults_drops_self_and_keeps_wraps(tmp_path: Path) -> None:
    client, path = _client(
        tmp_path,
        tools={"allow": ["files.read"]},
        audit={"capture_content": True},
    )

    class Reader:
        @client.tool("files.read")
        def read(self, path: str, limit: int = 5) -> str:
            """Read a file."""
            return f"{path}:{limit}"

    assert Reader.read.__name__ == "read"
    assert Reader.read.__doc__ == "Read a file."
    assert Reader().read("notes.txt") == "notes.txt:5"
    action = _events(path)[1]["action"]
    assert isinstance(action, dict)
    assert action["args"] == {"path": "notes.txt", "limit": 5}
    assert _calls(path)[0]["status"] == "ok"
    assert float(str(_calls(path)[0]["duration_ms"])) >= 0
    client.close()
    assert verify_audit_log(path).ok is True


def test_denied_and_unapproved_tools_do_not_run_or_emit_calls(tmp_path: Path) -> None:
    client, path = _client(
        tmp_path,
        tools={"allow": ["files.read"], "require_approval": ["files.delete"]},
    )
    ran: list[str] = []

    @client.tool("shell.exec")
    def blocked() -> None:
        ran.append("blocked")

    @client.tool("files.delete")
    def needs_approval() -> None:
        ran.append("approval")

    with pytest.raises(Denied):
        blocked()
    with pytest.raises(ApprovalRequired):
        needs_approval()
    assert ran == []
    assert _calls(path) == []
    client.close()
    assert verify_audit_log(path).ok is True


def test_approved_tool_writes_one_call_and_errors_keep_the_exception(tmp_path: Path) -> None:
    client, path = _client(
        tmp_path,
        tools={"require_approval": ["files.delete"], "allow": ["files.read"]},
        handler=lambda _decision: True,
    )

    @client.tool("files.delete")
    def delete() -> str:
        return "gone"

    @client.tool("files.read")
    def read() -> str:
        raise RuntimeError("secret message")

    assert delete() == "gone"
    with pytest.raises(RuntimeError, match="secret message"):
        read()
    calls = _calls(path)
    assert [call["status"] for call in calls] == ["ok", "error"]
    assert calls[1]["error_type"] == "builtins.RuntimeError"
    assert "secret message" not in path.read_text(encoding="utf-8")
    client.close()
    assert verify_audit_log(path).ok is True


def test_cancelled_and_interrupted_calls_are_recorded_as_errors(tmp_path: Path) -> None:
    client, path = _client(tmp_path, tools={"allow": ["files.read", "files.list"]})

    @client.tool("files.read")
    async def read() -> str:
        raise asyncio.CancelledError

    @client.tool("files.list")
    def listing() -> str:
        raise KeyboardInterrupt

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(read())
    with pytest.raises(KeyboardInterrupt):
        listing()
    calls = _calls(path)
    assert [call["status"] for call in calls] == ["error", "error"]
    assert calls[0]["error_type"] == "asyncio.exceptions.CancelledError"
    assert calls[1]["error_type"] == "builtins.KeyboardInterrupt"
    client.close()
    assert verify_audit_log(path).ok is True


def test_duration_uses_the_patched_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter((1.0, 1.25))
    monkeypatch.setattr(client_module.time, "monotonic", lambda: next(times))
    client, path = _client(tmp_path, tools={"allow": ["files.read"]})

    @client.tool("files.read")
    def read() -> str:
        return "ok"

    assert read() == "ok"
    assert _calls(path)[0]["duration_ms"] == 250.0
    client.close()
    assert verify_audit_log(path).ok is True


def test_guardrail_marks_only_when_it_returns(tmp_path: Path) -> None:
    client, path = _client(tmp_path)

    @client.guardrail("input.prompt_injection")
    def scan(text: str) -> str:
        """Scan a prompt."""
        return text

    @client.guardrail("input.other")
    def broken() -> None:
        raise RuntimeError("boom")

    assert scan.__doc__ == "Scan a prompt."
    assert scan("hello") == "hello"
    with pytest.raises(RuntimeError, match="boom"):
        broken()
    assert guardrails_ran(client.instance_id) == frozenset({"input.prompt_injection"})
    statuses = [event["status"] for event in _events(path) if event["type"] == "guardrail"]
    assert statuses == ["ran"]
    client.close()
    assert verify_audit_log(path).ok is True


def test_blocking_input_guardrail_is_required_again_after_the_model_call(tmp_path: Path) -> None:
    client, path = _client(
        tmp_path,
        models={"allow": ["local/*"]},
        guardrails={"required": ["input.prompt_injection"], "on_missing": "block"},
    )

    @client.guardrail("input.prompt_injection")
    def scan(prompt: str) -> None:
        return None

    @client.model_call("local/echo")
    def complete(prompt: str) -> str:
        """Echo a prompt."""
        return prompt

    assert complete.__name__ == "complete"
    with pytest.raises(Denied) as denied:
        complete("hi")
    assert denied.value.decision.reason is Reason.GUARDRAIL_MISSING
    scan("hi")
    assert complete("hi") == "hi"
    with pytest.raises(Denied):
        complete("again")
    assert len(_calls(path)) == 1
    assert _calls(path)[0]["status"] == "ok"
    assert _calls(path)[0]["kind"] == "model"
    client.close()
    assert verify_audit_log(path).ok is True


def test_warn_and_flag_record_missing_guardrails_without_denying(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="agent_tessera")
    client, path = _client(
        tmp_path,
        models={"allow": ["local/*"]},
        guardrails={
            "required": ["input.flagged", "input.warned", "output.pii"],
            "on_missing": "warn",
        },
    )

    @client.model_call("local/echo")
    def complete(prompt: str) -> str:
        return prompt

    assert complete("hi") == "hi"
    missing = [
        event["name"]
        for event in _events(path)
        if event["type"] == "guardrail" and event["status"] == "missing"
    ]
    assert missing == ["input.flagged", "input.warned"]
    assert "output.pii" not in missing
    assert any("input.warned" in record.message for record in caplog.records)
    client.close()
    assert verify_audit_log(path).ok is True

    flag_path = tmp_path / "flag.jsonl"
    flagged = Tessera(
        "bot",
        policy=_policy(
            models={"allow": ["local/*"]},
            guardrails={"required": ["input.flagged"], "on_missing": "flag"},
        ),
        audit_path=flag_path,
    )

    @flagged.model_call("local/echo")
    def echo(prompt: str) -> str:
        return prompt

    assert echo("hi") == "hi"
    flagged.close()
    assert verify_audit_log(flag_path).ok is True
    assert any(
        event["status"] == "missing" and event["name"] == "input.flagged"
        for event in _events(flag_path)
        if event["type"] == "guardrail"
    )


def test_usage_counts_toward_the_daily_token_limit(tmp_path: Path) -> None:
    client, path = _client(
        tmp_path,
        models={"allow": ["local/*"]},
        limits={"tokens_per_day": 10},
    )
    client.record_usage("local/echo", 4, 6)
    with pytest.raises(Denied) as caught:
        client.enforce(Action.model("local/echo"))
    assert caught.value.decision.reason is Reason.LIMIT
    usage = [event for event in _events(path) if event["type"] == "usage"]
    assert usage[0]["input_tokens"] == 4
    assert usage[0]["output_tokens"] == 6
    client.close()
    assert verify_audit_log(path).ok is True


def test_async_decorators(tmp_path: Path) -> None:
    client, path = _client(
        tmp_path,
        tools={"allow": ["files.read"]},
        models={"allow": ["local/*"]},
        guardrails={"required": ["input.prompt_injection"], "on_missing": "block"},
        audit={"capture_content": True},
    )

    @client.tool("files.read")
    async def read(path: str = "notes.txt") -> str:
        """Read asynchronously."""
        return path

    @client.guardrail("input.prompt_injection")
    async def scan(prompt: str) -> None:
        """Scan asynchronously."""
        return

    @client.model_call("local/echo")
    async def complete(prompt: str) -> str:
        """Complete asynchronously."""
        return prompt

    @client.tool("files.read")
    async def fail() -> None:
        raise RuntimeError("async secret")

    async def scenario() -> None:
        assert read.__name__ == "read"
        assert read.__doc__ == "Read asynchronously."
        assert await read() == "notes.txt"
        with pytest.raises(Denied):
            await complete("hi")
        await scan("hi")
        assert scan.__doc__ == "Scan asynchronously."
        assert await complete("hi") == "hi"
        assert complete.__name__ == "complete"
        with pytest.raises(Denied):
            await complete("again")
        with pytest.raises(RuntimeError, match="async secret"):
            await fail()

    asyncio.run(scenario())
    tool_args = [
        event["action"]["args"]
        for event in _events(path)
        if event["type"] == "decision"
        and isinstance(event["action"], dict)
        and event["action"].get("kind") == "tool"
        and "args" in event["action"]
    ]
    assert {"path": "notes.txt"} in tool_args
    statuses = [call["status"] for call in _calls(path)]
    assert statuses == ["ok", "ok", "error"]
    assert _calls(path)[2]["error_type"] == "builtins.RuntimeError"
    assert "async secret" not in path.read_text(encoding="utf-8")
    client.close()
    assert verify_audit_log(path).ok is True
