"""Instance quotas are counted in-process, including model reservations."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_tessera import Tessera
from agent_tessera.errors import Denied
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.quotas import model_reservation
from agent_tessera.types import Action, Reason


def test_model_reservation_scales_characters() -> None:
    assert model_reservation(0, 100) == 102
    assert model_reservation(8, 0) == 3


def test_instance_tool_quota_denies_the_third_call(tmp_path: Path) -> None:
    with _client(
        tmp_path,
        {
            "tools": {"allow": ["files.read"]},
            "limits": {
                "quotas": [
                    {
                        "name": "local-calls",
                        "metric": "tool_calls",
                        "window": "day",
                        "scope": "instance",
                        "max": 2,
                    }
                ]
            },
        },
    ) as client:
        client.enforce(Action.tool("files.read"))
        client.enforce(Action.tool("files.read"))
        decision = client.check(Action.tool("files.read"))
    assert decision.reason is Reason.LIMIT
    assert decision.rule == "agent:limits.quotas[local-calls]"


def test_reservation_denies_before_the_model_runs(tmp_path: Path) -> None:
    called: list[str] = []
    with _client(tmp_path, _token_quota(5)) as client:

        @client.model_call("local/echo")
        def complete(prompt: str) -> str:
            called.append(prompt)
            return prompt

        with pytest.raises(Denied) as caught:
            complete("hi")
    assert called == []
    assert caught.value.decision.reason is Reason.LIMIT
    assert caught.value.decision.rule == "agent:limits.quotas[local-tokens]"


def test_reservation_reconciles_to_measured_usage(tmp_path: Path) -> None:
    with _client(tmp_path, _token_quota(20)) as client:

        @client.model_call("local/echo")
        def complete(prompt: str) -> str:
            client.record_usage("local/echo", 10, 5)
            return prompt

        assert complete("x") == "x"
        with pytest.raises(Denied):
            complete("x")


def _token_quota(maximum: int) -> dict[str, object]:
    return {
        "models": {"allow": ["local/*"]},
        "limits": {
            "per_call": {"max_output_tokens": 10},
            "quotas": [
                {
                    "name": "local-tokens",
                    "metric": "tokens",
                    "window": "day",
                    "scope": "instance",
                    "max": maximum,
                }
            ],
        },
    }


def _client(tmp_path: Path, spec: dict[str, object]) -> Tessera:
    policy = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot"},
            "spec": spec,
        }
    )
    return Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl")
