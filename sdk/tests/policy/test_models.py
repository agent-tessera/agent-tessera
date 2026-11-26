import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from agent_tessera import policy
from agent_tessera.policy import (
    AgentPolicy,
    AgentPolicyMetadata,
    BaselinePolicy,
    BaselinePolicyMetadata,
    BaselineResourceRules,
    Condition,
    Guardrails,
    Limits,
    ResourceRules,
    Selector,
    models,
)

SUPPORT_TRIAGE: dict[str, object] = {
    "apiVersion": "tessera/v1",
    "kind": "AgentPolicy",
    "metadata": {
        "agent": "support-triage",
        "environment": "prod",
        "owner": "team-support@acme.com",
        "risk_tier": "medium",
        "labels": {"domain": "support"},
    },
    "spec": {
        "models": {
            "allow": ["openai/gpt-4.1*", "anthropic/claude-*"],
            "deny": ["*/*-preview"],
        },
        "tools": {
            "allow": ["zendesk.read_ticket", "zendesk.add_comment"],
            "require_approval": [
                {
                    "name": "zendesk.refund",
                    "when": [{"arg": "amount", "op": "gt", "value": 100}],
                }
            ],
            "deny": ["shell.*"],
        },
        "data": {"allow": ["crm.customers:read"]},
        "agents": {
            "may_invoke": ["billing-agent"],
            "may_be_invoked_by": ["orchestrator"],
        },
        "delegation": {
            "allow_on_behalf_of": True,
            "user_groups": ["support-staff"],
        },
        "limits": {
            "cost_usd_per_day": 50,
            "tokens_per_day": 2_000_000,
            "calls_per_minute": {"zendesk.*": 30},
            "max_steps_per_task": 25,
        },
        "guardrails": {
            "required": ["input.prompt_injection", "output.pii_redaction"],
            "on_missing": "block",
        },
        "audit": {"capture_content": False},
    },
}

PROD_BASELINE: dict[str, object] = {
    "apiVersion": "tessera/v1",
    "kind": "BaselinePolicy",
    "metadata": {"name": "prod-baseline"},
    "spec": {
        "selector": {"environment": "prod"},
        "tools": {"deny": ["shell.*", "fs.delete*"]},
        "limits": {"cost_usd_per_day": 500},
        "guardrails": {
            "required": ["input.prompt_injection"],
            "on_missing": "block",
        },
        "require_owner": True,
    },
}


def _insert(
    payload: dict[str, object],
    path: tuple[str | int, ...],
    value: object,
) -> dict[str, object]:
    cloned = copy.deepcopy(payload)
    cursor: Any = cloned
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return cloned


def test_policy_package_reexports_public_names() -> None:
    for name in models.__all__:
        assert name in policy.__all__
        assert getattr(policy, name) is getattr(models, name)


def test_support_triage_example_validates() -> None:
    parsed = AgentPolicy.model_validate(SUPPORT_TRIAGE)

    assert parsed.api_version == "tessera/v1"
    assert parsed.kind == "AgentPolicy"
    assert parsed.metadata.agent == "support-triage"
    assert parsed.metadata.environment == "prod"
    assert parsed.metadata.owner == "team-support@acme.com"
    assert parsed.metadata.risk_tier == "medium"
    assert parsed.metadata.labels == {"domain": "support"}
    assert parsed.spec.models.allow == ("openai/gpt-4.1*", "anthropic/claude-*")
    assert parsed.spec.models.deny == ("*/*-preview",)
    assert parsed.spec.tools.allow == ("zendesk.read_ticket", "zendesk.add_comment")
    assert parsed.spec.tools.deny == ("shell.*",)
    rule = parsed.spec.tools.require_approval[0]
    assert rule.name == "zendesk.refund"
    assert len(rule.when) == 1
    assert rule.when[0].arg == "amount"
    assert rule.when[0].op == "gt"
    assert rule.when[0].value == 100
    assert parsed.spec.data.allow == ("crm.customers:read",)
    assert parsed.spec.agents.may_invoke == ("billing-agent",)
    assert parsed.spec.agents.may_be_invoked_by == ("orchestrator",)
    assert parsed.spec.delegation.allow_on_behalf_of is True
    assert parsed.spec.delegation.user_groups == ("support-staff",)
    assert parsed.spec.limits.cost_usd_per_day == 50
    assert isinstance(parsed.spec.limits.cost_usd_per_day, float)
    assert parsed.spec.limits.tokens_per_day == 2_000_000
    assert parsed.spec.limits.calls_per_minute == {"zendesk.*": 30}
    assert parsed.spec.limits.max_steps_per_task == 25
    assert parsed.spec.guardrails.required == (
        "input.prompt_injection",
        "output.pii_redaction",
    )
    assert parsed.spec.guardrails.on_missing == "block"
    assert parsed.spec.audit.capture_content is False


def test_prod_baseline_example_validates_and_selects_prod_agents() -> None:
    baseline = BaselinePolicy.model_validate(PROD_BASELINE)
    agent = AgentPolicy.model_validate(SUPPORT_TRIAGE)

    assert baseline.metadata.name == "prod-baseline"
    assert baseline.spec.require_owner is True
    assert baseline.spec.tools.deny == ("shell.*", "fs.delete*")
    assert baseline.spec.limits.cost_usd_per_day == 500
    assert baseline.spec.selector.matches(agent.metadata) is True
    dev_agent = AgentPolicyMetadata(agent="support-triage", environment="dev")
    assert baseline.spec.selector.matches(dev_agent) is False


def test_agent_policy_defaults() -> None:
    parsed = AgentPolicy(
        api_version="tessera/v1",
        kind="AgentPolicy",
        metadata=AgentPolicyMetadata(agent="bot"),
    )

    assert parsed.api_version == "tessera/v1"
    assert parsed.metadata.environment == "dev"
    assert parsed.metadata.owner is None
    assert parsed.metadata.risk_tier == "low"
    assert parsed.metadata.labels == {}
    assert parsed.spec.tools.allow == ()
    assert parsed.spec.delegation.allow_on_behalf_of is False
    assert parsed.spec.limits.tokens_per_day is None
    assert parsed.spec.guardrails.on_missing == "flag"
    assert parsed.spec.audit.capture_content is False


def test_api_version_accepts_name_and_alias() -> None:
    metadata = {"agent": "bot"}
    by_alias = AgentPolicy.model_validate(
        {"apiVersion": "tessera/v1", "kind": "AgentPolicy", "metadata": metadata}
    )
    by_name = AgentPolicy.model_validate(
        {"api_version": "tessera/v1", "kind": "AgentPolicy", "metadata": metadata}
    )
    baseline = BaselinePolicy.model_validate(
        {"api_version": "tessera/v1", "kind": "BaselinePolicy", "metadata": {"name": "prod"}}
    )

    assert by_alias.api_version == "tessera/v1"
    assert by_name.api_version == "tessera/v1"
    assert baseline.api_version == "tessera/v1"


def test_api_version_is_required() -> None:
    with pytest.raises(ValidationError):
        AgentPolicy.model_validate({"kind": "AgentPolicy", "metadata": {"agent": "bot"}})


def test_wrong_api_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v2",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
            }
        )


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        (SUPPORT_TRIAGE, ("extra",)),
        (SUPPORT_TRIAGE, ("metadata", "extra")),
        (SUPPORT_TRIAGE, ("spec", "extra")),
        (SUPPORT_TRIAGE, ("spec", "models", "extra")),
        (SUPPORT_TRIAGE, ("spec", "tools", "extra")),
        (SUPPORT_TRIAGE, ("spec", "tools", "require_approval", 0, "extra")),
        (SUPPORT_TRIAGE, ("spec", "tools", "require_approval", 0, "when", 0, "extra")),
        (SUPPORT_TRIAGE, ("spec", "data", "extra")),
        (SUPPORT_TRIAGE, ("spec", "agents", "extra")),
        (SUPPORT_TRIAGE, ("spec", "delegation", "extra")),
        (SUPPORT_TRIAGE, ("spec", "limits", "extra")),
        (SUPPORT_TRIAGE, ("spec", "guardrails", "extra")),
        (SUPPORT_TRIAGE, ("spec", "audit", "extra")),
        (PROD_BASELINE, ("extra",)),
        (PROD_BASELINE, ("metadata", "extra")),
        (PROD_BASELINE, ("spec", "extra")),
        (PROD_BASELINE, ("spec", "selector", "extra")),
        (PROD_BASELINE, ("spec", "tools", "extra")),
        (PROD_BASELINE, ("spec", "limits", "extra")),
        (PROD_BASELINE, ("spec", "guardrails", "extra")),
    ],
)
def test_unknown_field_is_rejected(payload: dict[str, object], path: tuple[str | int, ...]) -> None:
    document = _insert(payload, path, "nope")
    model = AgentPolicy if payload["kind"] == "AgentPolicy" else BaselinePolicy

    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(document)

    assert any(err["type"] == "extra_forbidden" for err in exc_info.value.errors())


def test_baseline_with_tools_allow_is_rejected() -> None:
    payload = _insert(PROD_BASELINE, ("spec", "tools", "allow"), ["shell.exec"])

    with pytest.raises(ValidationError) as exc_info:
        BaselinePolicy.model_validate(payload)

    assert any(
        err["type"] == "extra_forbidden" and "allow" in err["loc"]
        for err in exc_info.value.errors()
    )


@pytest.mark.parametrize("model", [ResourceRules, BaselineResourceRules])
def test_require_approval_string_is_coerced(
    model: type[ResourceRules] | type[BaselineResourceRules],
) -> None:
    parsed = model.model_validate(
        {
            "require_approval": [
                "files.delete",
                {
                    "name": "files.write",
                    "when": [{"arg": "path", "op": "eq", "value": "notes.txt"}],
                },
            ]
        }
    )

    assert parsed.require_approval[0].name == "files.delete"
    assert parsed.require_approval[0].when == ()
    assert parsed.require_approval[1].name == "files.write"
    assert parsed.require_approval[1].when[0].value == "notes.txt"


def test_require_approval_dict_rules_stay_unchanged() -> None:
    parsed = ResourceRules.model_validate({"require_approval": [{"name": "files.delete"}]})

    assert parsed.require_approval[0].name == "files.delete"


def test_require_approval_must_be_a_list() -> None:
    with pytest.raises(ValidationError):
        ResourceRules.model_validate({"require_approval": "files.delete"})


def test_non_dict_input_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResourceRules.model_validate("files.delete")


@pytest.mark.parametrize(
    "name",
    ["A", "a-", "-a", "a_b", "a.b", "", " ", "a" * 64, "has space"],
)
@pytest.mark.parametrize(
    "build",
    [
        lambda name: AgentPolicyMetadata(agent=name),
        lambda name: AgentPolicyMetadata(agent="ok", environment=name),
        lambda name: BaselinePolicyMetadata(name=name),
    ],
)
def test_bad_slug_is_rejected(name: str, build: Callable[[str], object]) -> None:
    with pytest.raises(ValidationError):
        build(name)


@pytest.mark.parametrize("name", ["a", "9", "ab", "a-b", "a--b", "a0", "support-triage", "a" * 63])
def test_slug_accepts_valid_names(name: str) -> None:
    assert AgentPolicyMetadata(agent=name).agent == name
    assert AgentPolicyMetadata(agent="ok", environment=name).environment == name
    assert BaselinePolicyMetadata(name=name).name == name


@pytest.mark.parametrize("op", ["in", "not_in"])
def test_membership_condition_stores_a_tuple(op: str) -> None:
    condition = Condition(arg="status", op=op, value=["open", "closed"])

    assert condition.value == ("open", "closed")


@pytest.mark.parametrize("op", ["in", "not_in"])
@pytest.mark.parametrize("value", ["open", ("open", "closed"), 1, {"open"}])
def test_membership_condition_requires_a_list(op: str, value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Condition(arg="status", op=op, value=value)

    assert any("list" in err["msg"] for err in exc_info.value.errors())


@pytest.mark.parametrize("op", ["gt", "gte", "lt", "lte"])
@pytest.mark.parametrize("value", [0, -3, 1.5])
def test_ordering_condition_accepts_numbers(op: str, value: int | float) -> None:
    assert Condition(arg="amount", op=op, value=value).value == value


@pytest.mark.parametrize("op", ["gt", "gte", "lt", "lte"])
@pytest.mark.parametrize("value", [True, False, "100", None, [1]])
def test_ordering_condition_rejects_non_numbers(op: str, value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Condition(arg="amount", op=op, value=value)

    assert any("int or float" in err["msg"] for err in exc_info.value.errors())


@pytest.mark.parametrize("value", ["refund-\\d+", ".*", ""])
def test_matches_condition_accepts_a_regex(value: str) -> None:
    assert Condition(arg="name", op="matches", value=value).value == value


@pytest.mark.parametrize("value", ["[", "*", "("])
def test_matches_condition_rejects_an_invalid_regex(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Condition(arg="name", op="matches", value=value)

    assert any("regular expression" in err["msg"] for err in exc_info.value.errors())


@pytest.mark.parametrize("value", [1, None, ["a"]])
def test_matches_condition_requires_a_string(value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Condition(arg="name", op="matches", value=value)

    assert any("string" in err["msg"] for err in exc_info.value.errors())


def test_equality_condition_keeps_arbitrary_values() -> None:
    assert Condition(arg="flag", op="eq", value=True).value is True
    assert Condition(arg="name", op="ne", value=["a"]).value == ["a"]


def test_condition_rejects_an_unknown_operator() -> None:
    with pytest.raises(ValidationError):
        Condition(arg="amount", op="contains", value=1)


def test_condition_rejects_an_empty_argument_name() -> None:
    with pytest.raises(ValidationError):
        Condition(arg="", op="eq", value=1)


@pytest.mark.parametrize("name", ["input.prompt_injection", "a.b", "a.b.c", "output.pii_redaction"])
def test_guardrail_names_accept_dotted_identifiers(name: str) -> None:
    assert Guardrails(required=(name,)).required == (name,)


@pytest.mark.parametrize(
    "name",
    ["prompt_injection", "Input.x", "input.", ".input", "input.bad-name"],
)
def test_guardrail_names_reject_invalid_identifiers(name: str) -> None:
    with pytest.raises(ValidationError):
        Guardrails(required=(name,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cost_usd_per_day", 0),
        ("cost_usd_per_day", -1),
        ("tokens_per_day", 0),
        ("max_steps_per_task", 0),
    ],
)
def test_limits_reject_non_positive_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Limits(**{field: value})


def test_calls_per_minute_rejects_a_non_positive_cap() -> None:
    with pytest.raises(ValidationError):
        Limits(calls_per_minute={"zendesk.*": 0})


def test_models_are_frozen() -> None:
    parsed = AgentPolicy.model_validate(SUPPORT_TRIAGE)

    with pytest.raises(ValidationError):
        parsed.kind = "AgentPolicy"  # type: ignore[misc]


PROD_AGENT = AgentPolicyMetadata(
    agent="support-triage",
    environment="prod",
    risk_tier="medium",
    labels={"domain": "support", "team": "triage"},
)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (Selector(), True),
        (Selector(environment=None), True),
        (Selector(environment="prod"), True),
        (Selector(environment="dev"), False),
        (Selector(risk_tier=()), True),
        (Selector(risk_tier=("medium",)), True),
        (Selector(risk_tier=("low", "medium")), True),
        (Selector(risk_tier=("low", "high")), False),
        (Selector(labels={}), True),
        (Selector(labels={"domain": "support"}), True),
        (Selector(labels={"domain": "billing"}), False),
        (Selector(labels={"missing": "x"}), False),
        (
            Selector(environment="prod", risk_tier=("medium",), labels={"domain": "support"}),
            True,
        ),
        (
            Selector(environment="prod", risk_tier=("low",), labels={"domain": "support"}),
            False,
        ),
        (
            Selector(environment="dev", risk_tier=("medium",), labels={"domain": "support"}),
            False,
        ),
        (
            Selector(environment="prod", risk_tier=("medium",), labels={"domain": "nope"}),
            False,
        ),
    ],
)
def test_selector_matches(selector: Selector, expected: bool) -> None:
    assert selector.matches(PROD_AGENT) is expected


def test_empty_selector_matches_every_agent() -> None:
    other = AgentPolicyMetadata(agent="other", environment="dev", risk_tier="high")

    assert Selector().matches(other) is True
    assert Selector().matches(PROD_AGENT) is True
