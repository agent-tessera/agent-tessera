"""OpenTelemetry spans are optional and record governance decisions."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_span_id, format_trace_id

import agent_tessera.otel
from agent_tessera import AgentPolicy, Tessera
from agent_tessera.types import Action


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
            "spec": {"tools": {"allow": ["files.read"]}},
        }
    )


def test_import_stays_under_100ms() -> None:
    script = (
        "import time\n"
        "start = time.perf_counter()\n"
        "import agent_tessera\n"
        "print(time.perf_counter() - start)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(result.stdout.strip()) < 0.1


def test_configure_without_an_endpoint_is_a_noop() -> None:
    assert agent_tessera.otel.configure() is False
    assert agent_tessera.otel.configure("  ") is False


def test_missing_extra_does_not_block_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_tessera.otel._state, "missing", True)
    monkeypatch.setattr(agent_tessera.otel._state, "tracer", None)
    client = Tessera("bot", policy=_policy(), audit_path=tmp_path / "audit.jsonl")
    decision = client.check(Action.tool("files.read"))
    client.close()
    assert decision.effect.value == "allow"


def _install_memory_tracer() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Inject the tracer so tests do not fight the process-wide provider lock.
    agent_tessera.otel._state.tracer = cast(Any, provider.get_tracer("agent_tessera"))
    agent_tessera.otel._state.missing = False
    agent_tessera.otel._state.configured = False
    return exporter


def test_spans_record_the_decision(tmp_path: Path) -> None:
    exporter = _install_memory_tracer()
    path = tmp_path / "audit.jsonl"
    with Tessera("bot", policy=_policy(), audit_path=path) as client:
        instance = client.instance_id

        @client.tool("files.read")
        def read() -> str:
            return "ok"

        client.enforce(Action.tool("files.read"))
        assert read() == "ok"
    recorded = exporter.get_finished_spans()
    names = [span.name for span in recorded]
    assert "tessera.evaluate" in names
    assert "tessera.enforce" in names
    assert "tessera.call" in names
    evaluate = next(span for span in recorded if span.name == "tessera.evaluate")
    assert evaluate.attributes is not None
    assert evaluate.attributes["tessera.agent"] == "bot"
    assert evaluate.attributes["tessera.decision"] == "allow"
    assert str(evaluate.attributes["tessera.policy_version"]).startswith("sha256:")
    call = next(span for span in recorded if span.name == "tessera.call")
    assert call.attributes is not None
    assert call.attributes["tessera.instance"] == instance


def test_call_event_span_id_matches_exported_otel_span(tmp_path: Path) -> None:
    exporter = _install_memory_tracer()
    path = tmp_path / "audit.jsonl"
    with Tessera("bot", policy=_policy(), audit_path=path) as client:

        @client.tool("files.read")
        def read() -> str:
            return "ok"

        assert read() == "ok"
    call_span = next(span for span in exporter.get_finished_spans() if span.name == "tessera.call")
    context = call_span.get_span_context()
    assert context is not None
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    decision = next(event for event in events if event["type"] == "decision")
    call = next(event for event in events if event["type"] == "call")
    span_id = format_span_id(context.span_id)
    trace_id = format_trace_id(context.trace_id)
    assert decision["span_id"] == span_id
    assert call["span_id"] == span_id
    assert decision["trace_id"] == trace_id
    assert call["trace_id"] == trace_id


def test_without_otel_events_have_no_trace_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_tessera.otel._state, "missing", True)
    monkeypatch.setattr(agent_tessera.otel._state, "tracer", None)
    path = tmp_path / "audit.jsonl"
    client = Tessera("bot", policy=_policy(), audit_path=path)

    @client.tool("files.read")
    def read() -> str:
        return "ok"

    assert read() == "ok"
    client.close()
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    decision = next(event for event in events if event["type"] == "decision")
    call = next(event for event in events if event["type"] == "call")
    assert "trace_id" not in decision
    assert "trace_id" not in call
    assert isinstance(decision["span_id"], str)
    assert call["span_id"] == decision["span_id"]
