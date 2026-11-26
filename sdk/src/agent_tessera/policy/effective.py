"""Merge an agent policy with the baselines that apply to it."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from agent_tessera.errors import PolicyError
from agent_tessera.policy.models import (
    AgentPolicy,
    ApprovalRule,
    BaselinePolicy,
    CallerConstraint,
    CallerConstraintRule,
    CircuitBreakerSpec,
    Condition,
    Delegation,
    EffectsSpec,
    EffectTag,
    OnMissing,
    QuotaSpec,
    parse_duration,
)
from agent_tessera.types import ActionKind

_SECTIONS: tuple[tuple[ActionKind, str], ...] = (
    (ActionKind.MODEL, "models"),
    (ActionKind.TOOL, "tools"),
    (ActionKind.DATA, "data"),
)
_STRICTNESS: dict[str, int] = {"flag": 0, "warn": 1, "block": 2}


@dataclass(frozen=True, slots=True)
class PatternRule:
    """A glob pattern and the rule identifier that produced it.

    Attributes:
        pattern: Shell-style glob matched against a resource name.
        rule: Identifier of the policy entry, such as `agent:tools.allow[0]`.
        literal: Whether `pattern` contains no `*`, `?`, or `[`.
    """

    pattern: str
    rule: str
    literal: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Record whether the pattern is an exact resource name."""
        object.__setattr__(
            self,
            "literal",
            "*" not in self.pattern and "?" not in self.pattern and "[" not in self.pattern,
        )


@dataclass(frozen=True, slots=True)
class ApprovalPatternRule:
    """An approval glob, its conditions, and whether it also grants access.

    Attributes:
        pattern: Shell-style glob matched against a resource name.
        when: Conditions that must all hold before approval is required.
        grants: `True` when the agent policy contributed the entry. Baseline
            entries never grant.
        rule: Identifier of the policy entry.
    """

    pattern: str
    when: tuple[Condition, ...]
    grants: bool
    rule: str


@dataclass(frozen=True, slots=True)
class LimitValue:
    """One numeric limit and the rule that set it.

    Attributes:
        value: Limit threshold. Integers are stored as floats.
        rule: Identifier of the policy entry that supplied the value.
    """

    value: float
    rule: str


@dataclass(frozen=True, slots=True)
class OperatingWindow:
    """Local clock window copied from `autonomy.active_hours`.

    Attributes:
        tz: IANA time zone name.
        days: Weekdays the window covers. Monday is 0 and Sunday is 6.
        start_minute: First allowed minute of the local day, inclusive.
        end_minute: First minute outside the window. When this is earlier
            than `start_minute`, the window crosses midnight.

    Example:
        ```python
        window = OperatingWindow("Europe/Madrid", frozenset({0, 1, 2, 3, 4}), 8 * 60, 20 * 60)
        assert 0 in window.days
        ```
    """

    tz: str
    days: frozenset[int]
    start_minute: int
    end_minute: int


@dataclass(frozen=True, slots=True)
class QuotaLimit:
    """One merged quota and the rule that supplied its ceiling.

    Attributes:
        name: Quota name from the source that set `max`.
        metric: What the counter measures.
        window: How long the counter covers.
        scope: Who shares the counter.
        max: Smallest ceiling among sources with the same key.
        kinds: Action kinds that count, sorted. `None` counts every kind.
        resources: Resource globs that count, sorted. `None` counts every
            resource.
        on_exhausted: `deny` or `suspend`, from the source that set `max`.
        rule: Identifier such as `agent:limits.quotas[daily-tokens]`.

    Example:
        ```python
        quota = QuotaLimit(
            "daily-tokens",
            "tokens",
            "day",
            "agent",
            10,
            ("model",),
            None,
            "deny",
            "agent:limits.quotas[daily-tokens]",
        )
        assert quota.max == 10
        ```
    """

    name: str
    metric: str
    window: str
    scope: str
    max: float
    kinds: tuple[ActionKind, ...] | None
    resources: tuple[str, ...] | None
    on_exhausted: str
    rule: str


@dataclass(frozen=True, slots=True)
class CircuitLimit:
    """Merged circuit breaker.

    Attributes:
        denials: Smallest denial count among the sources that set a breaker.
        window_seconds: Shortest window, in seconds.
        action: `revoke_run` when any source asks for it, otherwise `suspend`.
        rule: Identifier of the source that set `denials`.

    Example:
        ```python
        breaker = CircuitLimit(3, 300, "revoke_run", "baseline/prod:circuit_breaker")
        assert breaker.action == "revoke_run"
        ```
    """

    denials: int
    window_seconds: int
    action: str
    rule: str


@dataclass(frozen=True, slots=True)
class EffectiveControls:
    """Quotas, caps, effects, and autonomy after baseline merge.

    Attributes:
        autonomy: Agent autonomy level. Baselines do not change it.
        active_hours: Operating window, or `None` when the agent may act at
            any time.
        max_output_tokens: Smallest per-call output cap.
        max_input_tokens: Smallest per-call input cap.
        max_tool_arg_bytes: Smallest per-call tool-argument cap.
        max_in_flight_model_calls: Smallest in-flight model-call cap.
        max_in_flight_tool_calls: Smallest in-flight tool-call cap.
        max_concurrent_runs: Smallest concurrent-run cap.
        run_max_duration: Smallest run duration, in seconds.
        run_max_steps: Smallest run step cap.
        run_max_cost_usd: Smallest run spend cap.
        quotas: Merged quotas. Same metric, window, scope, kinds, and
            resources keep the smaller `max`.
        circuit_breaker: Merged breaker, or `None`.
        effect_deny: Denied effect tags, agent entries first. Each item is
            `(tag, rule)`.
        effect_require_approval: Effect tags that require approval, agent
            entries first. Each item is `(tag, rule)`.
        effect_classify: Classification patterns in first-seen order. Each
            item is `(pattern, tags, rule)`. Tags from every source are united.

    Example:
        ```python
        controls = EffectiveControls(
            "interactive",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            (),
            None,
            (),
            (),
            (),
        )
        assert controls.autonomy == "interactive"
        assert controls.quotas == ()
        ```
    """

    autonomy: str
    active_hours: OperatingWindow | None
    max_output_tokens: LimitValue | None
    max_input_tokens: LimitValue | None
    max_tool_arg_bytes: LimitValue | None
    max_in_flight_model_calls: LimitValue | None
    max_in_flight_tool_calls: LimitValue | None
    max_concurrent_runs: LimitValue | None
    run_max_duration: LimitValue | None
    run_max_steps: LimitValue | None
    run_max_cost_usd: LimitValue | None
    quotas: tuple[QuotaLimit, ...]
    circuit_breaker: CircuitLimit | None
    effect_deny: tuple[tuple[EffectTag, str], ...]
    effect_require_approval: tuple[tuple[EffectTag, str], ...]
    effect_classify: tuple[tuple[str, frozenset[EffectTag], str], ...]


@dataclass(frozen=True, slots=True)
class RequiredGuardrail:
    """A guardrail the agent must run, with the strictest missing-action.

    Attributes:
        name: Guardrail name, such as `input.prompt_injection`.
        on_missing: Strictest `on_missing` among the sources that require it.
        rule: Identifier of the source that supplied that strictness.
    """

    name: str
    on_missing: OnMissing
    rule: str


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    """The agent policy after applicable baselines have constrained it.

    Attributes:
        agent: Agent name.
        environment: Environment name.
        owner: Accountable owner, or `None`.
        version: `sha256:` plus the hex digest of the canonical merged input.
        applied_baselines: Names of baselines that matched, sorted.
        allow: Grant patterns for every action kind.
        deny: Deny patterns for every action kind. Agent entries come first.
        approvals: Approval patterns for every action kind.
        may_be_invoked_by: Patterns for agents that may call this one.
        delegation: On-behalf-of settings from the agent policy.
        max_steps_per_task: Smallest step limit that any source set.
        tokens_per_day: Smallest daily token limit that any source set.
        cost_usd_per_day: Smallest daily spend limit that any source set.
        calls_per_minute: Per-pattern call caps, sorted by pattern.
        required_guardrails: Required guardrails, sorted by name.
        capture_content: Whether audit events may store prompts and responses.
        controls: Merged quotas, caps, effect tags, and autonomy.
        max_chain_depth: Most agents in a chain that reaches this one,
            including this agent. `None` sets no limit.
        allow_reentry: Whether this agent may appear twice in one chain.
        caller_constraint: `own`, `intersect`, or per-caller rules.
        peer_allows: Allow globs for other agents, keyed by agent name and
            action kind. Empty in offline mode. The bundle supplies these
            when `caller_constraint` is `intersect`.
        cap_to_supervisor: Whether model and tool calls must also match the
            resolved supervisor's grants.
        supervisor_grants: Allow globs keyed by supervisor id and action
            kind. Empty until a bundle supplies them.
        supervision_required: Whether a missing supervisor denies every action.
        max_unacknowledged_seconds: Shortest dead-man duration, in seconds.
            `None` disables it.
    """

    agent: str
    environment: str
    owner: str | None
    version: str
    applied_baselines: tuple[str, ...]
    allow: Mapping[ActionKind, tuple[PatternRule, ...]]
    deny: Mapping[ActionKind, tuple[PatternRule, ...]]
    approvals: Mapping[ActionKind, tuple[ApprovalPatternRule, ...]]
    may_be_invoked_by: tuple[PatternRule, ...]
    delegation: Delegation
    max_steps_per_task: LimitValue | None
    tokens_per_day: LimitValue | None
    cost_usd_per_day: LimitValue | None
    calls_per_minute: tuple[tuple[str, LimitValue], ...]
    required_guardrails: tuple[RequiredGuardrail, ...]
    capture_content: bool
    controls: EffectiveControls
    max_chain_depth: int | None
    allow_reentry: bool
    caller_constraint: CallerConstraint | tuple[CallerConstraintRule, ...]
    peer_allows: Mapping[str, Mapping[ActionKind, tuple[str, ...]]] = field(default_factory=dict)
    cap_to_supervisor: bool = False
    supervisor_grants: Mapping[str, Mapping[ActionKind, tuple[str, ...]]] = field(
        default_factory=dict
    )
    supervision_required: bool = False
    max_unacknowledged_seconds: int | None = None


def build_effective_policy(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy] = (),
) -> EffectivePolicy:
    """Merge an agent policy with every baseline that selects it.

    Args:
        agent: Agent policy that grants permissions.
        baselines: Candidate baselines. Only those whose selector matches are
            applied, in name order.

    Returns:
        The effective policy used for evaluation.

    Raises:
        PolicyError: If two baselines share a name, or an applied baseline
            requires an owner and the agent names none.

    Example:
        ```python
        from agent_tessera.policy.effective import build_effective_policy
        from agent_tessera.policy.models import AgentPolicy

        policy = AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
            }
        )
        effective = build_effective_policy(policy)
        assert effective.agent == "bot"
        assert effective.version.startswith("sha256:")
        ```
    """
    _reject_duplicate_baselines(baselines)
    applicable = tuple(
        sorted(
            (baseline for baseline in baselines if baseline.spec.selector.matches(agent.metadata)),
            key=lambda baseline: baseline.metadata.name,
        )
    )
    for baseline in applicable:
        if baseline.spec.require_owner and agent.metadata.owner is None:
            raise PolicyError(f"owner required by baseline {baseline.metadata.name}")
    return EffectivePolicy(
        agent=agent.metadata.agent,
        environment=agent.metadata.environment,
        owner=agent.metadata.owner,
        version=_version(agent, applicable),
        applied_baselines=tuple(baseline.metadata.name for baseline in applicable),
        allow=_allow(agent),
        deny=_deny(agent, applicable),
        approvals=_approvals(agent, applicable),
        may_be_invoked_by=_patterns(
            agent.spec.agents.may_be_invoked_by,
            "agent:agents.may_be_invoked_by",
        ),
        delegation=agent.spec.delegation,
        max_steps_per_task=_minimum_limit(agent, applicable, "max_steps_per_task"),
        tokens_per_day=_minimum_limit(agent, applicable, "tokens_per_day"),
        cost_usd_per_day=_minimum_limit(agent, applicable, "cost_usd_per_day"),
        calls_per_minute=_calls_per_minute(agent, applicable),
        required_guardrails=_guardrails(agent, applicable),
        capture_content=agent.spec.audit.capture_content,
        controls=_controls(agent, applicable),
        max_chain_depth=agent.spec.agents.max_chain_depth,
        allow_reentry=agent.spec.agents.allow_reentry,
        caller_constraint=agent.spec.agents.caller_constraint,
        cap_to_supervisor=agent.spec.supervision.cap_to_supervisor,
        supervision_required=_supervision_required(agent, applicable),
        max_unacknowledged_seconds=_ack_limit(agent, applicable),
    )


def _supervision_required(agent: AgentPolicy, baselines: Sequence[BaselinePolicy]) -> bool:
    """Return whether a missing supervisor denies every action.

    Args:
        agent: Agent policy.
        baselines: Baselines that apply to the agent.

    Returns:
        `True` when the agent or any baseline requires a supervisor.
    """
    if agent.spec.supervision.required:
        return True
    return any(baseline.spec.supervision.required for baseline in baselines)


def _ack_limit(agent: AgentPolicy, baselines: Sequence[BaselinePolicy]) -> int | None:
    """Return the shortest dead-man duration.

    Args:
        agent: Agent policy.
        baselines: Baselines that apply to the agent.

    Returns:
        Seconds, or `None` when nobody sets `max_unacknowledged`.
    """
    texts = [agent.spec.supervision.max_unacknowledged]
    texts.extend(baseline.spec.supervision.max_unacknowledged for baseline in baselines)
    parsed = [parse_duration(text) for text in texts if text]
    if not parsed:
        return None
    return min(parsed)


def _reject_duplicate_baselines(baselines: Sequence[BaselinePolicy]) -> None:
    """Raise when two baselines use the same name.

    Args:
        baselines: Baselines supplied by the caller, applied or not.

    Raises:
        PolicyError: If a name appears more than once.
    """
    seen: set[str] = set()
    for baseline in baselines:
        name = baseline.metadata.name
        if name in seen:
            raise PolicyError(f"duplicate baseline name {name}")
        seen.add(name)


def _source(name: str) -> str:
    """Return the rule-identifier prefix for a baseline.

    Args:
        name: Baseline name.

    Returns:
        `baseline/<name>`.
    """
    return f"baseline/{name}"


def _patterns(values: Sequence[str], prefix: str) -> tuple[PatternRule, ...]:
    """Build indexed pattern rules.

    Args:
        values: Glob patterns in policy order.
        prefix: Rule identifier without the index, such as `agent:tools.allow`.

    Returns:
        One rule per pattern.
    """
    return tuple(PatternRule(pattern, f"{prefix}[{index}]") for index, pattern in enumerate(values))


def _allow(agent: AgentPolicy) -> Mapping[ActionKind, tuple[PatternRule, ...]]:
    """Collect grant patterns from the agent policy.

    Args:
        agent: Agent policy.

    Returns:
        Allow rules for every action kind. Baselines never add grants.
    """
    allowed: dict[ActionKind, tuple[PatternRule, ...]] = {
        kind: _patterns(getattr(agent.spec, section).allow, f"agent:{section}.allow")
        for kind, section in _SECTIONS
    }
    allowed[ActionKind.AGENT] = _patterns(
        agent.spec.agents.may_invoke,
        "agent:agents.may_invoke",
    )
    return MappingProxyType(allowed)


def _deny(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> Mapping[ActionKind, tuple[PatternRule, ...]]:
    """Collect deny patterns, agent first and then baselines by name.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        Deny rules for every action kind. Agent calls have no deny list.
    """
    denied: dict[ActionKind, tuple[PatternRule, ...]] = {}
    for kind, section in _SECTIONS:
        rules = list(_patterns(getattr(agent.spec, section).deny, f"agent:{section}.deny"))
        for baseline in baselines:
            source = _source(baseline.metadata.name)
            rules.extend(
                _patterns(getattr(baseline.spec, section).deny, f"{source}:{section}.deny")
            )
        denied[kind] = tuple(rules)
    denied[ActionKind.AGENT] = ()
    return MappingProxyType(denied)


def _approval_rules(
    rules: Sequence[ApprovalRule],
    prefix: str,
    *,
    grants: bool,
) -> tuple[ApprovalPatternRule, ...]:
    """Build approval rules from one policy section.

    Args:
        rules: Approval rules from a resource section.
        prefix: Rule identifier without the index.
        grants: Whether these entries also grant the resource.

    Returns:
        Approval pattern rules in policy order.
    """
    return tuple(
        ApprovalPatternRule(
            pattern=rule.name,
            when=rule.when,
            grants=grants,
            rule=f"{prefix}[{index}]",
        )
        for index, rule in enumerate(rules)
    )


def _approvals(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> Mapping[ActionKind, tuple[ApprovalPatternRule, ...]]:
    """Collect approval patterns, agent entries first.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        Approval rules for every action kind. Agent calls have none.
    """
    approvals: dict[ActionKind, tuple[ApprovalPatternRule, ...]] = {}
    for kind, section in _SECTIONS:
        rules = list(
            _approval_rules(
                getattr(agent.spec, section).require_approval,
                f"agent:{section}.require_approval",
                grants=True,
            )
        )
        for baseline in baselines:
            source = _source(baseline.metadata.name)
            rules.extend(
                _approval_rules(
                    getattr(baseline.spec, section).require_approval,
                    f"{source}:{section}.require_approval",
                    grants=False,
                )
            )
        approvals[kind] = tuple(rules)
    approvals[ActionKind.AGENT] = ()
    return MappingProxyType(approvals)


def _minimum_limit(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
    field: str,
) -> LimitValue | None:
    """Return the smallest set value for one scalar limit.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.
        field: Limit field name.

    Returns:
        The minimum value. On a tie the agent rule is kept. `None` when no
        source sets the limit.
    """
    chosen = _limit_value(getattr(agent.spec.limits, field), f"agent:limits.{field}")
    for baseline in baselines:
        source = _source(baseline.metadata.name)
        candidate = _limit_value(
            getattr(baseline.spec.limits, field),
            f"{source}:limits.{field}",
        )
        if candidate is None:
            continue
        if chosen is None or candidate.value < chosen.value:
            chosen = candidate
    return chosen


def _limit_value(value: int | float | None, rule: str) -> LimitValue | None:
    """Wrap a limit when a source set one.

    Args:
        value: Raw limit, or `None` when unset.
        rule: Rule identifier for the source.

    Returns:
        The wrapped limit, or `None`.
    """
    if value is None:
        return None
    return LimitValue(float(value), rule)


def _calls_per_minute(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[tuple[str, LimitValue], ...]:
    """Union per-pattern call caps, keeping the smaller value.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        `(pattern, limit)` pairs sorted by pattern.
    """
    chosen: dict[str, LimitValue] = {}

    def consider(pattern: str, value: int, rule: str) -> None:
        candidate = LimitValue(float(value), rule)
        current = chosen.get(pattern)
        if current is None or candidate.value < current.value:
            chosen[pattern] = candidate

    for pattern, value in agent.spec.limits.calls_per_minute.items():
        consider(pattern, value, f"agent:limits.calls_per_minute[{pattern}]")
    for baseline in baselines:
        source = _source(baseline.metadata.name)
        for pattern, value in baseline.spec.limits.calls_per_minute.items():
            consider(pattern, value, f"{source}:limits.calls_per_minute[{pattern}]")
    return tuple(sorted(chosen.items()))


def _guardrails(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[RequiredGuardrail, ...]:
    """Union required guardrails and keep the strictest missing-action.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        Required guardrails sorted by name. Equal strictness keeps the agent.
    """
    chosen: dict[str, RequiredGuardrail] = {}

    def consider(names: Sequence[str], on_missing: OnMissing, prefix: str) -> None:
        for index, name in enumerate(names):
            candidate = RequiredGuardrail(name, on_missing, f"{prefix}[{index}]")
            current = chosen.get(name)
            if current is None or _STRICTNESS[on_missing] > _STRICTNESS[current.on_missing]:
                chosen[name] = candidate

    consider(
        agent.spec.guardrails.required,
        agent.spec.guardrails.on_missing,
        "agent:guardrails.required",
    )
    for baseline in baselines:
        source = _source(baseline.metadata.name)
        consider(
            baseline.spec.guardrails.required,
            baseline.spec.guardrails.on_missing,
            f"{source}:guardrails.required",
        )
    return tuple(chosen[name] for name in sorted(chosen))


def _controls(agent: AgentPolicy, baselines: Sequence[BaselinePolicy]) -> EffectiveControls:
    """Merge v0.3 caps, quotas, effects, and the agent operating window.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        The merged controls. Numeric caps keep the smallest value. On a tie
        the agent rule is kept.
    """
    return EffectiveControls(
        autonomy=agent.metadata.autonomy.level,
        active_hours=_operating_window(agent),
        max_output_tokens=_nested_limit(agent, baselines, "per_call", "max_output_tokens"),
        max_input_tokens=_nested_limit(agent, baselines, "per_call", "max_input_tokens"),
        max_tool_arg_bytes=_nested_limit(agent, baselines, "per_call", "max_tool_arg_bytes"),
        max_in_flight_model_calls=_nested_limit(
            agent, baselines, "concurrency", "max_in_flight_model_calls"
        ),
        max_in_flight_tool_calls=_nested_limit(
            agent, baselines, "concurrency", "max_in_flight_tool_calls"
        ),
        max_concurrent_runs=_nested_limit(agent, baselines, "concurrency", "max_concurrent_runs"),
        run_max_duration=_duration_limit(agent, baselines),
        run_max_steps=_nested_limit(agent, baselines, "run", "max_steps"),
        run_max_cost_usd=_nested_limit(agent, baselines, "run", "max_cost_usd"),
        quotas=_merged_quotas(agent, baselines),
        circuit_breaker=_merged_circuit(agent, baselines),
        effect_deny=_merged_effect_list(agent, baselines, "deny"),
        effect_require_approval=_merged_effect_list(agent, baselines, "require_approval"),
        effect_classify=_merged_classify(agent, baselines),
    )


def _nested_limit(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
    group: str,
    field: str,
) -> LimitValue | None:
    """Return the smallest set value for one nested limit.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.
        group: Limit group, such as `per_call` or `run`.
        field: Field inside that group.

    Returns:
        The minimum value. On a tie the agent rule is kept. `None` when no
        source sets the limit.
    """
    chosen = _limit_value(
        getattr(getattr(agent.spec.limits, group), field),
        f"agent:limits.{group}.{field}",
    )
    for baseline in baselines:
        source = _source(baseline.metadata.name)
        candidate = _limit_value(
            getattr(getattr(baseline.spec.limits, group), field),
            f"{source}:limits.{group}.{field}",
        )
        if candidate is None:
            continue
        if chosen is None or candidate.value < chosen.value:
            chosen = candidate
    return chosen


def _duration_limit(agent: AgentPolicy, baselines: Sequence[BaselinePolicy]) -> LimitValue | None:
    """Return the shortest run duration, in seconds.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        The minimum duration. On a tie the agent rule is kept. `None` when no
        source sets `run.max_duration`.
    """
    chosen = _seconds_limit(agent.spec.limits.run.max_duration, "agent:limits.run.max_duration")
    for baseline in baselines:
        source = _source(baseline.metadata.name)
        candidate = _seconds_limit(
            baseline.spec.limits.run.max_duration,
            f"{source}:limits.run.max_duration",
        )
        if candidate is None:
            continue
        if chosen is None or candidate.value < chosen.value:
            chosen = candidate
    return chosen


def _seconds_limit(value: str | None, rule: str) -> LimitValue | None:
    """Wrap a duration string as a number of seconds.

    Args:
        value: Duration text, or `None` when unset.
        rule: Rule identifier for the source.

    Returns:
        The wrapped duration, or `None`.
    """
    if value is None:
        return None
    return LimitValue(float(parse_duration(value)), rule)


def _merged_quotas(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[QuotaLimit, ...]:
    """Keep the smaller ceiling for each quota key.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        Merged quotas in first-seen order. The key is metric, window, scope,
        kinds, and resources.
    """
    chosen: dict[tuple[object, ...], QuotaLimit] = {}
    for source, quotas in _quota_sources(agent, baselines):
        for quota in quotas:
            candidate = _quota_limit(quota, source)
            current = chosen.get(_quota_key(quota))
            if current is None or candidate.max < current.max:
                chosen[_quota_key(quota)] = candidate
    return tuple(chosen.values())


def _quota_sources(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[tuple[str, tuple[QuotaSpec, ...]], ...]:
    """List quota sections, agent first and then baselines by name.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        `(source, quotas)` pairs.
    """
    sources = [("agent", agent.spec.limits.quotas)]
    sources.extend(
        (_source(baseline.metadata.name), baseline.spec.limits.quotas) for baseline in baselines
    )
    return tuple(sources)


def _quota_limit(quota: QuotaSpec, source: str) -> QuotaLimit:
    """Copy one quota into the merged form.

    Args:
        quota: Quota from one policy document.
        source: `agent` or `baseline/<name>`.

    Returns:
        The quota with a rule identifier.
    """
    return QuotaLimit(
        name=quota.name,
        metric=quota.metric,
        window=quota.window,
        scope=quota.scope,
        max=quota.max,
        kinds=None if quota.kinds is None else tuple(sorted(quota.kinds, key=str)),
        resources=None if quota.resources is None else tuple(sorted(quota.resources)),
        on_exhausted=quota.on_exhausted,
        rule=f"{source}:limits.quotas[{quota.name}]",
    )


def _quota_key(quota: QuotaSpec) -> tuple[object, ...]:
    """Return the identity used to merge two quotas.

    Args:
        quota: Quota from one policy document.

    Returns:
        Metric, window, scope, and sorted kind and resource filters.
    """
    kinds = None if quota.kinds is None else tuple(sorted(quota.kinds, key=str))
    resources = None if quota.resources is None else tuple(sorted(quota.resources))
    return (quota.metric, quota.window, quota.scope, kinds, resources)


def _merged_circuit(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> CircuitLimit | None:
    """Combine circuit breakers by the strictest count, window, and action.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        The merged breaker. `None` when no source sets one. `revoke_run`
        wins over `suspend`.
    """
    denials: LimitValue | None = None
    window: LimitValue | None = None
    action = "suspend"
    for source, breaker in _circuit_sources(agent, baselines):
        if breaker is None:
            continue
        rule = f"{source}:circuit_breaker"
        candidate_denials = LimitValue(float(breaker.denials), rule)
        candidate_window = LimitValue(float(parse_duration(breaker.window)), rule)
        if denials is None or candidate_denials.value < denials.value:
            denials = candidate_denials
        if window is None or candidate_window.value < window.value:
            window = candidate_window
        if breaker.action == "revoke_run":
            action = "revoke_run"
    if denials is None or window is None:
        return None
    return CircuitLimit(int(denials.value), int(window.value), action, denials.rule)


def _circuit_sources(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[tuple[str, CircuitBreakerSpec | None], ...]:
    """List circuit breakers, agent first and then baselines by name.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        `(source, breaker)` pairs. `breaker` is `None` when that source sets
        none.
    """
    sources: list[tuple[str, CircuitBreakerSpec | None]] = [("agent", agent.spec.circuit_breaker)]
    sources.extend(
        (_source(baseline.metadata.name), baseline.spec.circuit_breaker) for baseline in baselines
    )
    return tuple(sources)


def _merged_effect_list(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
    field: str,
) -> tuple[tuple[EffectTag, str], ...]:
    """Append effect tags, keeping the first source for each tag.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.
        field: `deny` or `require_approval`.

    Returns:
        `(tag, rule)` pairs in first-seen order.
    """
    chosen: list[tuple[EffectTag, str]] = []
    seen: set[str] = set()
    for source, effects in _effect_sources(agent, baselines):
        for tag in getattr(effects, field):
            if tag in seen:
                continue
            seen.add(tag)
            chosen.append((tag, f"{source}:effects.{field}"))
    return tuple(chosen)


def _merged_classify(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[tuple[str, frozenset[EffectTag], str], ...]:
    """Union classification tags for each pattern.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        `(pattern, tags, rule)` triples. The rule names the first source that
        wrote the pattern. Tags from later sources are added.
    """
    chosen: dict[str, tuple[set[EffectTag], str]] = {}
    for source, effects in _effect_sources(agent, baselines):
        for pattern, tags in effects.classify.items():
            current = chosen.get(pattern)
            if current is None:
                chosen[pattern] = (set(tags), f"{source}:effects.classify")
                continue
            current[0].update(tags)
    return tuple((pattern, frozenset(tags), rule) for pattern, (tags, rule) in chosen.items())


def _effect_sources(
    agent: AgentPolicy,
    baselines: Sequence[BaselinePolicy],
) -> tuple[tuple[str, EffectsSpec], ...]:
    """List effect sections, agent first and then baselines by name.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        `(source, effects)` pairs.
    """
    sources: list[tuple[str, EffectsSpec]] = [("agent", agent.spec.effects)]
    sources.extend(
        (_source(baseline.metadata.name), baseline.spec.effects) for baseline in baselines
    )
    return tuple(sources)


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _operating_window(agent: AgentPolicy) -> OperatingWindow | None:
    """Copy the agent's operating window into weekday numbers.

    Args:
        agent: Agent policy.

    Returns:
        The window, or `None` when the agent has no `active_hours`.
    """
    hours = agent.metadata.autonomy.active_hours
    if hours is None:
        return None
    return OperatingWindow(
        tz=hours.tz,
        days=frozenset(_weekday_numbers(hours.days)),
        start_minute=_clock_minute(hours.from_),
        end_minute=_clock_minute(hours.to),
    )


def _weekday_numbers(days: tuple[str, ...]) -> tuple[int, ...]:
    """Expand weekday names and ranges to Monday-based indexes.

    Args:
        days: Tokens such as `mon` or `mon-fri`.

    Returns:
        Weekday indexes. Monday is 0.
    """
    chosen: list[int] = []
    for token in days:
        if token in _WEEKDAYS:
            chosen.append(_WEEKDAYS.index(token))
            continue
        start, _, end = token.partition("-")
        for index in range(_WEEKDAYS.index(start), _WEEKDAYS.index(end) + 1):
            if index not in chosen:
                chosen.append(index)
    return tuple(chosen)


def _clock_minute(value: str) -> int:
    """Convert `HH:MM` to minutes after midnight.

    Args:
        value: Clock time validated by the policy schema.

    Returns:
        Minutes from 0 through 1439.
    """
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _version(agent: AgentPolicy, baselines: Sequence[BaselinePolicy]) -> str:
    """Hash the agent policy and the baselines that were applied.

    Args:
        agent: Agent policy.
        baselines: Applicable baselines, already sorted by name.

    Returns:
        `sha256:` plus 64 hex characters.
    """
    canonical = json.dumps(
        {
            "agent": agent.model_dump(mode="json", by_alias=True),
            "baselines": [
                baseline.model_dump(mode="json", by_alias=True) for baseline in baselines
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest}"


__all__ = [
    "ApprovalPatternRule",
    "CircuitLimit",
    "EffectiveControls",
    "EffectivePolicy",
    "LimitValue",
    "OperatingWindow",
    "PatternRule",
    "QuotaLimit",
    "RequiredGuardrail",
    "build_effective_policy",
]
