"""Evaluate one action against an effective policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_tessera.policy.effective import (
    ApprovalPatternRule,
    EffectivePolicy,
    LimitValue,
    OperatingWindow,
    QuotaLimit,
)
from agent_tessera.policy.matching import conditions_match, first_match, matches_pattern
from agent_tessera.policy.models import CallerConstraintRule
from agent_tessera.types import Action, ActionKind, Decision, Effect, Principal, Reason

_READ_ONLY = frozenset({"read"})
_UNATTENDED = frozenset({"supervised", "autonomous"})


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Snapshot of the counters and flags an evaluation may consult.

    Attributes:
        suspended: When `True`, every action is denied.
        run_revoked: When `True`, and the client is not suspended, every action
            is denied because the active run was revoked.
        guardrails_ran: Guardrail names that have already run.
        steps_in_task: Steps already taken in the active task. `None` means
            no task is active, so the step limit does not apply.
        tokens_today: Tokens recorded for the current day.
        calls_last_minute: Calls in the last minute, keyed by the
            `calls_per_minute` pattern.
        now: Clock used for the operating window. `None` uses the current
            time in the window's time zone.
        approvals_available: Whether an approval handler or server route can
            answer. `supervised` and `autonomous` agents are denied when this
            is `False`.
        input_tokens: Estimated input tokens for this call, when known.
        output_tokens: Requested output tokens for this call, when known.
        tool_arg_bytes: Size of the tool arguments, when known.
        cost_usd: Estimated cost of this call, when known.
        in_flight_model_calls: Model calls already open on this instance.
        in_flight_tool_calls: Tool calls already open on this instance.
        concurrent_runs: Runs already open for this agent.
        run_steps: Steps already taken in the active run.
        run_cost_usd: Estimated spend already recorded for the active run.
        run_elapsed_seconds: Seconds since the active run started, when known.
        run_id: Active run id. Required by a `run`-scoped quota.
        supervisor: Bound supervisor. Required by a `supervisor`-scoped quota.
        quota_usage: Amount already consumed, keyed by quota name.
        recent_denials: Denials already recorded inside the circuit-breaker
            window.
        chain: Agents the request has already passed through, root first.
            This agent is not included. Empty when the call did not arrive
            from another agent.
        dead_man: When `True`, and the policy sets `max_unacknowledged`,
            every action is denied.
    """

    suspended: bool = False
    run_revoked: bool = False
    guardrails_ran: frozenset[str] = frozenset()
    steps_in_task: int | None = None
    tokens_today: int = 0
    calls_last_minute: Mapping[str, int] = field(default_factory=dict)
    now: datetime | None = None
    approvals_available: bool = True
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_arg_bytes: int | None = None
    cost_usd: float | None = None
    in_flight_model_calls: int = 0
    in_flight_tool_calls: int = 0
    concurrent_runs: int = 0
    run_steps: int = 0
    run_cost_usd: float = 0.0
    run_elapsed_seconds: float | None = None
    run_id: str | None = None
    supervisor: str | None = None
    quota_usage: Mapping[str, float] = field(default_factory=dict)
    recent_denials: int = 0
    chain: tuple[str, ...] = ()
    dead_man: bool = False


def evaluate(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState | None = None,
) -> Decision:
    """Decide whether one action is allowed.

    The checks run in a fixed order and the first one that applies wins.
    Nothing is logged and no counters change.

    Args:
        policy: Effective policy for the agent.
        action: Action to evaluate.
        state: Runtime flags and counters. Defaults to an empty state.

    Returns:
        The decision, including the policy version.

    Example:
        ```python
        from agent_tessera.policy.effective import build_effective_policy
        from agent_tessera.policy.evaluator import evaluate
        from agent_tessera.policy.models import AgentPolicy
        from agent_tessera.types import Action, Effect

        agent = AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {"tools": {"allow": ["files.read"]}},
            }
        )
        decision = evaluate(build_effective_policy(agent), Action.tool("files.read"))
        assert decision.effect is Effect.ALLOW
        ```
    """
    current = RuntimeState() if state is None else state
    grant = _grant(policy, action)
    outcome = (
        _when_suspended(current)
        or _when_run_revoked(current)
        or _when_unsupervised(policy, current)
        or _when_dead_man(policy, current)
        or _when_outside_window(policy, current)
        or _when_circuit_open(policy, current)
        or _when_denied(policy, action)
        or _when_chain_denied(policy, action, current)
        or _when_delegation_denied(policy, action)
        or _when_guardrail_missing(policy, action, current)
        or _when_not_granted(grant)
        or _when_supervisor_capped(policy, action, current)
        or _when_peer_grant_denied(policy, action, current)
        or _when_quota_unavailable(policy, action, current)
        or _when_limited(policy, action, current)
        or _when_effect(policy, action)
        or _when_approval_required(policy, action)
    )
    if outcome is None:
        outcome = (Effect.ALLOW, Reason.ALLOW_RULE, grant)
    outcome = _without_unattended_approval(policy, current, outcome)
    effect, reason, rule = outcome
    return _decide(policy, action, effect, reason, rule)


_Outcome = tuple[Effect, Reason, str | None]


def _when_suspended(state: RuntimeState) -> _Outcome | None:
    """Return a denial when the agent is suspended.

    Args:
        state: Runtime flags.

    Returns:
        The suspended denial, or `None`.
    """
    if state.suspended:
        return (Effect.DENY, Reason.SUSPENDED, None)
    return None


def _when_unsupervised(policy: EffectivePolicy, state: RuntimeState) -> _Outcome | None:
    """Return a denial when a required supervisor is missing.

    Args:
        policy: Effective policy.
        state: Runtime state. `supervisor` is the resolved id.

    Returns:
        The unsupervised denial, or `None`.
    """
    if policy.supervision_required and not state.supervisor:
        return (Effect.DENY, Reason.UNSUPERVISED, "agent:supervision.required")
    return None


def _when_dead_man(policy: EffectivePolicy, state: RuntimeState) -> _Outcome | None:
    """Return a denial when the supervisor acknowledgement is overdue.

    Args:
        policy: Effective policy.
        state: Runtime state. `dead_man` is set by the control plane.

    Returns:
        The dead-man denial, or `None`.
    """
    if policy.max_unacknowledged_seconds is not None and state.dead_man:
        return (Effect.DENY, Reason.DEAD_MAN, "agent:supervision.max_unacknowledged")
    return None


def _when_outside_window(policy: EffectivePolicy, state: RuntimeState) -> _Outcome | None:
    """Return a denial when the action is outside the operating window.

    Args:
        policy: Effective policy.
        state: Clock used for the check.

    Returns:
        The outside-window denial, or `None` when the agent has no window or
        the clock is inside it.
    """
    window = policy.controls.active_hours
    if window is None or _inside_window(window, state.now):
        return None
    return (Effect.DENY, Reason.OUTSIDE_WINDOW, "agent:metadata.autonomy.active_hours")


def _when_circuit_open(policy: EffectivePolicy, state: RuntimeState) -> _Outcome | None:
    """Return a denial when the circuit breaker is open.

    Args:
        policy: Effective policy.
        state: Recent denial count.

    Returns:
        The circuit-open denial, or `None`.
    """
    breaker = policy.controls.circuit_breaker
    if breaker is None or state.recent_denials < breaker.denials:
        return None
    return (Effect.DENY, Reason.CIRCUIT_OPEN, breaker.rule)


def _when_run_revoked(state: RuntimeState) -> _Outcome | None:
    """Return a denial when the active run was revoked.

    Args:
        state: Runtime flags.

    Returns:
        The run-revoked denial, or `None`.
    """
    if state.run_revoked:
        return (Effect.DENY, Reason.RUN_REVOKED, None)
    return None


def _when_denied(policy: EffectivePolicy, action: Action) -> _Outcome | None:
    """Return a denial when a deny pattern matches.

    Args:
        policy: Effective policy.
        action: Action being evaluated.

    Returns:
        The deny-rule denial, or `None`.
    """
    denied = first_match(action.resource, policy.deny[action.kind])
    if denied is None:
        return None
    return (Effect.DENY, Reason.DENY_RULE, denied.rule)


def _when_chain_denied(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> _Outcome | None:
    """Return a denial when the call chain breaks a chain rule.

    Depth counts this agent plus every agent already in `state.chain`.
    Re-entry denies when this agent's name is already in that chain.
    `delegation.principal: required` denies a hop that has no principal.
    `forbidden` denies any action that carries one.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Chain and the rest of the runtime snapshot.

    Returns:
        The chain denial, or `None` when the chain is allowed.
    """
    depth = len((*state.chain, policy.agent))
    if policy.max_chain_depth is not None and depth > policy.max_chain_depth:
        return (Effect.DENY, Reason.CHAIN_TOO_DEEP, "agent:agents.max_chain_depth")
    if not policy.allow_reentry and policy.agent in state.chain:
        return (Effect.DENY, Reason.CHAIN_REENTRY, "agent:agents.allow_reentry")
    principal = policy.delegation.principal
    if principal == "required" and state.chain and action.principal is None:
        return (Effect.DENY, Reason.PRINCIPAL_REQUIRED, "agent:delegation.principal")
    if principal == "forbidden" and action.principal is not None:
        return (Effect.DENY, Reason.PRINCIPAL_FORBIDDEN, "agent:delegation.principal")
    return None


def _when_delegation_denied(policy: EffectivePolicy, action: Action) -> _Outcome | None:
    """Return a denial when the principal may not be acted for.

    Args:
        policy: Effective policy.
        action: Action being evaluated.

    Returns:
        The delegation denial, or `None` when there is no principal or the
        principal is allowed.
    """
    if action.principal is None:
        return None
    rule = _delegation_rule(policy, action.principal)
    if rule is None:
        return None
    return (Effect.DENY, Reason.DELEGATION, rule)


def _when_guardrail_missing(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> _Outcome | None:
    """Return a denial when a blocking input guardrail has not run.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Guardrails recorded as already run.

    Returns:
        The missing-guardrail denial, or `None`.
    """
    if action.kind is not ActionKind.MODEL:
        return None
    missing = _missing_input_guardrail(policy, state)
    if missing is None:
        return None
    return (Effect.DENY, Reason.GUARDRAIL_MISSING, missing)


def _when_supervisor_capped(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> _Outcome | None:
    """Return a denial when a capped call is outside the supervisor's grants.

    Model and tool calls are the only kinds this check applies to. A missing
    supervisor or a missing grant document denies the action. A document that
    does not list the resource denies it as not granted.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Runtime state. `supervisor` is the resolved id.

    Returns:
        The denial, or `None` when the cap is off, the kind is not capped,
        or the supervisor grants the resource.
    """
    if not policy.cap_to_supervisor or action.kind not in {ActionKind.MODEL, ActionKind.TOOL}:
        return None
    rule = "agent:supervision.cap_to_supervisor"
    supervisor = state.supervisor
    grants = None if supervisor is None else policy.supervisor_grants.get(supervisor)
    if not supervisor or grants is None:
        return (Effect.DENY, Reason.SUPERVISOR_GRANTS_MISSING, rule)
    patterns = grants.get(action.kind, ())
    if any(matches_pattern(action.resource, pattern) for pattern in patterns):
        return None
    return (Effect.DENY, Reason.NOT_GRANTED, rule)


def _when_peer_grant_denied(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> _Outcome | None:
    """Return a denial when an intersect chain does not grant the action.

    The immediate caller selects `own` or `intersect`. `intersect` requires
    every agent already in the chain to grant the resource. A chain member
    with no allow set in the bundle is unavailable.

    Args:
        policy: Effective policy, including peer allow sets from the bundle.
        action: Action being evaluated.
        state: Call chain, root first, not including this agent.

    Returns:
        The intersect denial, or `None` when the constraint is `own`, the
        chain is empty, or every member grants the action.
    """
    if not state.chain or _constraint_for(policy, state.chain[-1]) != "intersect":
        return None
    for name in state.chain:
        grants = policy.peer_allows.get(name)
        if grants is None:
            return (
                Effect.DENY,
                Reason.CHAIN_POLICY_UNAVAILABLE,
                "agent:agents.caller_constraint",
            )
        patterns = grants.get(action.kind, ())
        if not any(matches_pattern(action.resource, pattern) for pattern in patterns):
            return (Effect.DENY, Reason.NOT_GRANTED, "agent:agents.caller_constraint")
    return None


def _constraint_for(policy: EffectivePolicy, caller: str) -> str:
    """Return the chain constraint that applies to one caller.

    A list uses the first rule whose caller glob matches. No match means
    `own`.

    Args:
        policy: Effective policy.
        caller: Immediate caller agent name.

    Returns:
        `own` or `intersect`.
    """
    chosen = policy.caller_constraint
    if isinstance(chosen, str):
        return chosen
    for rule in chosen:
        if _rule_matches(rule, caller):
            return rule.constraint
    return "own"


def _rule_matches(rule: CallerConstraintRule, caller: str) -> bool:
    """Return whether a per-caller rule names this caller.

    Args:
        rule: One entry from `caller_constraint`.
        caller: Immediate caller agent name.

    Returns:
        `True` when one of the rule's globs matches.
    """
    return any(matches_pattern(caller, pattern) for pattern in rule.callers)


def _when_not_granted(grant: str | None) -> _Outcome | None:
    """Return a denial when nothing grants the action.

    Args:
        grant: Rule that grants the action, or `None`.

    Returns:
        The not-granted denial, or `None` when a grant exists.
    """
    if grant is None:
        return (Effect.DENY, Reason.NOT_GRANTED, None)
    return None


def _when_limited(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> _Outcome | None:
    """Return a denial when a limit has been reached.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Current counters.

    Returns:
        The limit denial, or `None`.
    """
    rule = (
        _legacy_limit(policy, action, state)
        or _per_call_limit(policy, action, state)
        or _concurrency_limit(policy, action, state)
        or _run_limit(policy, state)
        or _quota_limit(policy, action, state)
    )
    if rule is None:
        return None
    return (Effect.DENY, Reason.LIMIT, rule)


def _when_quota_unavailable(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> _Outcome | None:
    """Return a denial when a matching quota has no scope id.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Run and supervisor ids.

    Returns:
        The quota-unavailable denial, or `None`.
    """
    for quota in policy.controls.quotas:
        if _quota_applies(quota, action) and _scope_missing(quota, action, state):
            return (Effect.DENY, Reason.QUOTA_UNAVAILABLE, quota.rule)
    return None


def _when_effect(policy: EffectivePolicy, action: Action) -> _Outcome | None:
    """Return the effect-tag outcome for a granted resource.

    Args:
        policy: Effective policy.
        action: Action being evaluated.

    Returns:
        A denial or approval outcome, or `None` when no effect tag applies.
    """
    tags = _effect_tags(policy, action)
    denied = _matching_effect(policy.controls.effect_deny, tags)
    if denied is not None:
        return (Effect.DENY, Reason.EFFECT_DENIED, denied)
    if policy.controls.autonomy == "assistive" and not _read_only(tags):
        return (Effect.DENY, Reason.ASSISTIVE_EFFECT, "agent:metadata.autonomy")
    approval = _approval_effect(policy, tags)
    if approval is not None:
        return (Effect.REQUIRE_APPROVAL, Reason.EFFECT_APPROVAL, approval)
    return None


def _when_approval_required(policy: EffectivePolicy, action: Action) -> _Outcome | None:
    """Return an approval requirement when a matching approval rule applies.

    Args:
        policy: Effective policy.
        action: Action being evaluated.

    Returns:
        The approval outcome, or `None`.
    """
    approval = _approval(policy, action)
    if approval is None:
        return None
    return (Effect.REQUIRE_APPROVAL, Reason.APPROVAL_RULE, approval.rule)


def _decide(
    policy: EffectivePolicy,
    action: Action,
    effect: Effect,
    reason: Reason,
    rule: str | None,
) -> Decision:
    """Build a decision for this policy version.

    Args:
        policy: Effective policy being evaluated.
        action: Action that was evaluated.
        effect: Outcome.
        reason: Why that outcome was chosen.
        rule: Rule identifier, or `None`.

    Returns:
        The decision.
    """
    return Decision(effect, reason, action, policy.version, rule)


def _delegation_rule(policy: EffectivePolicy, principal: Principal) -> str | None:
    """Return the delegation rule that refuses this principal.

    Args:
        policy: Effective policy.
        principal: Person the action is for.

    Returns:
        The rule identifier, or `None` when delegation allows the principal.
    """
    if not policy.delegation.allow_on_behalf_of:
        return "agent:delegation.allow_on_behalf_of"
    groups = policy.delegation.user_groups
    if groups and principal.groups.isdisjoint(groups):
        return "agent:delegation.user_groups"
    return None


def _missing_input_guardrail(policy: EffectivePolicy, state: RuntimeState) -> str | None:
    """Return the rule for the first blocking input guardrail that has not run.

    Args:
        policy: Effective policy.
        state: Guardrails recorded as already run.

    Returns:
        The guardrail rule, or `None` when none block.
    """
    for guardrail in policy.required_guardrails:
        if (
            guardrail.name.startswith("input.")
            and guardrail.on_missing == "block"
            and guardrail.name not in state.guardrails_ran
        ):
            return guardrail.rule
    return None


def _grant(policy: EffectivePolicy, action: Action) -> str | None:
    """Return the rule that grants the action.

    Args:
        policy: Effective policy.
        action: Action to match.

    Returns:
        An allow-list rule, or a granting approval rule. `None` when the
        action is not granted.
    """
    allowed = first_match(action.resource, policy.allow[action.kind])
    if allowed is not None:
        return allowed.rule
    for entry in policy.approvals[action.kind]:
        if entry.grants and matches_pattern(action.resource, entry.pattern):
            return entry.rule
    return None


def _legacy_limit(policy: EffectivePolicy, action: Action, state: RuntimeState) -> str | None:
    """Return the first v0.1 limit rule the action has reached.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Current counters.

    Returns:
        The limit rule, or `None` when every applicable counter is under its
        limit.
    """
    if action.kind in {ActionKind.MODEL, ActionKind.TOOL} and state.steps_in_task is not None:
        limit = policy.max_steps_per_task
        if limit is not None and state.steps_in_task >= limit.value:
            return limit.rule
    tokens = policy.tokens_per_day
    if (
        action.kind is ActionKind.MODEL
        and tokens is not None
        and state.tokens_today >= tokens.value
    ):
        return tokens.rule
    if action.kind is ActionKind.TOOL:
        for pattern, limit in policy.calls_per_minute:
            count = state.calls_last_minute.get(pattern, 0)
            if matches_pattern(action.resource, pattern) and count >= limit.value:
                return limit.rule
    return None


def _per_call_limit(policy: EffectivePolicy, action: Action, state: RuntimeState) -> str | None:
    """Return a per-call cap the action exceeds.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Measured size of this call.

    Returns:
        The cap rule, or `None` when the call is within the caps or the size
        was not measured.
    """
    controls = policy.controls
    if action.kind is ActionKind.MODEL:
        output = _over_maximum(controls.max_output_tokens, _requested_output(action, state))
        if output is not None:
            return output
        return _over_maximum(controls.max_input_tokens, state.input_tokens)
    if action.kind is ActionKind.TOOL:
        return _over_maximum(controls.max_tool_arg_bytes, state.tool_arg_bytes)
    return None


def _concurrency_limit(
    policy: EffectivePolicy,
    action: Action,
    state: RuntimeState,
) -> str | None:
    """Return a concurrency cap the action would exceed.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: In-flight counts.

    Returns:
        The cap rule, or `None`.
    """
    controls = policy.controls
    if action.kind is ActionKind.MODEL:
        model = _over_count(controls.max_in_flight_model_calls, state.in_flight_model_calls)
        if model is not None:
            return model
    if action.kind is ActionKind.TOOL:
        tool = _over_count(controls.max_in_flight_tool_calls, state.in_flight_tool_calls)
        if tool is not None:
            return tool
    return _over_count(controls.max_concurrent_runs, state.concurrent_runs)


def _run_limit(policy: EffectivePolicy, state: RuntimeState) -> str | None:
    """Return a run bound the active run has reached.

    Args:
        policy: Effective policy.
        state: Run counters.

    Returns:
        The bound rule, or `None`.
    """
    controls = policy.controls
    steps = _over_count(controls.run_max_steps, state.run_steps)
    if steps is not None:
        return steps
    cost = _over_count(controls.run_max_cost_usd, state.run_cost_usd)
    if cost is not None:
        return cost
    if state.run_elapsed_seconds is None:
        return None
    return _over_count(controls.run_max_duration, state.run_elapsed_seconds)


def _quota_limit(policy: EffectivePolicy, action: Action, state: RuntimeState) -> str | None:
    """Return a quota this action would exhaust.

    Args:
        policy: Effective policy.
        action: Action being evaluated.
        state: Quota counters and this call's measured size.

    Returns:
        The quota rule, or `None`. A quota with a missing scope id is skipped
        here; that case is `quota_unavailable`.
    """
    for quota in policy.controls.quotas:
        if not _quota_applies(quota, action) or _scope_missing(quota, action, state):
            continue
        if _quota_exhausted(quota, state):
            return quota.rule
    return None


def _over_maximum(limit: LimitValue | None, amount: float | None) -> str | None:
    """Return the rule when a measured amount is above a cap.

    Args:
        limit: Cap, or `None` when unset.
        amount: Measured amount, or `None` when it was not measured.

    Returns:
        The cap rule, or `None`. An amount equal to the cap is still allowed.
    """
    if limit is None or amount is None or amount <= limit.value:
        return None
    return limit.rule


def _over_count(limit: LimitValue | None, amount: float) -> str | None:
    """Return the rule when a counter has reached a cap.

    Args:
        limit: Cap, or `None` when unset.
        amount: Amount already consumed.

    Returns:
        The cap rule, or `None`.
    """
    if limit is None or amount < limit.value:
        return None
    return limit.rule


def _requested_output(action: Action, state: RuntimeState) -> int | None:
    """Return the output-token request for a model call.

    Args:
        action: Action being evaluated.
        state: Runtime measurements.

    Returns:
        The state measurement, or `args["max_tokens"]` when that is an
        integer. `None` when neither is present.
    """
    if state.output_tokens is not None:
        return state.output_tokens
    raw = action.args.get("max_tokens")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _quota_applies(quota: QuotaLimit, action: Action) -> bool:
    """Return whether a quota counts this action.

    Args:
        quota: Merged quota.
        action: Action being evaluated.

    Returns:
        `True` when the kind and resource filters match.
    """
    if quota.kinds is not None and action.kind not in quota.kinds:
        return False
    if quota.resources is None:
        return True
    return any(matches_pattern(action.resource, pattern) for pattern in quota.resources)


def _scope_missing(quota: QuotaLimit, action: Action, state: RuntimeState) -> bool:
    """Return whether a shared quota has no id to charge.

    Args:
        quota: Merged quota.
        action: Action being evaluated.
        state: Run and supervisor ids.

    Returns:
        `True` for a principal, supervisor, or run quota whose id is absent.
    """
    if quota.scope == "principal":
        return action.principal is None
    if quota.scope == "supervisor":
        return state.supervisor is None
    if quota.scope == "run":
        return state.run_id is None
    return False


def _quota_exhausted(quota: QuotaLimit, state: RuntimeState) -> bool:
    """Return whether this action does not fit in the quota.

    Args:
        quota: Merged quota.
        state: Counters and this call's measured size.

    Returns:
        `True` when recorded usage has reached `max`, or a `call` window's
        own consumption is above `max`.
    """
    if quota.window == "call":
        return _call_amount(quota, state) > quota.max
    return state.quota_usage.get(quota.name, 0) >= quota.max


def _call_amount(quota: QuotaLimit, state: RuntimeState) -> float:
    """Return how much of a call-window quota this call consumes.

    Args:
        quota: Merged quota whose window is `call`.
        state: Measurements for this call.

    Returns:
        Token or cost size when the metric needs it, otherwise one call.
        An unmeasured token or cost call consumes nothing here.
    """
    if quota.metric == "tokens":
        if state.input_tokens is None and state.output_tokens is None:
            return 0
        return float(state.input_tokens or 0) + float(state.output_tokens or 0)
    if quota.metric == "input_tokens":
        return float(state.input_tokens or 0)
    if quota.metric == "output_tokens":
        return float(state.output_tokens or 0)
    if quota.metric == "cost_usd":
        return float(state.cost_usd or 0)
    return 1


def _effect_tags(policy: EffectivePolicy, action: Action) -> frozenset[str] | None:
    """Return the effect tags for a resource.

    Args:
        policy: Effective policy.
        action: Action being evaluated.

    Returns:
        The union of tags from every matching pattern. `None` when no pattern
        matches, which counts as an unknown side effect.
    """
    matched: set[str] = set()
    found = False
    for pattern, tags, _rule in policy.controls.effect_classify:
        if matches_pattern(action.resource, pattern):
            found = True
            matched.update(tags)
    if not found:
        return None
    return frozenset(matched)


def _read_only(tags: frozenset[str] | None) -> bool:
    """Return whether every tag is `read`.

    Args:
        tags: Classified tags, or `None` when the resource is unclassified.

    Returns:
        `False` for an unclassified resource or any tag other than `read`.
    """
    return tags is not None and tags <= _READ_ONLY


def _matching_effect(
    entries: tuple[tuple[str, str], ...],
    tags: frozenset[str] | None,
) -> str | None:
    """Return the rule for the first listed tag that the resource has.

    Args:
        entries: `(tag, rule)` pairs.
        tags: Classified tags, or `None` when the resource is unclassified.

    Returns:
        The rule, or `None` when the resource is unclassified or has none of
        the tags.
    """
    if tags is None:
        return None
    for tag, rule in entries:
        if tag in tags:
            return rule
    return None


def _approval_effect(policy: EffectivePolicy, tags: frozenset[str] | None) -> str | None:
    """Return the approval rule an effect tag triggers.

    Args:
        policy: Effective policy.
        tags: Classified tags, or `None` when the resource is unclassified.

    Returns:
        The rule. An unclassified resource matches a non-empty approval list.
        `None` when nothing requires approval.
    """
    entries = policy.controls.effect_require_approval
    if not entries:
        return None
    if tags is None:
        return entries[0][1]
    return _matching_effect(entries, tags)


def _inside_window(window: OperatingWindow, now: datetime | None) -> bool:
    """Return whether a moment sits inside an operating window.

    The start minute is included and the end minute is not. A window whose
    end is earlier than its start crosses midnight. A naive `now` is outside.

    Args:
        window: Operating window from the effective policy.
        now: Moment to test. `None` uses the current time in `window.tz`.

    Returns:
        `True` when the local weekday and minute are inside the window.
    """
    zone = ZoneInfo(window.tz)
    moment = datetime.now(zone) if now is None else now
    if moment.tzinfo is None:
        return False
    local = moment.astimezone(zone)
    if local.weekday() not in window.days:
        return False
    current = local.hour * 60 + local.minute
    start = window.start_minute
    end = window.end_minute
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _without_unattended_approval(
    policy: EffectivePolicy,
    state: RuntimeState,
    outcome: _Outcome,
) -> _Outcome:
    """Deny an approval when an unattended agent has nobody to ask.

    Args:
        policy: Effective policy.
        state: Whether an approval route is available.
        outcome: Decision chosen by the earlier checks.

    Returns:
        A denial with reason `approval_unavailable`, or the original outcome.
    """
    effect, _reason, rule = outcome
    if effect is not Effect.REQUIRE_APPROVAL:
        return outcome
    if state.approvals_available or policy.controls.autonomy not in _UNATTENDED:
        return outcome
    return (Effect.DENY, Reason.APPROVAL_UNAVAILABLE, rule)


def _approval(policy: EffectivePolicy, action: Action) -> ApprovalPatternRule | None:
    """Return the first approval rule whose pattern and conditions match.

    Args:
        policy: Effective policy.
        action: Action being evaluated.

    Returns:
        The matching approval rule, or `None`.
    """
    for entry in policy.approvals[action.kind]:
        if matches_pattern(action.resource, entry.pattern) and conditions_match(
            entry.when, action.args
        ):
            return entry
    return None


__all__ = ["RuntimeState", "evaluate"]
