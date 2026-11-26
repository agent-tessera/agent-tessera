"""Chain depth, re-entry, and principal decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_tessera import Tessera
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.types import Action, ActionKind, Effect, Principal, Reason


def _policy(agent: str = "worker", **spec: object) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": agent, "environment": "prod"},
            "spec": spec,
        }
    )


def _decide(
    spec: dict[str, object],
    action: Action,
    state: RuntimeState | None = None,
) -> tuple[Effect, Reason, str | None]:
    policy = build_effective_policy(_policy(**spec))
    decision = evaluate(policy, action, state)
    return decision.effect, decision.reason, decision.rule


_READ = Action.tool("files.read")
_ADA = Action(
    kind=ActionKind.TOOL,
    resource="files.read",
    principal=Principal("ada"),
)
_ALLOW = {"tools": {"allow": ["files.read"]}}


@pytest.mark.parametrize(
    "case",
    [
        (
            {**_ALLOW, "agents": {"max_chain_depth": 1}},
            _READ,
            RuntimeState(chain=("orchestrator",)),
            Effect.DENY,
            Reason.CHAIN_TOO_DEEP,
            "agent:agents.max_chain_depth",
        ),
        (
            {**_ALLOW, "agents": {"max_chain_depth": 2}},
            _READ,
            RuntimeState(chain=("orchestrator",)),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
        ),
        (
            {**_ALLOW, "agents": {"max_chain_depth": 1}},
            _READ,
            RuntimeState(),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
        ),
        (
            _ALLOW,
            _READ,
            RuntimeState(chain=("worker",)),
            Effect.DENY,
            Reason.CHAIN_REENTRY,
            "agent:agents.allow_reentry",
        ),
        (
            {**_ALLOW, "agents": {"allow_reentry": True}},
            _READ,
            RuntimeState(chain=("worker",)),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
        ),
        (
            {
                **_ALLOW,
                "agents": {"max_chain_depth": 1},
            },
            _READ,
            RuntimeState(chain=("worker",)),
            Effect.DENY,
            Reason.CHAIN_TOO_DEEP,
            "agent:agents.max_chain_depth",
        ),
        (
            {**_ALLOW, "delegation": {"principal": "required"}},
            _READ,
            RuntimeState(chain=("orchestrator",)),
            Effect.DENY,
            Reason.PRINCIPAL_REQUIRED,
            "agent:delegation.principal",
        ),
        (
            {**_ALLOW, "delegation": {"principal": "required"}},
            _READ,
            RuntimeState(),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
        ),
        (
            {
                **_ALLOW,
                "delegation": {"principal": "required", "allow_on_behalf_of": True},
            },
            _ADA,
            RuntimeState(chain=("orchestrator",)),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
        ),
        (
            {**_ALLOW, "delegation": {"principal": "forbidden", "allow_on_behalf_of": True}},
            _ADA,
            RuntimeState(),
            Effect.DENY,
            Reason.PRINCIPAL_FORBIDDEN,
            "agent:delegation.principal",
        ),
        (
            {**_ALLOW, "delegation": {"principal": "forbidden"}},
            _READ,
            RuntimeState(chain=("orchestrator",)),
            Effect.ALLOW,
            Reason.ALLOW_RULE,
            "agent:tools.allow[0]",
        ),
        (
            {
                "tools": {"allow": ["files.read"], "deny": ["files.read"]},
                "agents": {"max_chain_depth": 1},
            },
            _READ,
            RuntimeState(chain=("orchestrator",)),
            Effect.DENY,
            Reason.DENY_RULE,
            "agent:tools.deny[0]",
        ),
    ],
)
def test_chain_rules(
    case: tuple[dict[str, object], Action, RuntimeState, Effect, Reason, str],
) -> None:
    spec, action, state, effect, reason, rule = case
    assert _decide(spec, action, state) == (effect, reason, rule)


def test_handoff_records_the_caller_on_the_chain(tmp_path: Path) -> None:
    caller = Tessera(
        "orchestrator",
        policy=_policy("orchestrator", **_ALLOW),
        audit_path=tmp_path / "a.jsonl",
    )
    worker = Tessera(
        "worker",
        policy=_policy(**{**_ALLOW, "agents": {"max_chain_depth": 1}}),
        audit_path=tmp_path / "b.jsonl",
    )
    try:
        with caller.task("run-1"), worker.receive_handoff(caller):
            decision = worker.check(Action.tool("files.read"))
        assert decision.effect is Effect.DENY
        assert decision.reason is Reason.CHAIN_TOO_DEEP
    finally:
        caller.close()
        worker.close()
