"""Run, instance, and static supervisor resolution."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent_tessera import Action, Tessera
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.registry import LocalRegistry, RegistryEntry
from agent_tessera.remote.identity import bootstrap
from agent_tessera.supervision import resolve_supervisor

_ACCESS = "runtime-jwt"
_AGENT_TOKEN = "tsr_secret"  # noqa: S105
_TOKEN = {
    "access_token": _ACCESS,
    "expires_in": 600,
    "agent_id": "agent-1",
    "agent": "bot",
    "environment": "dev",
}


def test_run_beats_instance_and_static() -> None:
    static = ["", "user:static"]
    assert resolve_supervisor("user:run", "user:instance", static) == "user:run"
    assert resolve_supervisor(None, "user:instance", static) == "user:instance"
    assert resolve_supervisor("", None, static) == "user:static"
    assert resolve_supervisor(None, None, ["", ""]) is None


def test_task_supervisor_is_audited_and_wins(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)
    client._bound_supervisor = "user:instance"
    try:
        with client.task("run-1", supervisor="user:run"):
            client.enforce(Action.tool("files.read"))
            assert client._runtime_state().supervisor == "user:run"
    finally:
        client.close()
    decision = _decision(path)
    assert decision["task"] == "run-1"
    assert decision["supervisor"] == "user:run"


def test_registry_supervisor_fills_runtime_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "bot.yaml"
    policy.write_text(_policy_text("bot", "dev"), encoding="utf-8")
    store = LocalRegistry(tmp_path / "registry.yaml")
    store.register(
        RegistryEntry(
            name="bot",
            owner="dev@localhost",
            policy="bot.yaml",
            status="active",
            supervisors=("user:static",),
        )
    )
    monkeypatch.setenv("TESSERA_REGISTRY", str(store.path))
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    client = Tessera("bot", audit_path=tmp_path / "audit.jsonl")
    try:
        assert client._runtime_state().supervisor == "user:static"
        with client.task(supervisor="user:run"):
            assert client._runtime_state().supervisor == "user:run"
    finally:
        client.close()


def test_handoff_keeps_the_caller_supervisor(tmp_path: Path) -> None:
    caller = Tessera(
        "orchestrator",
        policy=_policy("orchestrator"),
        audit_path=tmp_path / "a.jsonl",
    )
    callee = Tessera("worker", policy=_policy("worker"), audit_path=tmp_path / "b.jsonl")
    try:
        with (
            caller.task("run-shared", supervisor="user:run"),
            callee.receive_handoff(caller) as run,
        ):
            assert run is not None
            assert run.supervisor == "user:run"
            assert callee._runtime_state().supervisor == "user:run"
    finally:
        caller.close()
        callee.close()


def test_bootstrap_sends_bound_supervisor() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_TOKEN)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="http://127.0.0.1:9") as http:
        issued = bootstrap(
            http,
            agent="bot",
            instance_id="ab" * 16,
            agent_token=_AGENT_TOKEN,
            bound_supervisor="user:ada",
        )
    assert issued.access_token == _ACCESS
    assert seen["grant_type"] == "registration"
    assert seen["bound_supervisor"] == "user:ada"


def _policy_text(agent: str, environment: str) -> str:
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {agent}\n"
        f"  environment: {environment}\n"
        "spec:\n"
        "  tools:\n"
        "    allow: ['files.read']\n"
    )


def _policy(agent: str = "bot") -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": agent, "environment": "prod"},
            "spec": {"tools": {"allow": ["files.read"]}},
        }
    )


def _decision(path: Path) -> dict[str, object]:
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["type"] == "decision":
            return dict(record)
    raise AssertionError("no decision")
