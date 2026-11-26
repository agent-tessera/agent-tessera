from collections.abc import Callable

import pytest

import agent_tessera
from agent_tessera import (
    Action,
    ActionKind,
    Decision,
    Effect,
    Principal,
    Reason,
)


def test_package_exports_errors_and_core_types() -> None:
    assert agent_tessera.__all__ == [
        "Action",
        "ActionKind",
        "AgentPolicy",
        "AgentRegistry",
        "ApprovalRequired",
        "BaselinePolicy",
        "ConfigError",
        "Decision",
        "Denied",
        "Effect",
        "GovernanceError",
        "LocalRegistry",
        "PolicyError",
        "Principal",
        "Reason",
        "RegistryError",
        "ServerError",
        "Task",
        "Tessera",
        "TesseraError",
        "__version__",
        "load_policy",
    ]


def test_action_kind_values() -> None:
    assert [kind.value for kind in ActionKind] == ["model", "tool", "data", "agent"]


def test_effect_values() -> None:
    assert [effect.value for effect in Effect] == ["allow", "deny", "require_approval"]


def test_reason_values() -> None:
    assert [reason.value for reason in Reason] == [
        "suspended",
        "deny_rule",
        "delegation",
        "guardrail_missing",
        "not_granted",
        "limit",
        "approval_rule",
        "allow_rule",
        "approval_granted",
        "approval_rejected",
        "run_revoked",
        "policy_unavailable",
        "fail_open",
        "outside_window",
        "effect_denied",
        "assistive_effect",
        "effect_approval",
        "approval_unavailable",
        "approval_timeout",
        "quota_unavailable",
        "circuit_open",
        "chain_too_deep",
        "chain_reentry",
        "principal_required",
        "principal_forbidden",
        "chain_policy_unavailable",
        "supervisor_grants_missing",
        "unsupervised",
        "dead_man",
    ]


def test_principal_defaults_to_no_groups() -> None:
    principal = Principal("ada")

    assert principal.user == "ada"
    assert principal.groups == frozenset()


def test_principal_keeps_groups() -> None:
    principal = Principal("ada", frozenset({"support", "oncall"}))

    assert principal.groups == frozenset({"support", "oncall"})


@pytest.mark.parametrize(
    ("build", "resource", "kind"),
    [
        (Action.model, "openai/gpt-4.1", ActionKind.MODEL),
        (Action.data, "crm.customers:read", ActionKind.DATA),
        (Action.agent, "billing", ActionKind.AGENT),
    ],
)
def test_action_constructors_set_kind_and_resource(
    build: Callable[[str], Action],
    resource: str,
    kind: ActionKind,
) -> None:
    action = build(resource)

    assert action.kind is kind
    assert action.resource == resource
    assert action.args == {}
    assert action.principal is None


def test_action_tool_without_args_is_empty() -> None:
    action = Action.tool("x")

    assert action.kind is ActionKind.TOOL
    assert action.resource == "x"
    assert action.args == {}
    assert action.principal is None


def test_action_tool_none_args_is_empty() -> None:
    assert Action.tool("x", None).args == {}


def test_action_tool_stores_a_copy_of_args() -> None:
    supplied = {"amount": 50}
    action = Action.tool("zendesk.refund", supplied)
    supplied["amount"] = 250

    assert action.args == {"amount": 50}


def test_action_default_args_are_independent() -> None:
    first = Action.model("openai/gpt-4.1")
    second = Action.model("openai/gpt-4.1")

    assert first.args == {}
    assert first.args is not second.args


def test_action_keeps_principal() -> None:
    principal = Principal("ada")
    action = Action(ActionKind.TOOL, "files.read", principal=principal)

    assert action.principal is principal


def test_action_equality_is_identity() -> None:
    assert Action.tool("x") != Action.tool("x")


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (Effect.ALLOW, True),
        (Effect.DENY, False),
        (Effect.REQUIRE_APPROVAL, False),
    ],
)
def test_decision_allowed_matches_allow_effect(effect: Effect, expected: bool) -> None:
    decision = Decision(effect, Reason.ALLOW_RULE, Action.tool("files.read"), "v1")

    assert decision.allowed is expected
    assert decision.policy_version == "v1"
    assert decision.rule is None


def test_decision_keeps_rule() -> None:
    action = Action.tool("files.delete")
    decision = Decision(
        Effect.REQUIRE_APPROVAL,
        Reason.APPROVAL_RULE,
        action,
        "sha256:abc",
        "agent:tools.require_approval[0]",
    )

    assert decision.action is action
    assert decision.reason is Reason.APPROVAL_RULE
    assert decision.rule == "agent:tools.require_approval[0]"
    assert decision.allowed is False


def test_decision_equality_is_identity() -> None:
    action = Action.tool("x")
    first = Decision(Effect.ALLOW, Reason.ALLOW_RULE, action, "v1")
    second = Decision(Effect.ALLOW, Reason.ALLOW_RULE, action, "v1")

    assert first != second
