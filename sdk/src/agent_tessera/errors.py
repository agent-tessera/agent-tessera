"""Errors raised by the Agent Tessera SDK."""

from pathlib import Path

from agent_tessera.types import Decision


class TesseraError(Exception):
    """Raise as the base class for every Agent Tessera error."""


class ServerError(TesseraError):
    """Raise when the control plane cannot be used.

    The server is unreachable, the credentials are rejected, or the response
    does not match the protocol.
    """


class ConfigError(TesseraError):
    """Raise when SDK configuration is invalid.

    The policy path is missing, the agent name does not match the loaded
    policy, or the wrapped client is unsupported.
    """


class PolicyError(TesseraError):
    """Raise when a policy cannot be read or validated.

    The string form is `message` alone, or `<path>: <message>` when `path` is
    set.

    Args:
        message: What is wrong with the policy.
        path: File the error refers to. Omit it when the policy did not come
            from a file.

    Example:
        ```python
        from pathlib import Path

        err = PolicyError("unknown field", path=Path("policy.yaml"))
        assert err.path == Path("policy.yaml")
        assert str(err) == "policy.yaml: unknown field"
        ```
    """

    path: Path | None

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        """Store the policy failure and an optional source path.

        Args:
            message: What is wrong with the policy.
            path: File the error refers to. Omit it when the policy did not
                come from a file.
        """
        self.path = path
        rendered = f"{path}: {message}" if path is not None else message
        super().__init__(rendered)


class GovernanceError(TesseraError):
    """Raise when an action is not allowed to proceed.

    The string form is `<effect> <kind> '<resource>': <reason>`. When `rule`
    is set, ` (rule <rule>)` is appended.

    Args:
        decision: Decision that blocked the action.

    Example:
        ```python
        from agent_tessera import Action, Decision, Denied, Effect, Reason

        decision = Decision(
            Effect.DENY,
            Reason.DENY_RULE,
            Action.tool("shell.exec"),
            "v1",
            "agent:tools.deny[0]",
        )
        assert str(Denied(decision)) == (
            "deny tool 'shell.exec': deny_rule (rule agent:tools.deny[0])"
        )
        ```
    """

    decision: Decision

    def __init__(self, decision: Decision) -> None:
        """Store the decision that blocked the action.

        Args:
            decision: Decision that blocked the action.
        """
        self.decision = decision
        message = (
            f"{decision.effect} {decision.action.kind} "
            f"'{decision.action.resource}': {decision.reason}"
        )
        if decision.rule:
            message = f"{message} (rule {decision.rule})"
        super().__init__(message)


class Denied(GovernanceError):  # noqa: N818
    """Raise when policy denies an action."""


class ApprovalRequired(GovernanceError):  # noqa: N818
    """Raise when an action needs approval before it can proceed.

    Attributes:
        approval_id: Server approval id when the client did not wait.
            `None` when no request was stored.

    Example:
        ```python
        err = ApprovalRequired(decision, approval_id="01H")
        assert err.approval_id == "01H"
        ```
    """

    approval_id: str | None

    def __init__(self, decision: Decision, approval_id: str | None = None) -> None:
        """Store the decision and, when present, the server approval id.

        Args:
            decision: Decision that still needs approval.
            approval_id: Id from `POST /v1/approvals`. Omit it offline.
        """
        self.approval_id = approval_id
        super().__init__(decision)


class RegistryError(TesseraError):
    """Raise when a local agent registry cannot be read or changed.

    Example:
        ```python
        from agent_tessera import RegistryError

        err = RegistryError("registry is locked")
        assert str(err) == "registry is locked"
        ```
    """
