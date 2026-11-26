"""Chain headers and in-process handoff share run and principal."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from agent_tessera import Tessera
from agent_tessera.context import current_principal, current_task
from agent_tessera.delegation import (
    PARENT_SPAN_HEADER,
    RUN_HEADER,
    chain_headers,
    parent_span_from_headers,
    run_id_from_headers,
)
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.remote.connection import ServerConnection, ServerSettings
from agent_tessera.remote.identity import RuntimeCredential
from agent_tessera.types import Principal

_TOKEN = "actor-jwt"  # noqa: S105
_SPAN = re.compile(r"^[0-9a-f]{16}$")


def test_chain_headers_and_run_lookup() -> None:
    assert chain_headers(access_token=None, run_id=None) == {}
    assert chain_headers(access_token=_TOKEN, run_id="run-1") == {
        "Authorization": f"Bearer {_TOKEN}",
        RUN_HEADER: "run-1",
    }
    assert chain_headers(
        access_token=None,
        run_id=None,
        parent_span_id="0123456789abcdef",
    ) == {PARENT_SPAN_HEADER: "0123456789abcdef"}
    assert chain_headers(access_token=None, run_id=None, parent_span_id="nope") == {}
    assert run_id_from_headers({"tessera-run": "run-9"}) == "run-9"
    assert run_id_from_headers({}) is None
    assert parent_span_from_headers({PARENT_SPAN_HEADER: "0123456789ABCDEF"}) == (
        "0123456789abcdef"
    )
    assert parent_span_from_headers({PARENT_SPAN_HEADER: "short"}) is None
    assert parent_span_from_headers({}) is None


def test_http_client_sets_authorization_and_run(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _connection, original = _server_client(tmp_path, handler)
    try:
        with client.task("run-42"), client.http_client() as http:
            request = httpx.Request("GET", "https://worker.example/work")
            for hook in http.event_hooks["request"]:
                hook(request)
            assert request.headers.get("Authorization") == f"Bearer {_TOKEN}"
            assert request.headers.get(RUN_HEADER) == "run-42"
            assert request.headers.get(PARENT_SPAN_HEADER) is None
    finally:
        original.close()
        client.close()


def test_http_client_sets_parent_span_during_call(tmp_path: Path) -> None:
    seen: list[str | None] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _connection, original = _server_client(tmp_path, handler)

    @client.tool("files.read")
    def read() -> str:
        with client.http_client() as http:
            request = httpx.Request("GET", "https://worker.example/work")
            for hook in http.event_hooks["request"]:
                hook(request)
            seen.append(request.headers.get(PARENT_SPAN_HEADER))
        return "ok"

    try:
        assert read() == "ok"
        assert len(seen) == 1
        assert seen[0] is not None
        assert _SPAN.fullmatch(seen[0])
    finally:
        original.close()
        client.close()


def test_worker_call_parents_to_caller_span_and_records_chain(tmp_path: Path) -> None:
    orchestrator_path = tmp_path / "a.jsonl"
    worker_path = tmp_path / "b.jsonl"
    orchestrator = Tessera(
        "orchestrator",
        policy=_policy("orchestrator", tools=["delegate", "files.read"]),
        audit_path=orchestrator_path,
    )
    worker = Tessera(
        "worker",
        policy=_policy("worker"),
        audit_path=worker_path,
    )

    @worker.tool("files.read")
    def work() -> str:
        return "done"

    @orchestrator.tool("delegate")
    def delegate() -> str:
        with worker.receive_handoff(orchestrator):
            return work()

    try:
        with orchestrator.task("run-shared"):
            assert delegate() == "done"
        outer = next(
            event for event in _events(orchestrator_path) if _decision_resource(event) == "delegate"
        )
        worker_decision = next(
            event for event in _events(worker_path) if event.get("type") == "decision"
        )
        worker_call = next(event for event in _events(worker_path) if event.get("type") == "call")
        assert worker_decision["parent_span_id"] == outer["span_id"]
        assert worker_call["parent_span_id"] == outer["span_id"]
        assert worker_call["chain"] == ["orchestrator"]
    finally:
        orchestrator.close()
        worker.close()


def test_handle_call_binds_parent_and_ignores_bad_header(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    worker = Tessera("worker", policy=_policy("worker"), audit_path=path)
    parent = "fedcba9876543210"

    @worker.tool("files.read")
    def read() -> str:
        return "ok"

    try:
        with worker.handle_call({PARENT_SPAN_HEADER: parent}):
            assert read() == "ok"
        decision = next(event for event in _events(path) if event["type"] == "decision")
        assert decision["parent_span_id"] == parent
        path.write_text("", encoding="utf-8")
        with worker.handle_call({PARENT_SPAN_HEADER: "not-a-span"}):
            assert read() == "ok"
        decision = next(event for event in _events(path) if event["type"] == "decision")
        assert decision["parent_span_id"] is None
    finally:
        worker.close()


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _decision_resource(event: dict[str, object]) -> str | None:
    if event.get("type") != "decision":
        return None
    action = event.get("action")
    if not isinstance(action, dict):
        return None
    resource = action.get("resource")
    return resource if isinstance(resource, str) else None


def test_receive_handoff_shares_run_and_principal(tmp_path: Path) -> None:
    orchestrator = Tessera(
        "orchestrator",
        policy=_policy("orchestrator"),
        audit_path=tmp_path / "a.jsonl",
    )
    worker = Tessera(
        "worker",
        policy=_policy("worker"),
        audit_path=tmp_path / "b.jsonl",
    )
    try:
        with (
            orchestrator.on_behalf_of("ada", ["support"]),
            orchestrator.task("run-shared"),
            worker.receive_handoff(orchestrator) as run,
        ):
            assert run is not None
            assert run.id == "run-shared"
            assert current_task(worker.instance_id) is run
            outer = current_task(orchestrator.instance_id)
            assert outer is not None
            assert outer.id == "run-shared"
            principal = current_principal()
            assert principal is not None
            assert principal == Principal("ada", frozenset({"support"}))
        assert current_task(worker.instance_id) is None
    finally:
        orchestrator.close()
        worker.close()


def test_receive_handoff_without_caller_task(tmp_path: Path) -> None:
    caller = Tessera("caller", policy=_policy("caller"), audit_path=tmp_path / "a.jsonl")
    callee = Tessera("callee", policy=_policy("callee"), audit_path=tmp_path / "b.jsonl")
    try:
        with callee.receive_handoff(caller) as run:
            assert run is None
            assert current_task(callee.instance_id) is None
    finally:
        caller.close()
        callee.close()


def _server_client(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[Tessera, ServerConnection, httpx.Client]:
    client = Tessera("bot", policy=_policy("bot"))
    settings = ServerSettings(
        url="http://127.0.0.1:9",
        agent_token=None,
        fail_mode="closed",
        cache_dir=tmp_path / "cache",
        timeout=1.0,
        spool_dir=tmp_path / "spool",
    )
    connection = ServerConnection(
        "bot",
        client.instance_id,
        settings,
        on_policy=lambda _policy, _status: None,
        on_unusable=lambda: None,
        on_rejected=lambda: None,
    )
    original = connection._http
    connection._http = httpx.Client(
        base_url="http://127.0.0.1:9",
        transport=httpx.MockTransport(handler),
    )
    connection._credential = RuntimeCredential(_TOKEN, 900, "agent-1", "bot", "prod")
    client._connection = connection
    return client, connection, original


def _policy(agent: str, *, tools: list[str] | None = None) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": agent, "environment": "prod"},
            "spec": {
                "tools": {"allow": ["files.read"] if tools is None else tools},
                "delegation": {"allow_on_behalf_of": True},
            },
        }
    )
