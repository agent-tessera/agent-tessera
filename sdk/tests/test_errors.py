from pathlib import Path

import pytest

from agent_tessera import (
    Action,
    ApprovalRequired,
    ConfigError,
    Decision,
    Denied,
    Effect,
    GovernanceError,
    PolicyError,
    Reason,
    ServerError,
    TesseraError,
)


def test_server_error_is_a_tessera_error() -> None:
    err = ServerError("control plane unreachable")

    assert isinstance(err, TesseraError)
    assert str(err) == "control plane unreachable"


def test_config_error_is_a_tessera_error() -> None:
    err = ConfigError("missing policy path")

    assert isinstance(err, TesseraError)
    assert str(err) == "missing policy path"


def test_policy_error_without_path_uses_message() -> None:
    err = PolicyError("invalid document")

    assert isinstance(err, TesseraError)
    assert err.path is None
    assert str(err) == "invalid document"


def test_policy_error_with_path_prefixes_message() -> None:
    path = Path("policies/support.yaml")
    message = "spec.tools.allow.0: Input should be a valid string"
    err = PolicyError(message, path=path)

    assert err.path == path
    assert str(err) == f"{path}: {message}"


def test_policy_error_path_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        PolicyError("bad", Path("policy.yaml"))


def _decision(
    effect: Effect,
    reason: Reason,
    *,
    rule: str | None = None,
    resource: str = "shell.exec",
) -> Decision:
    return Decision(effect, reason, Action.tool(resource), "v1", rule)


def test_denied_formats_decision_without_rule() -> None:
    decision = _decision(Effect.DENY, Reason.NOT_GRANTED)
    err = Denied(decision)

    assert isinstance(err, GovernanceError)
    assert isinstance(err, TesseraError)
    assert err.decision is decision
    assert str(err) == "deny tool 'shell.exec': not_granted"


def test_approval_required_formats_decision_with_rule() -> None:
    decision = _decision(
        Effect.REQUIRE_APPROVAL,
        Reason.APPROVAL_RULE,
        rule="agent:tools.require_approval[0]",
        resource="files.delete",
    )
    err = ApprovalRequired(decision)

    assert isinstance(err, GovernanceError)
    assert err.decision is decision
    assert err.approval_id is None
    assert str(err) == (
        "require_approval tool 'files.delete': approval_rule (rule agent:tools.require_approval[0])"
    )


def test_governance_error_ignores_empty_rule() -> None:
    decision = _decision(Effect.DENY, Reason.SUSPENDED, rule="")

    assert str(GovernanceError(decision)) == "deny tool 'shell.exec': suspended"
