"""Chain and delegation fields on an agent policy."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from agent_tessera.errors import PolicyError
from agent_tessera.policy.loader import load_policy, parse_policy
from agent_tessera.policy.models import AgentPolicy, CallerConstraintRule

EXAMPLES = Path(__file__).resolve().parents[3] / "policies" / "examples"

_AGENT: dict[str, object] = {
    "apiVersion": "tessera/v1",
    "kind": "AgentPolicy",
    "metadata": {"agent": "chain-worker"},
}


def _with_spec(spec: dict[str, object]) -> dict[str, object]:
    document = copy.deepcopy(_AGENT)
    document["spec"] = spec
    return document


def test_chain_fields_default_to_own_policy() -> None:
    policy = parse_policy(_AGENT)

    assert isinstance(policy, AgentPolicy)
    assert policy.spec.agents.max_chain_depth is None
    assert policy.spec.agents.allow_reentry is False
    assert policy.spec.agents.caller_constraint == "own"
    assert policy.spec.delegation.principal == "optional"


def test_chain_worker_example_loads() -> None:
    policy = load_policy(EXAMPLES / "chain-worker.yaml")

    assert isinstance(policy, AgentPolicy)
    assert policy.spec.agents.max_chain_depth == 4
    assert policy.spec.agents.allow_reentry is False
    rules = policy.spec.agents.caller_constraint
    assert rules == (
        CallerConstraintRule(callers=("orchestrator",), constraint="intersect"),
        CallerConstraintRule(callers=("billing-*",), constraint="own"),
    )
    assert policy.spec.delegation.principal == "required"


@pytest.mark.parametrize("constraint", ["own", "intersect"])
def test_scalar_caller_constraint_is_accepted(constraint: str) -> None:
    policy = parse_policy(_with_spec({"agents": {"caller_constraint": constraint}}))

    assert isinstance(policy, AgentPolicy)
    assert policy.spec.agents.caller_constraint == constraint


@pytest.mark.parametrize("principal", ["required", "optional", "forbidden"])
def test_principal_mode_is_accepted(principal: str) -> None:
    policy = parse_policy(_with_spec({"delegation": {"principal": principal}}))

    assert isinstance(policy, AgentPolicy)
    assert policy.spec.delegation.principal == principal


@pytest.mark.parametrize(
    "spec",
    [
        {"agents": {"caller_constraint": "neither"}},
        {"agents": {"caller_constraint": [{"callers": ["orchestrator"], "constraint": "all"}]}},
        {"agents": {"caller_constraint": [{"callers": [], "constraint": "own"}]}},
        {"agents": {"caller_constraint": {"callers": ["orchestrator"], "constraint": "own"}}},
        {"agents": {"max_chain_depth": 0}},
        {"delegation": {"principal": "maybe"}},
    ],
)
def test_invalid_chain_field_is_a_policy_error(spec: dict[str, object]) -> None:
    with pytest.raises(PolicyError):
        parse_policy(_with_spec(spec))
