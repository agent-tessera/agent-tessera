"""Evaluator and baseline-merge tests for v0.3 controls."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_tessera.policy.effective import (
    CircuitLimit,
    LimitValue,
    build_effective_policy,
)
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.loader import load_agent_policy, load_baseline_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.types import Action, ActionKind, Effect, Principal, Reason

_EXAMPLES = Path(__file__).resolve().parents[3] / "policies" / "examples"
_MADRID = ZoneInfo("Europe/Madrid")
_INSIDE = datetime(2026, 11, 2, 10, 0, tzinfo=_MADRID)
_OUTSIDE = datetime(2026, 11, 2, 21, 0, tzinfo=_MADRID)


def _agent(
    spec: dict[str, object] | None = None,
    **metadata: object,
) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod", "owner": "team", **metadata},
            "spec": spec or {},
        }
    )


def _baseline(name: str, spec: dict[str, object]) -> BaselinePolicy:
    return BaselinePolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "BaselinePolicy",
            "metadata": {"name": name},
            "spec": spec,
        }
    )


def _decide(
    spec: dict[str, object],
    action: Action,
    effect: Effect,
    reason: Reason,
    rule: str | None,
    *,
    metadata: dict[str, object] | None = None,
    baselines: list[BaselinePolicy] | None = None,
    state: RuntimeState | None = None,
) -> None:
    policy = build_effective_policy(_agent(spec, **(metadata or {})), baselines or [])
    decision = evaluate(policy, action, state)
    assert decision.effect is effect
    assert decision.reason is reason
    assert decision.rule == rule


def test_spend_baseline_merges_the_stricter_controls() -> None:
    policy = build_effective_policy(
        load_agent_policy(_EXAMPLES / "spend-guard.yaml"),
        [load_baseline_policy(_EXAMPLES / "spend-baseline.yaml")],
    )
    controls = policy.controls

    assert controls.autonomy == "supervised"
    assert controls.active_hours is not None
    assert controls.active_hours.days == frozenset({0, 1, 2, 3, 4})
    assert controls.max_output_tokens == LimitValue(
        2048,
        "baseline/spend-baseline:limits.per_call.max_output_tokens",
    )
    assert controls.run_max_duration == LimitValue(7200, "agent:limits.run.max_duration")
    assert controls.circuit_breaker == CircuitLimit(
        3,
        300,
        "revoke_run",
        "baseline/spend-baseline:circuit_breaker",
    )
    quotas = {quota.name: quota for quota in controls.quotas}
    assert quotas["gpt41-per-user-daily"].max == 200_000
    assert quotas["org-daily-cost"].scope == "org"
    assert quotas["org-daily-cost"].rule == "baseline/spend-baseline:limits.quotas[org-daily-cost]"
    assert ("credential", "agent:effects.deny") in controls.effect_deny
    assert ("irreversible", "agent:effects.require_approval") in controls.effect_require_approval
    assert ("financial", "baseline/spend-baseline:effects.require_approval") in (
        controls.effect_require_approval
    )
    classified = {pattern: tags for pattern, tags, _rule in controls.effect_classify}
    assert classified["zendesk.refund"] == frozenset({"financial", "irreversible"})


def test_same_quota_key_keeps_the_smaller_max() -> None:
    policy = build_effective_policy(
        _agent(
            {
                "limits": {
                    "per_call": {"max_output_tokens": 10},
                    "quotas": [
                        {
                            "name": "agent-daily",
                            "metric": "tokens",
                            "window": "day",
                            "scope": "agent",
                            "max": 100,
                            "kinds": ["tool", "model"],
                        }
                    ],
                }
            }
        ),
        [
            _baseline(
                "ceiling",
                {
                    "limits": {
                        "per_call": {"max_output_tokens": 10},
                        "quotas": [
                            {
                                "name": "baseline-daily",
                                "metric": "tokens",
                                "window": "day",
                                "scope": "agent",
                                "max": 40,
                                "kinds": ["model", "tool"],
                            }
                        ],
                    }
                },
            )
        ],
    )

    assert len(policy.controls.quotas) == 1
    quota = policy.controls.quotas[0]
    assert quota.max == 40
    assert quota.rule == "baseline/ceiling:limits.quotas[baseline-daily]"
    assert quota.kinds == ("model", "tool")


@pytest.mark.parametrize(
    ("metadata", "spec", "action", "state", "reason", "rule"),
    [
        (
            {
                "autonomy": {
                    "level": "interactive",
                    "active_hours": {
                        "tz": "Europe/Madrid",
                        "days": ["mon-fri"],
                        "from": "08:00",
                        "to": "20:00",
                    },
                }
            },
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.read"),
            RuntimeState(now=_OUTSIDE),
            Reason.OUTSIDE_WINDOW,
            "agent:metadata.autonomy.active_hours",
        ),
        (
            {
                "autonomy": {
                    "level": "interactive",
                    "active_hours": {
                        "tz": "Europe/Madrid",
                        "days": ["mon-fri"],
                        "from": "22:00",
                        "to": "06:00",
                    },
                }
            },
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.read"),
            RuntimeState(now=datetime(2026, 11, 3, 12, 0, tzinfo=_MADRID)),
            Reason.OUTSIDE_WINDOW,
            "agent:metadata.autonomy.active_hours",
        ),
        (
            {"autonomy": "assistive"},
            {"tools": {"allow": ["files.write"]}},
            Action.tool("files.write"),
            None,
            Reason.ASSISTIVE_EFFECT,
            "agent:metadata.autonomy",
        ),
        (
            {"autonomy": "assistive"},
            {
                "tools": {"allow": ["files.write"]},
                "effects": {"classify": {"files.write": ["read", "write"]}},
            },
            Action.tool("files.write"),
            None,
            Reason.ASSISTIVE_EFFECT,
            "agent:metadata.autonomy",
        ),
        (
            {},
            {
                "tools": {"allow": ["files.write"]},
                "effects": {
                    "classify": {"files.write": ["delete"]},
                    "deny": ["delete"],
                },
            },
            Action.tool("files.write"),
            None,
            Reason.EFFECT_DENIED,
            "agent:effects.deny",
        ),
        (
            {},
            {
                "tools": {"allow": ["files.write"]},
                "effects": {"require_approval": ["irreversible"]},
            },
            Action.tool("files.write"),
            None,
            Reason.EFFECT_APPROVAL,
            "agent:effects.require_approval",
        ),
        (
            {"autonomy": "autonomous"},
            {"tools": {"require_approval": ["files.delete"]}},
            Action.tool("files.delete"),
            RuntimeState(approvals_available=False),
            Reason.APPROVAL_UNAVAILABLE,
            "agent:tools.require_approval[0]",
        ),
        (
            {},
            {"tools": {"require_approval": ["files.delete"]}},
            Action.tool("files.delete"),
            RuntimeState(approvals_available=False),
            Reason.APPROVAL_RULE,
            "agent:tools.require_approval[0]",
        ),
        (
            {},
            {
                "limits": {
                    "per_call": {"max_output_tokens": 100},
                    "quotas": [
                        {
                            "name": "daily-tokens",
                            "metric": "tokens",
                            "window": "day",
                            "scope": "principal",
                            "max": 10,
                        }
                    ],
                },
                "models": {"allow": ["openai/*"]},
            },
            Action.model("openai/gpt-4.1"),
            None,
            Reason.QUOTA_UNAVAILABLE,
            "agent:limits.quotas[daily-tokens]",
        ),
        (
            {},
            {
                "limits": {
                    "per_call": {"max_output_tokens": 100},
                    "quotas": [
                        {
                            "name": "daily-tokens",
                            "metric": "tokens",
                            "window": "day",
                            "scope": "agent",
                            "max": 10,
                        }
                    ],
                },
                "models": {"allow": ["openai/*"]},
            },
            Action.model("openai/gpt-4.1"),
            RuntimeState(quota_usage={"daily-tokens": 10}),
            Reason.LIMIT,
            "agent:limits.quotas[daily-tokens]",
        ),
        (
            {},
            {
                "limits": {"per_call": {"max_output_tokens": 100}},
                "models": {"allow": ["openai/*"]},
            },
            Action(ActionKind.MODEL, "openai/gpt-4.1", {"max_tokens": 101}),
            None,
            Reason.LIMIT,
            "agent:limits.per_call.max_output_tokens",
        ),
        (
            {},
            {
                "limits": {"concurrency": {"max_in_flight_tool_calls": 2}},
                "tools": {"allow": ["files.read"]},
            },
            Action.tool("files.read"),
            RuntimeState(in_flight_tool_calls=2),
            Reason.LIMIT,
            "agent:limits.concurrency.max_in_flight_tool_calls",
        ),
        (
            {},
            {
                "limits": {"run": {"max_steps": 3, "max_duration": "30m"}},
                "tools": {"allow": ["files.read"]},
            },
            Action.tool("files.read"),
            RuntimeState(run_elapsed_seconds=1800),
            Reason.LIMIT,
            "agent:limits.run.max_duration",
        ),
        (
            {},
            {
                "tools": {"allow": ["files.read"]},
                "circuit_breaker": {"denials": 2, "window": "10m", "action": "suspend"},
            },
            Action.tool("files.read"),
            RuntimeState(recent_denials=2),
            Reason.CIRCUIT_OPEN,
            "agent:circuit_breaker",
        ),
    ],
)
def test_control_denies_and_approvals(  # noqa: PLR0917
    metadata: dict[str, object],
    spec: dict[str, object],
    action: Action,
    state: RuntimeState | None,
    reason: Reason,
    rule: str,
) -> None:
    effect = Effect.REQUIRE_APPROVAL if reason is Reason.EFFECT_APPROVAL else Effect.DENY
    if reason is Reason.APPROVAL_RULE:
        effect = Effect.REQUIRE_APPROVAL
    _decide(spec, action, effect, reason, rule, metadata=metadata, state=state)


def test_inside_window_and_read_only_assistive_are_allowed() -> None:
    _decide(
        {"tools": {"allow": ["files.read"]}},
        Action.tool("files.read"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
        metadata={
            "autonomy": {
                "level": "interactive",
                "active_hours": {
                    "tz": "Europe/Madrid",
                    "days": ["mon-fri"],
                    "from": "22:00",
                    "to": "06:00",
                },
            }
        },
        state=RuntimeState(now=datetime(2026, 11, 2, 23, 0, tzinfo=_MADRID)),
    )
    _decide(
        {
            "tools": {"allow": ["files.read"]},
            "effects": {"classify": {"files.read": ["read"]}},
        },
        Action.tool("files.read"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
        metadata={"autonomy": "assistive"},
    )


def test_suspension_wins_over_the_operating_window() -> None:
    _decide(
        {"tools": {"allow": ["files.read"]}},
        Action.tool("files.read"),
        Effect.DENY,
        Reason.SUSPENDED,
        None,
        metadata={
            "autonomy": {
                "level": "interactive",
                "active_hours": {
                    "tz": "Europe/Madrid",
                    "days": ["mon"],
                    "from": "08:00",
                    "to": "09:00",
                },
            }
        },
        state=RuntimeState(suspended=True, now=_INSIDE),
    )


def test_tool_quota_does_not_limit_a_model_call() -> None:
    _decide(
        {
            "models": {"allow": ["openai/*"]},
            "limits": {
                "quotas": [
                    {
                        "name": "tool-hour",
                        "metric": "tool_calls",
                        "window": "hour",
                        "scope": "agent",
                        "max": 1,
                        "kinds": ["tool"],
                    }
                ]
            },
        },
        Action.model("openai/gpt-4.1"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:models.allow[0]",
        state=RuntimeState(quota_usage={"tool-hour": 5}),
    )


def test_call_window_output_quota_uses_this_call() -> None:
    _decide(
        {
            "models": {"allow": ["openai/*"]},
            "limits": {
                "per_call": {"max_output_tokens": 100},
                "quotas": [
                    {
                        "name": "one-call",
                        "metric": "output_tokens",
                        "window": "call",
                        "scope": "instance",
                        "max": 10,
                    }
                ],
            },
        },
        Action.model("openai/gpt-4.1"),
        Effect.DENY,
        Reason.LIMIT,
        "agent:limits.quotas[one-call]",
        state=RuntimeState(output_tokens=11),
    )


def test_run_quota_without_a_run_id_is_unavailable() -> None:
    _decide(
        {
            "tools": {"allow": ["files.read"]},
            "limits": {
                "quotas": [
                    {
                        "name": "run-steps",
                        "metric": "steps",
                        "window": "run",
                        "scope": "run",
                        "max": 5,
                    }
                ]
            },
        },
        Action.tool("files.read"),
        Effect.DENY,
        Reason.QUOTA_UNAVAILABLE,
        "agent:limits.quotas[run-steps]",
    )


def test_baseline_effect_tag_denies_a_classified_tool() -> None:
    _decide(
        {
            "tools": {"allow": ["files.write"]},
            "effects": {"classify": {"files.*": ["write"]}},
        },
        Action.tool("files.write"),
        Effect.DENY,
        Reason.EFFECT_DENIED,
        "baseline/safety:effects.deny",
        baselines=[
            _baseline(
                "safety",
                {"effects": {"deny": ["write"], "classify": {"files.*": ["delete"]}}},
            )
        ],
    )
    policy = build_effective_policy(
        _agent(
            {
                "tools": {"allow": ["files.write"]},
                "effects": {"classify": {"files.*": ["write"]}},
            }
        ),
        [_baseline("safety", {"effects": {"classify": {"files.*": ["delete"]}}})],
    )
    classified = policy.controls.effect_classify
    assert classified[0][1] == frozenset({"write", "delete"})


def test_principal_quota_allows_a_principal_under_the_ceiling() -> None:
    _decide(
        {
            "tools": {"allow": ["files.read"]},
            "delegation": {"allow_on_behalf_of": True},
            "limits": {
                "quotas": [
                    {
                        "name": "user-reads",
                        "metric": "tool_calls",
                        "window": "day",
                        "scope": "principal",
                        "max": 5,
                    }
                ]
            },
        },
        Action(ActionKind.TOOL, "files.read", principal=Principal("ada")),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
        state=RuntimeState(quota_usage={"user-reads": 4}),
    )
