"""Core value types for actions, principals, and policy decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ActionKind(StrEnum):
    """Name the kind of call a policy evaluates.

    Attributes:
        MODEL: A model invocation, such as `openai/gpt-4.1`.
        TOOL: A tool call.
        DATA: A data-scope access, such as `crm.customers:read`.
        AGENT: An outbound call to another agent.
    """

    MODEL = "model"
    TOOL = "tool"
    DATA = "data"
    AGENT = "agent"


class Effect(StrEnum):
    """Name the outcome of evaluating an action.

    Attributes:
        ALLOW: The action may proceed.
        DENY: The action is refused.
        REQUIRE_APPROVAL: The action needs approval before it may proceed.
    """

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Reason(StrEnum):
    """Name why a decision has its effect.

    These values are stable strings recorded with each decision.

    Attributes:
        SUSPENDED: The agent is suspended.
        DENY_RULE: A deny list matched the action.
        DELEGATION: The caller may not act for the principal.
        GUARDRAIL_MISSING: A required input guardrail has not run.
        NOT_GRANTED: No allow rule or granting approval rule matched.
        LIMIT: A usage limit was reached.
        APPROVAL_RULE: An approval rule matched.
        ALLOW_RULE: An allow list matched.
        APPROVAL_GRANTED: An approval handler allowed the action.
        APPROVAL_REJECTED: An approval handler refused the action.
        RUN_REVOKED: The active run was revoked.
        POLICY_UNAVAILABLE: No current policy bundle is available.
        FAIL_OPEN: The client allowed the action because the server was unreachable.
        OUTSIDE_WINDOW: The action is outside the agent's operating window.
        EFFECT_DENIED: An effect tag on the resource is denied.
        ASSISTIVE_EFFECT: An assistive agent tried a side-effecting action.
        EFFECT_APPROVAL: An effect tag on the resource requires approval.
        APPROVAL_UNAVAILABLE: An unattended agent has nobody to ask.
        APPROVAL_TIMEOUT: The approval route ended without a decision.
        QUOTA_UNAVAILABLE: A shared quota cannot be charged.
        CIRCUIT_OPEN: Too many recent denials opened the circuit breaker.
        CHAIN_TOO_DEEP: The call chain is longer than `agents.max_chain_depth`.
        CHAIN_REENTRY: This agent already appears in the chain.
        PRINCIPAL_REQUIRED: A chain hop has no principal.
        PRINCIPAL_FORBIDDEN: The agent must not act for a principal.
        CHAIN_POLICY_UNAVAILABLE: A chain member's grants are not in the bundle.
        SUPERVISOR_GRANTS_MISSING: `cap_to_supervisor` is set and that
            supervisor has no grant document.
        UNSUPERVISED: A supervisor is required and none is resolved.
        DEAD_MAN: The supervisor acknowledgement is overdue.
    """

    SUSPENDED = "suspended"
    DENY_RULE = "deny_rule"
    DELEGATION = "delegation"
    GUARDRAIL_MISSING = "guardrail_missing"
    NOT_GRANTED = "not_granted"
    LIMIT = "limit"
    APPROVAL_RULE = "approval_rule"
    ALLOW_RULE = "allow_rule"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    RUN_REVOKED = "run_revoked"
    POLICY_UNAVAILABLE = "policy_unavailable"
    FAIL_OPEN = "fail_open"
    OUTSIDE_WINDOW = "outside_window"
    EFFECT_DENIED = "effect_denied"
    ASSISTIVE_EFFECT = "assistive_effect"
    EFFECT_APPROVAL = "effect_approval"
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    APPROVAL_TIMEOUT = "approval_timeout"
    QUOTA_UNAVAILABLE = "quota_unavailable"
    CIRCUIT_OPEN = "circuit_open"
    CHAIN_TOO_DEEP = "chain_too_deep"
    CHAIN_REENTRY = "chain_reentry"
    PRINCIPAL_REQUIRED = "principal_required"
    PRINCIPAL_FORBIDDEN = "principal_forbidden"
    CHAIN_POLICY_UNAVAILABLE = "chain_policy_unavailable"
    SUPERVISOR_GRANTS_MISSING = "supervisor_grants_missing"
    UNSUPERVISED = "unsupervised"
    DEAD_MAN = "dead_man"


@dataclass(frozen=True, slots=True)
class Principal:
    """Identify the person an action is performed for.

    Args:
        user: Stable user identifier.
        groups: Group names used by delegation checks. Empty when the user
            belongs to none.

    Example:
        ```python
        principal = Principal("ada", frozenset({"support"}))
        assert principal.user == "ada"
        assert "support" in principal.groups
        ```
    """

    user: str
    groups: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True, eq=False)
class Action:
    """Describe one model, tool, data, or agent call.

    Instances compare by identity. `args` is copied so later changes to the
    mapping passed in do not change the action.

    Args:
        kind: Kind of call.
        resource: Model name, tool name, data scope, or agent name.
        args: Arguments supplied with the call. Empty when the call has none.
        principal: User this call is made for, when acting on behalf of someone.

    Example:
        ```python
        action = Action.tool("files.read")
        assert action.kind is ActionKind.TOOL
        assert action.resource == "files.read"
        assert action.args == {}
        ```
    """

    kind: ActionKind
    resource: str
    args: Mapping[str, object] = field(default_factory=dict)
    principal: Principal | None = None

    def __post_init__(self) -> None:
        """Copy call arguments so the action keeps its own mapping."""
        object.__setattr__(self, "args", dict(self.args))

    @classmethod
    def model(cls, name: str) -> Action:
        """Build a model-call action.

        Args:
            name: Provider-qualified model name, such as `openai/gpt-4.1`.

        Returns:
            An action of kind `model`.
        """
        return cls(kind=ActionKind.MODEL, resource=name)

    @classmethod
    def tool(cls, name: str, args: Mapping[str, object] | None = None) -> Action:
        """Build a tool-call action.

        Args:
            name: Tool name, which may include dots such as `files.read`.
            args: Arguments the tool was called with. `None` means no arguments.

        Returns:
            An action of kind `tool`.
        """
        return cls(kind=ActionKind.TOOL, resource=name, args={} if args is None else args)

    @classmethod
    def data(cls, scope: str) -> Action:
        """Build a data-access action.

        Args:
            scope: Data scope, such as `crm.customers:read`.

        Returns:
            An action of kind `data`.
        """
        return cls(kind=ActionKind.DATA, resource=scope)

    @classmethod
    def agent(cls, name: str) -> Action:
        """Build an outbound call to another agent.

        Args:
            name: Name of the agent being called.

        Returns:
            An action of kind `agent`.
        """
        return cls(kind=ActionKind.AGENT, resource=name)


@dataclass(frozen=True, slots=True, eq=False)
class Decision:
    """Record the result of evaluating one action.

    Instances compare by identity. `rule` is an identifier such as
    `agent:tools.allow[0]` or `agent:limits.max_steps_per_task`, and is `None`
    when the reason is `suspended` or `not_granted`.

    Args:
        effect: Whether the action is allowed, denied, or needs approval.
        reason: Why the effect was chosen.
        action: Action that was evaluated.
        policy_version: Identifier of the policy that produced this decision.
        rule: Identifier of the rule that decided the effect, if one applied.

    Example:
        ```python
        decision = Decision(
            Effect.DENY,
            Reason.NOT_GRANTED,
            Action.tool("shell.exec"),
            "v1",
        )
        assert decision.allowed is False
        assert decision.rule is None
        ```
    """

    effect: Effect
    reason: Reason
    action: Action
    policy_version: str
    rule: str | None = None

    @property
    def allowed(self) -> bool:
        """Return whether the effect is allow.

        Returns:
            `True` when `effect` is `allow`.
        """
        return self.effect is Effect.ALLOW
