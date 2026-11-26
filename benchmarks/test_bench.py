"""Performance budgets for policy evaluation and import."""

from __future__ import annotations

import math
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from agent_tessera.audit import AuditLog
from agent_tessera.client import Tessera
from agent_tessera.effects import classify_effect
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.types import Action, ActionKind

_WARMUP = 200
_SAMPLES = 2_000
_EVALUATE_NS = 100_000
_ENFORCE_NS = 1_000_000
_IMPORT_NS = 100_000_000


@pytest.mark.benchmark
def test_evaluate_p99_stays_under_100_microseconds() -> None:
    policy = build_effective_policy(
        AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {"tools": {"allow": [f"tool-{index}" for index in range(200)]}},
            }
        )
    )
    action = Action.tool("tool-199")
    for _ in range(_WARMUP):
        evaluate(policy, action)
    samples = [_elapsed(lambda: evaluate(policy, action)) for _ in range(_SAMPLES)]
    assert _percentile(samples, 99) < _EVALUATE_NS


@pytest.mark.benchmark
def test_enforce_p99_stays_under_1_millisecond(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AuditLog, "write", lambda self, event_type, fields: {})
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot"},
            "spec": {"tools": {"allow": ["files.read"]}},
        }
    )
    with Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client:
        action = Action.tool("files.read")
        for _ in range(_WARMUP):
            client.enforce(action)
        samples = [_elapsed(lambda: client.enforce(action)) for _ in range(_SAMPLES)]
    assert _percentile(samples, 99) < _ENFORCE_NS


@pytest.mark.benchmark
def test_import_median_stays_under_100_milliseconds() -> None:
    samples = [_import_ns() for _ in range(5)]
    assert _percentile(samples, 50) < _IMPORT_NS


@pytest.mark.benchmark
def test_quota_check_p99_stays_under_100_microseconds() -> None:
    policy = build_effective_policy(
        AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {
                    "tools": {"allow": ["files.read"]},
                    "limits": {
                        "quotas": [
                            {
                                "name": "reads",
                                "metric": "tool_calls",
                                "window": "day",
                                "scope": "instance",
                                "max": 10,
                                "resources": ["files.read"],
                            }
                        ]
                    },
                },
            }
        )
    )
    action = Action.tool("files.read")
    for _ in range(_WARMUP):
        evaluate(policy, action)
    samples = [_elapsed(lambda: evaluate(policy, action)) for _ in range(_SAMPLES)]
    assert _percentile(samples, 99) < _EVALUATE_NS


@pytest.mark.benchmark
def test_effect_classify_p99_stays_under_100_microseconds() -> None:
    policy = build_effective_policy(
        AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {
                    "tools": {"allow": ["files.read"]},
                    "effects": {
                        "classify": {f"files.{index}": ["read"] for index in range(50)},
                    },
                },
            }
        )
    )
    for _ in range(_WARMUP):
        classify_effect(policy, "files.49")
    samples = [_elapsed(lambda: classify_effect(policy, "files.49")) for _ in range(_SAMPLES)]
    assert _percentile(samples, 99) < _EVALUATE_NS


@pytest.mark.benchmark
def test_chain_evaluate_p99_stays_under_100_microseconds() -> None:
    policy = build_effective_policy(
        AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "worker"},
                "spec": {
                    "tools": {"allow": [f"tool-{index}" for index in range(200)]},
                    "agents": {"caller_constraint": "intersect"},
                },
            }
        )
    )
    peers = {
        "planner": {ActionKind.TOOL: tuple(f"tool-{index}" for index in range(20))},
        "orchestrator": {ActionKind.TOOL: ("tool-199", "zendesk.read")},
    }
    policy = replace(policy, peer_allows=peers)
    action = Action.tool("tool-199")
    state = RuntimeState(chain=("planner", "orchestrator"))
    for _ in range(_WARMUP):
        evaluate(policy, action, state)
    samples = [_elapsed(lambda: evaluate(policy, action, state)) for _ in range(_SAMPLES)]
    assert _percentile(samples, 99) < _EVALUATE_NS


def _elapsed(call: Callable[[], object]) -> int:
    start = time.perf_counter_ns()
    call()
    return time.perf_counter_ns() - start


def _percentile(samples: list[int], percent: int) -> int:
    ordered = sorted(samples)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[index]


def _import_ns() -> int:
    script = (
        "import time\n"
        "start = time.perf_counter_ns()\n"
        "import agent_tessera\n"
        "print(time.perf_counter_ns() - start)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())
