"""Tests for the Tessera client."""

import json
import os
from pathlib import Path

import pytest

import agent_tessera
from agent_tessera import AgentPolicy, BaselinePolicy, Tessera
from agent_tessera.audit import verify_audit_log
from agent_tessera.errors import ApprovalRequired, ConfigError, Denied
from agent_tessera.types import Action, Decision, Effect, Reason

_AGENT = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: bot
  environment: prod
spec:
  tools:
    allow: ["files.read"]
"""

_OTHER = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: other
  environment: prod
spec:
  tools:
    allow: ["files.read"]
"""

_DENY = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: lock
spec:
  tools:
    deny: ["files.read"]
"""


def _policy(agent: str = "bot", **spec: object) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": agent, "environment": "prod"},
            "spec": spec,
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _finish(client: Tessera, path: Path) -> None:
    client.close()
    assert verify_audit_log(path).ok is True


def test_argument_wins_over_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_policy = tmp_path / "env.yaml"
    arg_policy = tmp_path / "arg.yaml"
    env_baseline = tmp_path / "deny.yaml"
    env_audit = tmp_path / "env.jsonl"
    arg_audit = tmp_path / "arg.jsonl"
    env_policy.write_text(_OTHER, encoding="utf-8")
    arg_policy.write_text(_AGENT, encoding="utf-8")
    env_baseline.write_text(_DENY, encoding="utf-8")
    monkeypatch.setenv("TESSERA_POLICY", str(env_policy))
    monkeypatch.setenv("TESSERA_BASELINES", f"{os.pathsep}{env_baseline}")
    monkeypatch.setenv("TESSERA_AUDIT_PATH", str(env_audit))
    monkeypatch.setenv("TESSERA_SUSPENDED", "1")

    client = Tessera(
        "bot",
        policy=arg_policy,
        baselines=(),
        audit_path=arg_audit,
        suspended=False,
    )
    decision = client.enforce(Action.tool("files.read"))
    _finish(client, arg_audit)
    assert decision.effect is Effect.ALLOW
    assert client.suspended is False
    assert arg_audit.is_file()
    assert not env_audit.exists()


def test_environment_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = tmp_path / "agent.yaml"
    baseline = tmp_path / "deny.yaml"
    audit = tmp_path / "from-env.jsonl"
    policy.write_text(_AGENT, encoding="utf-8")
    baseline.write_text(_DENY, encoding="utf-8")
    monkeypatch.setenv("TESSERA_POLICY", str(policy))
    monkeypatch.setenv("TESSERA_BASELINES", f"{os.pathsep}{baseline}")
    monkeypatch.setenv("TESSERA_AUDIT_PATH", str(audit))
    monkeypatch.setenv("TESSERA_SUSPENDED", "1")

    client = Tessera("bot")
    with pytest.raises(Denied) as caught:
        client.enforce(Action.tool("files.read"))
    _finish(client, audit)
    assert client.suspended is True
    assert caught.value.decision.reason is Reason.SUSPENDED


def test_default_audit_path_and_unsuspended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.delenv("TESSERA_BASELINES", raising=False)
    monkeypatch.delenv("TESSERA_AUDIT_PATH", raising=False)
    monkeypatch.delenv("TESSERA_SUSPENDED", raising=False)
    client = Tessera("bot", policy=_policy(tools={"allow": ["files.read"]}))
    assert client.suspended is False
    client.enforce(Action.tool("files.read"))
    path = tmp_path / ".tessera" / "audit.jsonl"
    client.close()
    assert verify_audit_log(path).ok is True


def test_missing_policy_and_agent_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    with pytest.raises(ConfigError, match="policy is required"):
        Tessera("bot", audit_path=tmp_path / "missing.jsonl")
    with pytest.raises(ConfigError, match="does not match"):
        Tessera("bot", policy=_policy("other"), audit_path=tmp_path / "mismatch.jsonl")


def test_enforce_denies_and_requires_approval(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(
            tools={
                "allow": ["files.read"],
                "require_approval": ["files.delete"],
            }
        ),
        audit_path=path,
    )
    allowed = client.check(Action.tool("files.read"))
    assert allowed.effect is Effect.ALLOW
    with pytest.raises(Denied):
        client.enforce(Action.tool("shell.exec"))
    with pytest.raises(ApprovalRequired):
        client.enforce(Action.tool("files.delete"))
    _finish(client, path)


def test_approval_handler_grants_or_rejects(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    answers = iter((True, False))
    client = Tessera(
        "bot",
        policy=_policy(tools={"require_approval": ["files.delete"]}),
        audit_path=path,
        approval_handler=lambda _decision: next(answers),
    )
    with client.task("task-1") as task:
        granted = client.enforce(Action.tool("files.delete"))
        assert granted.effect is Effect.ALLOW
        assert granted.reason is Reason.APPROVAL_GRANTED
        assert task.steps == 1
        with pytest.raises(Denied) as caught:
            client.enforce(Action.tool("files.delete"))
        assert caught.value.decision.reason is Reason.APPROVAL_REJECTED
        assert task.steps == 1
    events = _events(path)
    approvals = [event for event in events if event["type"] == "approval"]
    assert [event["granted"] for event in approvals] == [True, False]
    _finish(client, path)


def test_steps_and_call_limits(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(
            tools={"allow": ["files.read"]},
            models={"allow": ["local/*"]},
            data={"allow": ["crm.*"]},
            limits={"max_steps_per_task": 2, "calls_per_minute": {"files.*": 1}},
        ),
        audit_path=path,
    )
    with client.task("task-1") as task:
        checked = client.check(Action.tool("files.read"))
        assert checked.effect is Effect.ALLOW
        assert task.steps == 0
        client.enforce(Action.model("local/echo"))
        client.enforce(Action.data("crm.customers:read"))
        assert task.steps == 1
        client.enforce(Action.tool("files.read"))
        assert task.steps == 2
        with pytest.raises(Denied) as steps:
            client.enforce(Action.model("local/echo"))
        assert steps.value.decision.reason is Reason.LIMIT
        assert task.steps == 2
    outside = client.enforce(Action.model("local/echo"))
    assert outside.effect is Effect.ALLOW
    with pytest.raises(Denied) as calls:
        client.enforce(Action.tool("files.read"))
    assert calls.value.decision.reason is Reason.LIMIT
    _finish(client, path)


def test_principal_suspension_and_captured_args(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(
            tools={"allow": ["files.read"]},
            delegation={"allow_on_behalf_of": True, "user_groups": ["support"]},
            audit={"capture_content": True},
        ),
        audit_path=path,
    )
    with client.on_behalf_of("ada", ["support", "ops"]) as principal:
        decision = client.check(Action.tool("files.read", {"path": "notes.txt"}))
        assert decision.action.principal is principal
    client.suspend()
    with pytest.raises(Denied) as suspended:
        client.enforce(Action.tool("files.read"))
    assert suspended.value.decision.reason is Reason.SUSPENDED
    client.resume()
    assert client.enforce(Action.tool("files.read")).effect is Effect.ALLOW
    recorded = [
        event["action"]
        for event in _events(path)
        if event["type"] == "decision" and isinstance(event["action"], dict)
    ]
    captured = recorded[0]
    assert captured["args"] == {"path": "notes.txt"}
    principal_field = _events(path)[1]["principal"]
    assert isinstance(principal_field, dict)
    assert principal_field["groups"] == ["ops", "support"]
    _finish(client, path)


def test_decision_omits_args_without_capture(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(tools={"allow": ["files.read"]}),
        audit_path=path,
    )
    client.check(Action.tool("files.read", {"path": "secret"}))
    action = _events(path)[1]["action"]
    assert isinstance(action, dict)
    assert "args" not in action
    _finish(client, path)


def test_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    with Tessera("bot", policy=_policy(), audit_path=path) as client:
        assert len(client.instance_id) == 32
        assert client.agent == "bot"
        assert client.policy.agent == "bot"
    client.close()
    stops = [event for event in _events(path) if event.get("event") == "stop"]
    assert len(stops) == 1
    assert verify_audit_log(path).ok is True
    start = _events(path)[0]
    assert start["event"] == "start"
    assert start["task"] is None
    assert start["sdk_version"] == agent_tessera.__version__


def test_two_clients_keep_separate_tasks(tmp_path: Path) -> None:
    alpha = Tessera(
        "alpha",
        policy=_policy(
            "alpha",
            tools={"allow": ["files.read"]},
            models={"allow": ["local/*"]},
        ),
        audit_path=tmp_path / "alpha.jsonl",
    )
    beta = Tessera(
        "beta",
        policy=_policy(
            "beta",
            tools={"allow": ["files.read"]},
            models={"allow": ["local/*"]},
            delegation={"allow_on_behalf_of": True},
        ),
        audit_path=tmp_path / "beta.jsonl",
    )
    with alpha.task("alpha-task") as task:
        alpha.enforce(Action.tool("files.read"))
        beta.enforce(Action.tool("files.read"))
        beta.enforce(Action.model("local/echo"))
        assert task.steps == 1
    with alpha.on_behalf_of("ada", ["support"]):
        decision = beta.check(Action.tool("files.read"))
    assert decision.action.principal is not None
    assert decision.action.principal.user == "ada"
    _finish(alpha, tmp_path / "alpha.jsonl")
    _finish(beta, tmp_path / "beta.jsonl")


def test_unenforced_settings_are_warned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="agent_tessera")
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(
            limits={"cost_usd_per_day": 5},
            guardrails={
                "required": ["input.prompt_injection", "output.pii"],
                "on_missing": "block",
            },
        ),
        audit_path=path,
    )
    messages = [record.message for record in caplog.records]
    assert "cost_usd_per_day is not enforced in this version" in messages
    assert "only input guardrails can block; treated as warn: output.pii" in messages
    assert not any("input.prompt_injection" in message for message in messages)
    _finish(client, path)


def test_non_one_suspended_env_is_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_SUSPENDED", "true")
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)
    assert client.suspended is False
    _finish(client, path)


def test_baseline_object_is_accepted(tmp_path: Path) -> None:
    baseline = BaselinePolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "BaselinePolicy",
            "metadata": {"name": "prod"},
            "spec": {"tools": {"deny": ["files.read"]}},
        }
    )
    path = tmp_path / "audit.jsonl"
    client = Tessera(
        "bot",
        policy=_policy(tools={"allow": ["files.read"]}),
        baselines=[baseline],
        audit_path=path,
    )
    with pytest.raises(Denied) as caught:
        client.enforce(Action.tool("files.read"))
    assert caught.value.decision.reason is Reason.DENY_RULE
    _finish(client, path)


def test_decision_event_shape(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(tools={"allow": ["files.read"]}), audit_path=path)
    decision = client.check(Action.tool("files.read"))
    event = _events(path)[1]
    assert isinstance(decision, Decision)
    assert event["agent"] == "bot"
    assert event["environment"] == "prod"
    assert event["instance"] == client.instance_id
    assert event["effect"] == "allow"
    assert event["reason"] == "allow_rule"
    assert event["principal"] is None
    _finish(client, path)


def test_unattended_agent_without_handler_is_denied(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod", "autonomy": "supervised"},
            "spec": {"tools": {"require_approval": ["files.delete"]}},
        }
    )
    client = Tessera("bot", policy=policy, audit_path=path)
    with pytest.raises(Denied) as caught:
        client.enforce(Action.tool("files.delete"))
    assert caught.value.decision.reason is Reason.APPROVAL_UNAVAILABLE
    _finish(client, path)


def test_offline_circuit_breaker_opens_after_repeated_denials(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {
                "tools": {"allow": ["files.read"]},
                "circuit_breaker": {"denials": 2, "window": "1h", "action": "suspend"},
            },
        }
    )
    client = Tessera("bot", policy=policy, audit_path=path)
    for _ in range(2):
        with pytest.raises(Denied) as denied:
            client.enforce(Action.tool("shell.exec"))
        assert denied.value.decision.reason is Reason.NOT_GRANTED
    with pytest.raises(Denied) as opened:
        client.enforce(Action.tool("files.read"))
    assert opened.value.decision.reason is Reason.CIRCUIT_OPEN
    _finish(client, path)
