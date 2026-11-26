"""In-flight caps, run bounds, and circuit-breaker actions."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_tessera import Action, Denied, Reason, Tessera
from agent_tessera.context import Task, use_task
from agent_tessera.policy.models import AgentPolicy


def _policy(**spec: object) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": ["files.read"]}, **spec},
        }
    )


def test_in_flight_tool_cap(tmp_path: Path) -> None:
    policy = _policy(limits={"concurrency": {"max_in_flight_tool_calls": 1}})
    started = threading.Event()
    release = threading.Event()
    with Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client:

        @client.tool("files.read")
        def read() -> str:
            started.set()
            assert release.wait(5)
            return "ok"

        worker = threading.Thread(target=read)
        worker.start()
        assert started.wait(5)
        with pytest.raises(Denied) as caught:
            client.enforce(Action.tool("files.read"))
        release.set()
        worker.join(5)
    assert caught.value.decision.reason is Reason.LIMIT
    assert caught.value.decision.rule == "agent:limits.concurrency.max_in_flight_tool_calls"
    assert not worker.is_alive()


def test_nested_run_is_denied_at_the_cap(tmp_path: Path) -> None:
    policy = _policy(limits={"concurrency": {"max_concurrent_runs": 1}})
    with (
        Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client,
        client.task("outer"),
    ):
        client.enforce(Action.tool("files.read"))
        with client.task("inner"), pytest.raises(Denied) as caught:
            client.enforce(Action.tool("files.read"))
    assert caught.value.decision.reason is Reason.LIMIT
    assert caught.value.decision.rule == "agent:limits.concurrency.max_concurrent_runs"


def test_run_max_steps(tmp_path: Path) -> None:
    policy = _policy(limits={"run": {"max_steps": 2}})
    with (
        Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client,
        client.task("run-steps"),
    ):
        client.enforce(Action.tool("files.read"))
        client.enforce(Action.tool("files.read"))
        with pytest.raises(Denied) as caught:
            client.enforce(Action.tool("files.read"))
    assert caught.value.decision.reason is Reason.LIMIT
    assert caught.value.decision.rule == "agent:limits.run.max_steps"


def test_run_max_duration(tmp_path: Path) -> None:
    policy = _policy(limits={"run": {"max_duration": "30m"}})
    with Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client:
        started = datetime.now(UTC) - timedelta(hours=2)
        task = Task("run-old", client.instance_id, started_at=started)
        with use_task(task), pytest.raises(Denied) as caught:
            client.enforce(Action.tool("files.read"))
    assert caught.value.decision.reason is Reason.LIMIT
    assert caught.value.decision.rule == "agent:limits.run.max_duration"


def test_run_max_cost(tmp_path: Path) -> None:
    policy = _policy(limits={"run": {"max_cost_usd": 0.01}})
    with (
        Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client,
        client.task("run-cost"),
    ):
        client.record_usage("openai/gpt-4.1", 10_000, 0)
        with pytest.raises(Denied) as caught:
            client.enforce(Action.tool("files.read"))
    assert caught.value.decision.reason is Reason.LIMIT
    assert caught.value.decision.rule == "agent:limits.run.max_cost_usd"


def test_circuit_open_suspends_the_client(tmp_path: Path) -> None:
    policy = _policy(circuit_breaker={"denials": 2, "window": "1h", "action": "suspend"})
    with Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client:
        for _ in range(2):
            with pytest.raises(Denied) as denied:
                client.enforce(Action.tool("shell.exec"))
            assert denied.value.decision.reason is Reason.NOT_GRANTED
        with pytest.raises(Denied) as opened:
            client.enforce(Action.tool("files.read"))
        assert opened.value.decision.reason is Reason.CIRCUIT_OPEN
        assert client.suspended
        with pytest.raises(Denied) as later:
            client.enforce(Action.tool("files.read"))
    assert later.value.decision.reason is Reason.SUSPENDED


def test_circuit_open_revokes_the_run(tmp_path: Path) -> None:
    policy = _policy(circuit_breaker={"denials": 2, "window": "1h", "action": "revoke_run"})
    with (
        Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client,
        client.task("run-revoke"),
    ):
        for _ in range(2):
            with pytest.raises(Denied) as denied:
                client.enforce(Action.tool("shell.exec"))
            assert denied.value.decision.reason is Reason.NOT_GRANTED
        with pytest.raises(Denied) as opened:
            client.enforce(Action.tool("files.read"))
        assert opened.value.decision.reason is Reason.CIRCUIT_OPEN
        with pytest.raises(Denied) as later:
            client.enforce(Action.tool("files.read"))
    assert later.value.decision.reason is Reason.RUN_REVOKED
