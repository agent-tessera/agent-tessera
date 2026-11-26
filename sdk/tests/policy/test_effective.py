"""Tests for merging an agent policy with baselines."""

import pytest

from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import (
    ApprovalPatternRule,
    LimitValue,
    PatternRule,
    RequiredGuardrail,
    build_effective_policy,
)
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.types import ActionKind


def _agent(**spec: object) -> AgentPolicy:
    body: dict[str, object] = {
        "apiVersion": "tessera/v1",
        "kind": "AgentPolicy",
        "metadata": {
            "agent": "support-triage",
            "environment": "prod",
            "owner": "team-support",
            "risk_tier": "high",
        },
        "spec": spec,
    }
    return AgentPolicy.model_validate(body)


def _baseline(
    name: str,
    *,
    environment: str = "prod",
    spec: dict[str, object] | None = None,
) -> BaselinePolicy:
    body: dict[str, object] = {
        "apiVersion": "tessera/v1",
        "kind": "BaselinePolicy",
        "metadata": {"name": name},
        "spec": {"selector": {"environment": environment}, **(spec or {})},
    }
    return BaselinePolicy.model_validate(body)


def test_rule_strings_and_grants_match_the_contract() -> None:
    agent = _agent(
        models={"allow": ["openai/*"], "deny": ["*preview*"]},
        tools={
            "allow": ["files.read"],
            "deny": ["shell.*"],
            "require_approval": [
                {
                    "name": "files.delete",
                    "when": [{"arg": "force", "op": "eq", "value": True}],
                }
            ],
        },
        data={"allow": ["crm.customers:read"]},
        agents={"may_invoke": ["billing"], "may_be_invoked_by": ["orchestrator"]},
        delegation={"allow_on_behalf_of": True, "user_groups": ["support"]},
        limits={
            "max_steps_per_task": 10,
            "tokens_per_day": 1000,
            "cost_usd_per_day": 2.5,
            "calls_per_minute": {"files.*": 5, "zendesk.*": 30},
        },
        guardrails={
            "required": ["input.prompt_injection", "output.pii"],
            "on_missing": "flag",
        },
        audit={"capture_content": True},
    )
    alpha = _baseline(
        "alpha",
        spec={
            "tools": {"deny": ["fs.delete*"], "require_approval": ["files.write"]},
            "models": {"deny": ["openai/gpt-old"]},
            "limits": {
                "max_steps_per_task": 10,
                "calls_per_minute": {"zendesk.*": 10, "files.*": 5},
            },
            "guardrails": {"required": ["input.prompt_injection"], "on_missing": "block"},
        },
    )
    zeta = _baseline(
        "zeta",
        spec={"tools": {"deny": ["net.*"]}, "data": {"deny": ["secrets.*"]}},
    )
    ignored = _baseline("staging-only", environment="staging", spec={"tools": {"deny": ["rm"]}})

    first = build_effective_policy(agent, [zeta, ignored, alpha])
    second = build_effective_policy(agent, [alpha, zeta, ignored])

    assert first.version == second.version
    assert first.version.startswith("sha256:")
    assert len(first.version) == len("sha256:") + 64
    assert first.applied_baselines == ("alpha", "zeta")
    assert first.agent == "support-triage"
    assert first.environment == "prod"
    assert first.owner == "team-support"
    assert first.capture_content is True
    assert first.delegation.allow_on_behalf_of is True
    assert first.delegation.user_groups == ("support",)

    assert first.allow[ActionKind.TOOL] == (PatternRule("files.read", "agent:tools.allow[0]"),)
    assert first.allow[ActionKind.MODEL] == (PatternRule("openai/*", "agent:models.allow[0]"),)
    assert first.allow[ActionKind.DATA] == (
        PatternRule("crm.customers:read", "agent:data.allow[0]"),
    )
    assert first.allow[ActionKind.AGENT] == (PatternRule("billing", "agent:agents.may_invoke[0]"),)
    assert first.may_be_invoked_by == (
        PatternRule("orchestrator", "agent:agents.may_be_invoked_by[0]"),
    )
    assert first.deny[ActionKind.TOOL] == (
        PatternRule("shell.*", "agent:tools.deny[0]"),
        PatternRule("fs.delete*", "baseline/alpha:tools.deny[0]"),
        PatternRule("net.*", "baseline/zeta:tools.deny[0]"),
    )
    assert first.deny[ActionKind.MODEL] == (
        PatternRule("*preview*", "agent:models.deny[0]"),
        PatternRule("openai/gpt-old", "baseline/alpha:models.deny[0]"),
    )
    assert first.deny[ActionKind.DATA] == (PatternRule("secrets.*", "baseline/zeta:data.deny[0]"),)
    assert first.deny[ActionKind.AGENT] == ()
    assert first.approvals[ActionKind.AGENT] == ()
    agent_approval, baseline_approval = first.approvals[ActionKind.TOOL]
    assert agent_approval == ApprovalPatternRule(
        "files.delete",
        agent_approval.when,
        True,
        "agent:tools.require_approval[0]",
    )
    assert agent_approval.when[0].arg == "force"
    assert baseline_approval == ApprovalPatternRule(
        "files.write",
        (),
        False,
        "baseline/alpha:tools.require_approval[0]",
    )
    assert first.approvals[ActionKind.TOOL][0].grants is True
    assert first.approvals[ActionKind.TOOL][1].grants is False
    assert first.max_steps_per_task == LimitValue(10.0, "agent:limits.max_steps_per_task")
    assert first.tokens_per_day == LimitValue(1000.0, "agent:limits.tokens_per_day")
    assert first.cost_usd_per_day == LimitValue(2.5, "agent:limits.cost_usd_per_day")
    assert first.calls_per_minute == (
        ("files.*", LimitValue(5.0, "agent:limits.calls_per_minute[files.*]")),
        ("zendesk.*", LimitValue(10.0, "baseline/alpha:limits.calls_per_minute[zendesk.*]")),
    )
    assert first.required_guardrails == (
        RequiredGuardrail(
            "input.prompt_injection",
            "block",
            "baseline/alpha:guardrails.required[0]",
        ),
        RequiredGuardrail("output.pii", "flag", "agent:guardrails.required[1]"),
    )


def test_version_ignores_non_applicable_baselines_and_changes_with_applied_ones() -> None:
    agent = _agent(tools={"allow": ["files.read"]})
    applied = _baseline("prod", spec={"tools": {"deny": ["shell.*"]}})
    other = _baseline("dev", environment="dev", spec={"tools": {"deny": ["shell.*"]}})
    alone = build_effective_policy(agent, [applied])
    with_ignored = build_effective_policy(agent, [applied, other])
    assert alone.version == with_ignored.version

    changed = build_effective_policy(
        _agent(tools={"allow": ["files.write"]}),
        [applied],
    )
    assert changed.version != alone.version
    tighter = build_effective_policy(agent, [_baseline("prod", spec={"tools": {"deny": ["rm"]}})])
    assert tighter.version != alone.version


def test_unset_limits_stay_absent_and_a_tighter_baseline_wins() -> None:
    agent = _agent(limits={"max_steps_per_task": 25, "calls_per_minute": {"zendesk.*": 30}})
    baseline = _baseline("prod", spec={"limits": {"max_steps_per_task": 8}})
    policy = build_effective_policy(agent, [baseline])
    assert policy.max_steps_per_task == LimitValue(8.0, "baseline/prod:limits.max_steps_per_task")
    assert policy.tokens_per_day is None
    assert policy.cost_usd_per_day is None
    assert policy.calls_per_minute == (
        ("zendesk.*", LimitValue(30.0, "agent:limits.calls_per_minute[zendesk.*]")),
    )
    assert policy.required_guardrails == ()
    assert policy.capture_content is False


def test_equal_guardrail_strictness_keeps_the_agent_rule() -> None:
    agent = _agent(guardrails={"required": ["input.pii"], "on_missing": "warn"})
    baseline = _baseline(
        "prod",
        spec={"guardrails": {"required": ["input.pii"], "on_missing": "warn"}},
    )
    policy = build_effective_policy(agent, [baseline])
    assert policy.required_guardrails == (
        RequiredGuardrail("input.pii", "warn", "agent:guardrails.required[0]"),
    )


def test_require_owner_rejects_an_unowned_agent() -> None:
    agent = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod"},
        }
    )
    baseline = _baseline("prod", spec={"require_owner": True})
    with pytest.raises(PolicyError, match="owner required by baseline prod"):
        build_effective_policy(agent, [baseline])


def test_duplicate_baseline_names_are_rejected() -> None:
    first = _baseline("prod")
    second = _baseline("prod", environment="dev")
    agent = _agent()
    with pytest.raises(PolicyError, match="duplicate baseline name prod"):
        build_effective_policy(agent, [first, second])
