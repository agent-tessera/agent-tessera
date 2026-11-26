"""Tests for the policy decision order."""

from pathlib import Path

import pytest

from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.loader import load_agent_policy, load_baseline_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.types import Action, ActionKind, Effect, Principal, Reason

_EXAMPLES = Path(__file__).resolve().parents[3] / "policies" / "examples"


def _agent(spec: dict[str, object] | None = None) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod", "owner": "team"},
            "spec": spec or {},
        }
    )


def _baseline(spec: dict[str, object] | None = None) -> BaselinePolicy:
    return BaselinePolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "BaselinePolicy",
            "metadata": {"name": "prod-baseline"},
            "spec": {"selector": {"environment": "prod"}, **(spec or {})},
        }
    )


def _expect(
    policy_spec: dict[str, object],
    action: Action,
    effect: Effect,
    reason: Reason,
    rule: str | None,
    *,
    baselines: list[dict[str, object]] | None = None,
    state: RuntimeState | None = None,
) -> None:
    policy = build_effective_policy(
        _agent(policy_spec),
        [_baseline(item) for item in baselines or []],
    )
    decision = evaluate(policy, action, state)
    assert decision.effect is effect
    assert decision.reason is reason
    assert decision.rule == rule
    assert decision.policy_version == policy.version
    assert decision.action is action


@pytest.mark.parametrize(
    "case",
    [
        (
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.read"),
            Effect.DENY,
            Reason.SUSPENDED,
            None,
            None,
            RuntimeState(suspended=True),
        ),
        (
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.read"),
            Effect.DENY,
            Reason.SUSPENDED,
            None,
            None,
            RuntimeState(suspended=True, run_revoked=True),
        ),
        (
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.read"),
            Effect.DENY,
            Reason.RUN_REVOKED,
            None,
            None,
            RuntimeState(run_revoked=True),
        ),
        (
            {"tools": {"allow": ["shell.*"], "deny": ["shell.*"]}},
            Action.tool("shell.exec"),
            Effect.DENY,
            Reason.DENY_RULE,
            "agent:tools.deny[0]",
            None,
            None,
        ),
        (
            {"tools": {"allow": ["shell.rm"]}},
            Action.tool("shell.rm"),
            Effect.DENY,
            Reason.DENY_RULE,
            "baseline/prod-baseline:tools.deny[0]",
            [{"tools": {"deny": ["shell.*"]}}],
            None,
        ),
        (
            {"tools": {"allow": ["files.read"]}, "delegation": {"allow_on_behalf_of": False}},
            Action(ActionKind.TOOL, "files.read", {}, Principal("ada")),
            Effect.DENY,
            Reason.DELEGATION,
            "agent:delegation.allow_on_behalf_of",
            None,
            None,
        ),
        (
            {
                "tools": {"allow": ["files.read"]},
                "delegation": {"allow_on_behalf_of": True, "user_groups": ["support"]},
            },
            Action(ActionKind.TOOL, "files.read", {}, Principal("ada", frozenset({"billing"}))),
            Effect.DENY,
            Reason.DELEGATION,
            "agent:delegation.user_groups",
            None,
            None,
        ),
        (
            {
                "models": {"allow": ["local/*"]},
                "guardrails": {"required": ["input.prompt_injection"], "on_missing": "block"},
            },
            Action.model("local/echo"),
            Effect.DENY,
            Reason.GUARDRAIL_MISSING,
            "agent:guardrails.required[0]",
            None,
            None,
        ),
        (
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.write"),
            Effect.DENY,
            Reason.NOT_GRANTED,
            None,
            None,
            None,
        ),
        (
            {"tools": {"allow": ["files.read"]}, "limits": {"max_steps_per_task": 2}},
            Action.tool("files.read"),
            Effect.DENY,
            Reason.LIMIT,
            "agent:limits.max_steps_per_task",
            None,
            RuntimeState(steps_in_task=2),
        ),
        (
            {"models": {"allow": ["local/*"]}, "limits": {"tokens_per_day": 10}},
            Action.model("local/echo"),
            Effect.DENY,
            Reason.LIMIT,
            "agent:limits.tokens_per_day",
            None,
            RuntimeState(guardrails_ran=frozenset(), tokens_today=10),
        ),
        (
            {
                "tools": {"allow": ["zendesk.read"]},
                "limits": {"calls_per_minute": {"zendesk.*": 2}},
            },
            Action.tool("zendesk.read"),
            Effect.DENY,
            Reason.LIMIT,
            "agent:limits.calls_per_minute[zendesk.*]",
            None,
            RuntimeState(calls_last_minute={"zendesk.*": 2}),
        ),
        (
            {"tools": {"allow": ["files.read"], "require_approval": ["files.read"]}},
            Action.tool("files.read"),
            Effect.REQUIRE_APPROVAL,
            Reason.APPROVAL_RULE,
            "agent:tools.require_approval[0]",
            None,
            None,
        ),
        (
            {"tools": {"allow": ["files.read"]}},
            Action.tool("files.read"),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
            None,
            None,
        ),
    ],
)
def test_each_decision_step(
    case: tuple[
        dict[str, object],
        Action,
        Effect,
        Reason,
        str | None,
        list[dict[str, object]] | None,
        RuntimeState | None,
    ],
) -> None:
    spec, action, effect, reason, rule, baselines, state = case
    _expect(spec, action, effect, reason, rule, baselines=baselines, state=state)


def test_delegation_without_a_principal_is_skipped() -> None:
    _expect(
        {"tools": {"allow": ["files.read"]}, "delegation": {"allow_on_behalf_of": False}},
        Action.tool("files.read"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
    )


def test_warn_and_non_input_guardrails_do_not_deny() -> None:
    _expect(
        {
            "models": {"allow": ["local/*"]},
            "guardrails": {
                "required": ["input.prompt_injection", "output.pii"],
                "on_missing": "warn",
            },
        },
        Action.model("local/echo"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:models.allow[0]",
    )
    _expect(
        {
            "models": {"allow": ["local/*"]},
            "guardrails": {"required": ["output.pii"], "on_missing": "block"},
        },
        Action.model("local/echo"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:models.allow[0]",
    )


def test_approval_grant_with_a_non_matching_condition_allows() -> None:
    _expect(
        {
            "tools": {
                "require_approval": [
                    {"name": "files.delete", "when": [{"arg": "force", "op": "eq", "value": True}]}
                ]
            }
        },
        Action.tool("files.delete", {"force": False}),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.require_approval[0]",
    )


def test_baseline_approval_requires_approval_when_the_agent_allows() -> None:
    _expect(
        {"tools": {"allow": ["files.write"]}},
        Action.tool("files.write"),
        Effect.REQUIRE_APPROVAL,
        Reason.APPROVAL_RULE,
        "baseline/prod-baseline:tools.require_approval[0]",
        baselines=[{"tools": {"require_approval": ["files.write"]}}],
    )


def test_baseline_approval_alone_does_not_grant() -> None:
    _expect(
        {},
        Action.tool("files.write"),
        Effect.DENY,
        Reason.NOT_GRANTED,
        None,
        baselines=[{"tools": {"require_approval": ["files.write"]}}],
    )


def test_limits_one_below_the_threshold_allow() -> None:
    _expect(
        {"tools": {"allow": ["files.read"]}, "limits": {"max_steps_per_task": 2}},
        Action.tool("files.read"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
        state=RuntimeState(steps_in_task=1),
    )
    _expect(
        {"models": {"allow": ["local/*"]}, "limits": {"tokens_per_day": 10}},
        Action.model("local/echo"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:models.allow[0]",
        state=RuntimeState(tokens_today=9),
    )
    _expect(
        {"tools": {"allow": ["zendesk.read"]}, "limits": {"calls_per_minute": {"zendesk.*": 2}}},
        Action.tool("zendesk.read"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
        state=RuntimeState(calls_last_minute={"zendesk.*": 1}),
    )


def test_support_triage_refund_amounts() -> None:
    policy = build_effective_policy(
        load_agent_policy(_EXAMPLES / "support-triage.yaml"),
        [load_baseline_policy(_EXAMPLES / "prod-baseline.yaml")],
    )
    small = evaluate(policy, Action.tool("zendesk.refund", {"amount": 50}))
    large = evaluate(policy, Action.tool("zendesk.refund", {"amount": 250}))
    assert small.effect is Effect.ALLOW
    assert small.reason is Reason.ALLOW_RULE
    assert small.rule == "agent:tools.require_approval[0]"
    assert small.policy_version == policy.version
    assert large.effect is Effect.REQUIRE_APPROVAL
    assert large.reason is Reason.APPROVAL_RULE
    assert large.rule == "agent:tools.require_approval[0]"
    assert large.policy_version == policy.version


def test_a_ran_input_guardrail_no_longer_blocks() -> None:
    _expect(
        {
            "models": {"allow": ["local/*"]},
            "guardrails": {"required": ["input.prompt_injection"], "on_missing": "block"},
        },
        Action.model("local/echo"),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:models.allow[0]",
        state=RuntimeState(guardrails_ran=frozenset({"input.prompt_injection"})),
    )


def test_overlapping_delegation_group_allows() -> None:
    _expect(
        {
            "tools": {"allow": ["files.read"]},
            "delegation": {"allow_on_behalf_of": True, "user_groups": ["support"]},
        },
        Action(ActionKind.TOOL, "files.read", {}, Principal("ada", frozenset({"support"}))),
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
    )
