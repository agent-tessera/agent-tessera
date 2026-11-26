"""Supervisor grant documents and the cap_to_supervisor intersection."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.models import AgentPolicy, SupervisorGrants
from agent_tessera.types import Action, ActionKind, Effect, Reason

_ROOT = Path(__file__).resolve().parents[3]


def test_example_grant_document_validates() -> None:
    path = _ROOT / "policies" / "examples" / "supervisor-grants.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    grants = SupervisorGrants.model_validate(document)
    assert grants.metadata.supervisor == "user:ada"
    assert grants.spec.tools.allow == ("files.read",)
    assert grants.spec.models.allow == ("openai/gpt-4o",)


def test_unknown_grant_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SupervisorGrants.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "SupervisorGrants",
                "metadata": {"supervisor": "user:ada"},
                "spec": {"tools": {"allow": ["files.read"], "deny": ["shell.exec"]}},
            }
        )


def test_capped_tool_is_denied_when_the_grant_lacks_it() -> None:
    policy = _capped()
    state = RuntimeState(supervisor="user:ada")
    allowed = evaluate(policy, Action.tool("files.read"), state)
    denied = evaluate(policy, Action.tool("shell.exec"), state)
    assert allowed.effect is Effect.ALLOW
    assert denied.effect is Effect.DENY
    assert denied.reason is Reason.NOT_GRANTED
    assert denied.rule == "agent:supervision.cap_to_supervisor"


def test_missing_grants_deny_the_capped_call() -> None:
    policy = build_effective_policy(_agent(cap=True))
    missing = evaluate(policy, Action.tool("files.read"), RuntimeState(supervisor="user:ada"))
    unnamed = evaluate(policy, Action.tool("files.read"), RuntimeState())
    assert missing.reason is Reason.SUPERVISOR_GRANTS_MISSING
    assert unnamed.reason is Reason.SUPERVISOR_GRANTS_MISSING


def test_required_supervisor_denies_until_one_is_resolved() -> None:
    policy = build_effective_policy(_agent(cap=False, required=True))
    denied = evaluate(policy, Action.tool("files.read"), RuntimeState())
    allowed = evaluate(policy, Action.tool("files.read"), RuntimeState(supervisor="user:ada"))
    overdue = evaluate(
        replace(policy, max_unacknowledged_seconds=60),
        Action.tool("files.read"),
        RuntimeState(supervisor="user:ada", dead_man=True),
    )
    assert denied.reason is Reason.UNSUPERVISED
    assert allowed.effect is Effect.ALLOW
    assert overdue.reason is Reason.DEAD_MAN


def test_cap_does_not_apply_when_it_is_off_or_the_kind_is_data() -> None:
    open_policy = build_effective_policy(_agent(cap=False))
    decision = evaluate(open_policy, Action.tool("shell.exec"))
    assert decision.effect is Effect.ALLOW
    capped = _capped()
    data = evaluate(capped, Action.data("files.read"), RuntimeState())
    assert data.effect is Effect.ALLOW


def _capped():
    policy = build_effective_policy(_agent(cap=True))
    grants = {
        ActionKind.MODEL: ("openai/gpt-4o",),
        ActionKind.TOOL: ("files.read",),
        ActionKind.DATA: (),
        ActionKind.AGENT: (),
    }
    return replace(policy, supervisor_grants={"user:ada": grants})


def _agent(*, cap: bool, required: bool = False) -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot"},
            "spec": {
                "tools": {"allow": ["files.read", "shell.exec"]},
                "data": {"allow": ["files.read"]},
                "supervision": {"cap_to_supervisor": cap, "required": required},
            },
        }
    )
