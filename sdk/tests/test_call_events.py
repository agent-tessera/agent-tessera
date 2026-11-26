"""Contract tests for enriched call events."""

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

from agent_tessera import AgentPolicy, Tessera
from agent_tessera.audit import verify_audit_log


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {
                "tools": {"allow": ["files.*"]},
                "models": {"allow": ["local/*"]},
                "delegation": {"allow_on_behalf_of": True},
                "effects": {
                    "classify": {
                        "files.read": ["read"],
                        "files.write": ["write", "delete"],
                    }
                },
            },
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _calls(path: Path) -> list[dict[str, object]]:
    return [event for event in _events(path) if event["type"] == "call"]


def test_tool_call_records_context_effects_and_sizes(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)

    @client.tool("files.write")
    def write(name: str) -> str:
        return name

    @client.tool("files.other")
    def other() -> None:
        return None

    @client.tool("files.read")
    def fail(name: str) -> str:
        raise RuntimeError("hidden")

    with client.task("run-1") as task, client.on_behalf_of("ada", ["support"]):
        assert write("note") == "note"
        assert other() is None
        with contextlib.suppress(RuntimeError):
            fail("secret")

    call = _calls(path)
    assert call[0]["kind"] == "tool"
    assert call[0]["resource"] == "files.write"
    assert call[0]["status"] == "ok"
    assert call[0]["run"] == task.id
    assert call[0]["chain"] == []
    assert call[0]["principal"] == {"user": "ada", "groups": ["support"]}
    assert isinstance(call[0]["span_id"], str)
    assert call[0]["effects"] == ["delete", "write"]
    assert call[0]["args_bytes"] == len(
        json.dumps({"name": "note"}, separators=(",", ":")).encode()
    )
    assert call[0]["result_bytes"] == len(b"note")
    assert "approval_wait_ms" not in call[0]
    assert call[1]["effects"] == ["unknown"]
    assert call[1]["result_bytes"] == len(b"null")
    assert call[2]["status"] == "error"
    assert call[2]["result_bytes"] is None
    secret_args = json.dumps({"name": "secret"}, separators=(",", ":")).encode()
    assert call[2]["args_bytes"] == len(secret_args)
    assert "hidden" not in path.read_text(encoding="utf-8")
    client.close()
    assert verify_audit_log(path).ok is True


def test_model_call_records_stream_and_finish_reason(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)

    @client.model_call("local/echo")
    def complete(prompt: str) -> SimpleNamespace:
        return SimpleNamespace(
            stop_reason=None,
            choices=[SimpleNamespace(finish_reason="stop")],
        )

    with client.task("run-2"):
        assert complete("hi").choices[0].finish_reason == "stop"

    call = _calls(path)[0]
    assert call["kind"] == "model"
    assert call["run"] == "run-2"
    assert call["chain"] == []
    assert call["principal"] is None
    assert call["streamed"] is False
    assert call["finish_reason"] == "stop"
    assert "effects" not in call
    assert "approval_wait_ms" not in call
    client.close()
    assert verify_audit_log(path).ok is True
