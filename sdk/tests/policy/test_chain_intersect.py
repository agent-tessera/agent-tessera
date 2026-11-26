"""Caller-constraint intersection against peer allow sets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from agent_tessera.policy.effective import EffectivePolicy, build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.types import Action, ActionKind, Effect, Reason

_SHELL = Action.tool("shell.exec")
_TICKET = Action.tool("zendesk.read")


def _policy(agents: dict[str, object] | None = None) -> EffectivePolicy:
    document = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "worker", "environment": "prod"},
            "spec": {
                "tools": {"allow": ["shell.exec", "zendesk.read"]},
                "agents": agents or {"caller_constraint": "intersect"},
            },
        }
    )
    return build_effective_policy(document)


def _peers(
    *members: tuple[str, tuple[str, ...]],
) -> Mapping[str, Mapping[ActionKind, tuple[str, ...]]]:
    return {name: {ActionKind.TOOL: tools} for name, tools in members}


def _effect(
    policy: EffectivePolicy,
    action: Action,
    chain: tuple[str, ...] = ("orchestrator",),
) -> tuple[Effect, Reason, str | None]:
    decision = evaluate(policy, action, RuntimeState(chain=chain))
    return decision.effect, decision.reason, decision.rule


def test_low_orchestrator_cannot_use_a_high_worker() -> None:
    policy = replace(
        _policy(),
        peer_allows=_peers(("orchestrator", ("zendesk.read",))),
    )

    assert _effect(policy, _SHELL) == (
        Effect.DENY,
        Reason.NOT_GRANTED,
        "agent:agents.caller_constraint",
    )
    assert _effect(policy, _TICKET) == (
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[1]",
    )


def test_missing_peer_policy_is_unavailable() -> None:
    assert _effect(_policy(), _SHELL) == (
        Effect.DENY,
        Reason.CHAIN_POLICY_UNAVAILABLE,
        "agent:agents.caller_constraint",
    )


def test_own_constraint_ignores_missing_peers() -> None:
    policy = _policy({"caller_constraint": "own"})

    assert _effect(policy, _SHELL) == (
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
    )


def test_empty_chain_does_not_intersect() -> None:
    assert _effect(_policy(), _SHELL, ()) == (
        Effect.ALLOW,
        Reason.ALLOW_RULE,
        "agent:tools.allow[0]",
    )


def test_per_caller_list_uses_the_first_match() -> None:
    policy = _policy(
        {
            "caller_constraint": [
                {"callers": ["orchestrator"], "constraint": "own"},
                {"callers": ["*"], "constraint": "intersect"},
            ]
        }
    )

    assert _effect(policy, _SHELL, ("orchestrator",))[0] is Effect.ALLOW
    assert _effect(policy, _SHELL, ("billing",)) == (
        Effect.DENY,
        Reason.CHAIN_POLICY_UNAVAILABLE,
        "agent:agents.caller_constraint",
    )


def test_every_chain_member_must_grant() -> None:
    both = replace(
        _policy(),
        peer_allows=_peers(
            ("orchestrator", ("shell.exec",)),
            ("billing", ("shell.exec",)),
        ),
    )
    low = replace(
        _policy(),
        peer_allows=_peers(
            ("orchestrator", ("zendesk.read",)),
            ("billing", ("shell.exec",)),
        ),
    )
    chain = ("orchestrator", "billing")

    assert _effect(both, _SHELL, chain)[0] is Effect.ALLOW
    assert _effect(low, _SHELL, chain)[1] is Reason.NOT_GRANTED
    assert _effect(_policy(), _SHELL, chain)[1] is Reason.CHAIN_POLICY_UNAVAILABLE
