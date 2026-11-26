"""Validate agent and baseline policy documents.

These models are the schema for `tessera/v1` policies. Unknown fields are
rejected, and list fields are stored as tuples. Agent, environment, and
baseline names match `SLUG`. Guardrail names match `GUARDRAIL_NAME`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_tessera.types import ActionKind

SLUG = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
GUARDRAIL_NAME = r"^[a-z0-9_]+(\.[a-z0-9_]+)+$"
Operator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "matches"]
OnMissing = Literal["block", "warn", "flag"]
RiskTier = Literal["low", "medium", "high"]

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
_ORDERING_OPS = frozenset({"gt", "gte", "lt", "lte"})
_MEMBERSHIP_OPS = frozenset({"in", "not_in"})
_DURATION = re.compile(r"^(?:(\d+)h|(\d+)m|(\d+)s|(\d+))$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ROUTE = re.compile(r"^(?:principal|supervisor|owner|user:.+|group:.+|agent:.+)$")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RESERVATION_METRICS = frozenset({"tokens", "input_tokens", "output_tokens", "cost_usd"})
EffectTag = Literal[
    "read",
    "write",
    "delete",
    "send",
    "financial",
    "credential",
    "spawn",
    "irreversible",
]
AutonomyLevel = Literal["assistive", "interactive", "supervised", "autonomous"]
QuotaMetric = Literal[
    "tokens",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "model_calls",
    "tool_calls",
    "agent_calls",
    "spawns",
    "steps",
    "effects",
]
QuotaWindow = Literal["call", "run", "minute", "hour", "day", "month"]
QuotaScope = Literal["instance", "agent", "run", "principal", "supervisor", "org"]
CallerConstraint = Literal["own", "intersect"]
PrincipalMode = Literal["required", "optional", "forbidden"]


def parse_duration(value: str) -> int:
    """Parse a limit duration into seconds.

    A bare number is seconds. `h`, `m`, and `s` suffixes select hours, minutes,
    and seconds. `2h`, `30m`, and `90` are valid. Combined values such as
    `2h30m` are not.

    Args:
        value: Duration text, already stripped.

    Returns:
        The duration in seconds.

    Raises:
        ValueError: If the text is not a positive duration of that shape.

    Example:
        ```python
        from agent_tessera.policy.models import parse_duration

        assert parse_duration("2h") == 7200
        assert parse_duration("90") == 90
        ```
    """
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError("duration must be seconds, or Nh, Nm, or Ns, such as 2h, 30m, or 90")
    hours, minutes, seconds, bare = match.groups()
    if hours is not None:
        total = int(hours) * 3600
    elif minutes is not None:
        total = int(minutes) * 60
    elif seconds is not None:
        total = int(seconds)
    else:
        total = int(bare or "0")
    if total <= 0:
        raise ValueError("duration must be greater than zero")
    return total


def _duration_text(value: object) -> object:
    """Turn an unquoted YAML number into duration text.

    Args:
        value: Raw duration. Booleans are rejected. Integers become decimal text.

    Returns:
        The value to validate as a duration string.

    Raises:
        ValueError: If `value` is a boolean.
    """
    if isinstance(value, bool):
        raise ValueError("duration must be seconds, or Nh, Nm, or Ns, such as 2h, 30m, or 90")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return value


def _checked_duration(value: str | None) -> str | None:
    """Accept a duration string only when it parses.

    Args:
        value: Duration text, or `None` when the field is unset.

    Returns:
        The same text, with surrounding space removed.

    Raises:
        ValueError: If the text is not a positive duration.
    """
    if value is None:
        return None
    parse_duration(value)
    return value


def _quota_reserves_model_output(quota: QuotaSpec) -> bool:
    """Return whether a quota can charge a model call before it returns.

    Args:
        quota: Quota being loaded.

    Returns:
        `True` when the metric is tokens or cost and the quota is not limited
        to non-model kinds.
    """
    if quota.metric not in _RESERVATION_METRICS:
        return False
    return quota.kinds is None or ActionKind.MODEL in quota.kinds


def _coerce_approval_strings(data: object) -> object:
    """Turn plain strings in `require_approval` into name-only rules.

    Args:
        data: Raw model input. Non-dicts are returned unchanged.

    Returns:
        The original input, or a copy whose string approval entries are dicts.
    """
    if not isinstance(data, dict) or "require_approval" not in data:
        return data
    raw = data["require_approval"]
    if not isinstance(raw, list | tuple):
        return data
    if not any(isinstance(item, str) for item in raw):
        return data
    rules: list[object] = [{"name": item} if isinstance(item, str) else item for item in raw]
    updated = dict(data)
    updated["require_approval"] = rules
    return updated


class Condition(BaseModel):
    """Compare one call argument with an expected value.

    `in` and `not_in` store `value` as a tuple. Ordering operators accept only
    an int or float, and reject bool. `matches` requires a regular expression.

    Attributes:
        arg: Name of the top-level call argument to read.
        op: Comparison operator.
        value: Expected value. Its shape depends on `op`.

    Example:
        ```python
        condition = Condition(arg="status", op="in", value=["open", "closed"])
        assert condition.value == ("open", "closed")
        ```
    """

    model_config = _MODEL_CONFIG

    arg: str = Field(min_length=1)
    op: Operator
    # Arbitrary YAML: scalars for most operators, and a list for in/not_in.
    value: Any

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: object, info: ValidationInfo) -> object:
        """Check `value` against the operator and normalize membership lists.

        Args:
            value: Raw expected value.
            info: Validation context, including the already checked operator.

        Returns:
            The value to store. Membership lists are returned as tuples.

        Raises:
            ValueError: If the value has the wrong shape, or a `matches`
                pattern does not compile.
        """
        op = info.data.get("op")
        if op in _MEMBERSHIP_OPS:
            if not isinstance(value, list):
                raise ValueError("value must be a list")
            return tuple(value)
        if op in _ORDERING_OPS:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError("value must be an int or float, not bool")
            return value
        if op == "matches":
            if not isinstance(value, str):
                raise ValueError("value must be a string")
            try:
                re.compile(value)
            except re.error as err:
                raise ValueError(f"value must be a valid regular expression: {err}") from err
        return value


class ApprovalRule(BaseModel):
    """Require approval for resources that match a glob.

    Attributes:
        name: Glob pattern matched against the resource name.
        when: Conditions that must all hold before approval is required. Empty
            means the pattern alone is enough.
    """

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    when: tuple[Condition, ...] = ()


class ResourceRules(BaseModel):
    """Grant, deny, or gate one class of resources.

    A string in `require_approval` is read as a rule with that name and no
    conditions.

    Attributes:
        allow: Glob patterns that grant a resource.
        deny: Glob patterns that refuse a resource.
        require_approval: Patterns that grant a resource and may require
            approval.

    Example:
        ```python
        rules = ResourceRules.model_validate({"require_approval": ["files.delete"]})
        assert rules.require_approval[0].name == "files.delete"
        ```
    """

    model_config = _MODEL_CONFIG

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    require_approval: tuple[ApprovalRule, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _coerce_require_approval(cls, data: object) -> object:
        """Coerce string approval entries before field validation.

        Args:
            data: Raw model input.

        Returns:
            Input with string `require_approval` items rewritten as rules.
        """
        return _coerce_approval_strings(data)


class BaselineResourceRules(BaseModel):
    """Deny or gate resources without granting new ones.

    A string in `require_approval` is read as a rule with that name and no
    conditions. Baselines have no `allow` list.

    Attributes:
        deny: Glob patterns that refuse a resource.
        require_approval: Patterns that add an approval requirement.
    """

    model_config = _MODEL_CONFIG

    deny: tuple[str, ...] = ()
    require_approval: tuple[ApprovalRule, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _coerce_require_approval(cls, data: object) -> object:
        """Coerce string approval entries before field validation.

        Args:
            data: Raw model input.

        Returns:
            Input with string `require_approval` items rewritten as rules.
        """
        return _coerce_approval_strings(data)


class CallerConstraintRule(BaseModel):
    """Apply one chain constraint to a set of callers.

    The first matching rule wins. `callers` entries are globs.

    Attributes:
        callers: Globs for the agents this rule covers. At least one.
        constraint: `own` limits the callee by its own policy. `intersect`
            also requires every agent in the chain to grant the action.

    Example:
        ```python
        rule = CallerConstraintRule(callers=("orchestrator",), constraint="intersect")
        assert rule.constraint == "intersect"
        ```
    """

    model_config = _MODEL_CONFIG

    callers: tuple[str, ...] = Field(min_length=1)
    constraint: CallerConstraint


class AgentRules(BaseModel):
    """Control which agents this agent may call and be called by.

    Attributes:
        may_invoke: Globs for agents this agent may call.
        may_be_invoked_by: Globs for agents that may call this agent.
        max_chain_depth: Most agents allowed in a chain that reaches this
            one, including this agent. `None` sets no limit.
        allow_reentry: Whether this agent may appear twice in one chain.
        caller_constraint: `own`, `intersect`, or a list of per-caller rules.
            `own` is the default.

    Example:
        ```python
        rules = AgentRules(caller_constraint="intersect", max_chain_depth=3)
        assert rules.allow_reentry is False
        ```
    """

    model_config = _MODEL_CONFIG

    may_invoke: tuple[str, ...] = ()
    may_be_invoked_by: tuple[str, ...] = ()
    max_chain_depth: PositiveInt | None = None
    allow_reentry: bool = False
    caller_constraint: CallerConstraint | tuple[CallerConstraintRule, ...] = "own"


class Delegation(BaseModel):
    """Say whether the agent may act for a user.

    Attributes:
        allow_on_behalf_of: Whether calls may carry a user principal.
        user_groups: Groups a principal must intersect when delegation is
            limited. Empty means any principal is allowed once delegation
            itself is allowed.
        principal: Whether a chain must, may, or must not carry a principal.
            `optional` is the default.

    Example:
        ```python
        delegation = Delegation(allow_on_behalf_of=True, principal="required")
        assert delegation.principal == "required"
        ```
    """

    model_config = _MODEL_CONFIG

    allow_on_behalf_of: bool = False
    user_groups: tuple[str, ...] = ()
    principal: PrincipalMode = "optional"


class PerCallLimits(BaseModel):
    """Cap a single model or tool call.

    Attributes:
        max_output_tokens: Most tokens a model response may request.
        max_input_tokens: Most tokens a model prompt may contain.
        max_tool_arg_bytes: Most bytes of tool arguments a call may send.

    Example:
        ```python
        cap = PerCallLimits(max_output_tokens=4096)
        assert cap.max_input_tokens is None
        ```
    """

    model_config = _MODEL_CONFIG

    max_output_tokens: PositiveInt | None = None
    max_input_tokens: PositiveInt | None = None
    max_tool_arg_bytes: PositiveInt | None = None


class ConcurrencyLimits(BaseModel):
    """Cap how many calls or runs may be in progress together.

    Attributes:
        max_in_flight_model_calls: Model calls one instance may have open.
        max_in_flight_tool_calls: Tool calls one instance may have open.
        max_concurrent_runs: Runs one agent may have open.

    Example:
        ```python
        limits = ConcurrencyLimits(max_in_flight_model_calls=4)
        assert limits.max_concurrent_runs is None
        ```
    """

    model_config = _MODEL_CONFIG

    max_in_flight_model_calls: PositiveInt | None = None
    max_in_flight_tool_calls: PositiveInt | None = None
    max_concurrent_runs: PositiveInt | None = None


class RunLimits(BaseModel):
    """Cap one run.

    Attributes:
        max_duration: Longest run, as seconds or `Nh`, `Nm`, or `Ns`.
        max_steps: Most steps in the run.
        max_cost_usd: Most estimated spend in the run, in US dollars.

    Example:
        ```python
        limits = RunLimits.model_validate({"max_duration": "2h", "max_steps": 50})
        assert limits.max_duration == "2h"
        ```
    """

    model_config = _MODEL_CONFIG

    max_duration: str | None = None
    max_steps: PositiveInt | None = None
    max_cost_usd: PositiveFloat | None = None

    @field_validator("max_duration", mode="before")
    @classmethod
    def _stringify_duration(cls, value: object) -> object:
        """Accept an unquoted number of seconds.

        Args:
            value: Raw duration.

        Returns:
            Duration text, or the original value when it is not an integer.
        """
        return _duration_text(value)

    @field_validator("max_duration")
    @classmethod
    def _validate_duration(cls, value: str | None) -> str | None:
        """Reject a duration the schema cannot parse.

        Args:
            value: Duration text.

        Returns:
            The duration text.

        Raises:
            ValueError: If the text is not a positive duration.
        """
        return _checked_duration(value)


class QuotaSpec(BaseModel):
    """One hard ceiling for a metric, window, and scope.

    Attributes:
        name: Quota name. Lowercase letters, digits, and hyphens.
        metric: What the counter measures.
        window: How long the counter covers.
        scope: Who shares the counter.
        max: Hard ceiling. A float, so a cost ceiling can be fractional.
        kinds: Action kinds that count. `None` counts every kind.
        resources: Resource globs that count. `None` counts every resource.
        warn_at: Fraction of `max` that only warns. Greater than 0 and at most 1.
        on_exhausted: `deny` or `suspend`.

    Example:
        ```python
        quota = QuotaSpec(
            name="refunds-per-hour",
            metric="tool_calls",
            window="hour",
            scope="agent",
            max=20,
        )
        assert quota.on_exhausted == "deny"
        ```
    """

    model_config = _MODEL_CONFIG

    name: str = Field(pattern=SLUG)
    metric: QuotaMetric
    window: QuotaWindow
    scope: QuotaScope
    max: float = Field(gt=0)
    kinds: tuple[ActionKind, ...] | None = None
    resources: tuple[str, ...] | None = None
    warn_at: float | None = Field(default=None, gt=0, le=1)
    on_exhausted: Literal["deny", "suspend"] = "deny"


class CircuitBreakerSpec(BaseModel):
    """Stop an agent after too many denials.

    Attributes:
        denials: How many denials open the breaker.
        window: Sliding window, as seconds or `Nh`, `Nm`, or `Ns`.
        action: `suspend` the agent or `revoke_run`.

    Example:
        ```python
        breaker = CircuitBreakerSpec(denials=5, window="10m", action="suspend")
        assert breaker.denials == 5
        ```
    """

    model_config = _MODEL_CONFIG

    denials: PositiveInt
    window: str
    action: Literal["suspend", "revoke_run"]

    @field_validator("window", mode="before")
    @classmethod
    def _stringify_window(cls, value: object) -> object:
        """Accept an unquoted number of seconds.

        Args:
            value: Raw window.

        Returns:
            Duration text, or the original value when it is not an integer.
        """
        return _duration_text(value)

    @field_validator("window")
    @classmethod
    def _validate_window(cls, value: str) -> str:
        """Reject a window the schema cannot parse.

        Args:
            value: Window text.

        Returns:
            The window text.

        Raises:
            ValueError: If the text is not a positive duration.
        """
        checked = _checked_duration(value)
        if checked is None:
            raise ValueError("duration must be seconds, or Nh, Nm, or Ns, such as 2h, 30m, or 90")
        return checked


class ApprovalsSpec(BaseModel):
    """Route an approval through one or more parties.

    Attributes:
        route_to: Escalation order. Each entry is `principal`, `supervisor`,
            `owner`, `user:...`, `group:...`, or `agent:...`.
        timeout: How long each entry is offered the approval.
        forbid_self_approval: Whether the principal who triggered the action
            may approve it.

    Example:
        ```python
        approvals = ApprovalsSpec.model_validate({"route_to": ["supervisor", "owner"]})
        assert approvals.timeout == "15m"
        assert approvals.forbid_self_approval is True
        ```
    """

    model_config = _MODEL_CONFIG

    route_to: tuple[str, ...] = ("principal",)
    timeout: str = "15m"
    forbid_self_approval: bool = True

    @field_validator("timeout", mode="before")
    @classmethod
    def _stringify_timeout(cls, value: object) -> object:
        """Accept an unquoted number of seconds.

        Args:
            value: Raw timeout.

        Returns:
            Duration text, or the original value when it is not an integer.
        """
        return _duration_text(value)

    @field_validator("route_to")
    @classmethod
    def _validate_route(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a route entry that is not a known party.

        Args:
            value: Escalation entries.

        Returns:
            The same entries.

        Raises:
            ValueError: If an entry is not principal, supervisor, owner, or a
                `user:`, `group:`, or `agent:` name.
        """
        for item in value:
            if _ROUTE.fullmatch(item) is None:
                raise ValueError(
                    "route_to entries must be principal, supervisor, owner, "
                    "user:..., group:..., or agent:..."
                )
        return value

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, value: str) -> str:
        """Reject a timeout the schema cannot parse.

        Args:
            value: Timeout text.

        Returns:
            The timeout text.

        Raises:
            ValueError: If the text is not a positive duration.
        """
        checked = _checked_duration(value)
        if checked is None:
            raise ValueError("duration must be seconds, or Nh, Nm, or Ns, such as 2h, 30m, or 90")
        return checked


class EffectsSpec(BaseModel):
    """Classify tools and name the effects to deny or approve.

    Attributes:
        classify: Glob pattern to effect tags. Tags are `read`, `write`,
            `delete`, `send`, `financial`, `credential`, `spawn`, and
            `irreversible`.
        deny: Effect tags that are denied.
        require_approval: Effect tags that require approval.

    Example:
        ```python
        effects = EffectsSpec.model_validate(
            {"classify": {"zendesk.read_*": ["read"]}, "deny": ["delete"]}
        )
        assert effects.classify["zendesk.read_*"] == ("read",)
        ```
    """

    model_config = _MODEL_CONFIG

    classify: dict[str, tuple[EffectTag, ...]] = Field(default_factory=dict)
    deny: tuple[EffectTag, ...] = ()
    require_approval: tuple[EffectTag, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _coerce_classify(cls, data: object) -> object:
        """Store each classification as a tuple.

        Args:
            data: Raw model input.

        Returns:
            Input whose `classify` lists are tuples.
        """
        if not isinstance(data, dict) or not isinstance(data.get("classify"), dict):
            return data
        classify = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in data["classify"].items()
        }
        updated = dict(data)
        updated["classify"] = classify
        return updated


class ActiveHoursSpec(BaseModel):
    """Name the clock window when governed actions are allowed.

    Attributes:
        tz: IANA time zone, such as `Europe/Madrid`.
        days: Weekdays or ranges. Each entry is `mon` through `sun`, or a
            range such as `mon-fri`.
        from_: Local start time, `HH:MM`. Accepts the alias `from`.
        to: Local end time, `HH:MM`.

    Example:
        ```python
        hours = ActiveHoursSpec.model_validate(
            {"tz": "Europe/Madrid", "days": ["mon-fri"], "from": "08:00", "to": "20:00"}
        )
        assert hours.from_ == "08:00"
        ```
    """

    model_config = _MODEL_CONFIG

    tz: str
    days: tuple[str, ...] = Field(min_length=1)
    from_: str = Field(alias="from", pattern=_CLOCK.pattern)
    to: str = Field(pattern=_CLOCK.pattern)

    @field_validator("tz")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        """Reject a time zone the host cannot load.

        Args:
            value: IANA time zone name.

        Returns:
            The same name.

        Raises:
            ValueError: If the name is not an IANA time zone.
        """
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as err:
            raise ValueError("tz must be an IANA time zone") from err
        return value

    @field_validator("days")
    @classmethod
    def _validate_days(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a weekday token that is not a day or an inclusive range.

        Args:
            value: Day tokens from the policy.

        Returns:
            The same tokens.

        Raises:
            ValueError: If a token is not `mon` through `sun` or a range whose
                start is not after its end.
        """
        for token in value:
            if token in _WEEKDAYS:
                continue
            start, separator, end = token.partition("-")
            if (
                separator != "-"
                or start not in _WEEKDAYS
                or end not in _WEEKDAYS
                or _WEEKDAYS.index(start) > _WEEKDAYS.index(end)
            ):
                raise ValueError("days must be mon through sun, or a range such as mon-fri")
        return value


class AutonomySettings(BaseModel):
    """Choose how independently an agent acts, and when it may act.

    A bare string such as `interactive` is the level, with no operating window.

    Attributes:
        level: `assistive`, `interactive`, `supervised`, or `autonomous`.
        active_hours: Operating window. `None` means the agent may act at any
            time.

    Example:
        ```python
        settings = AutonomySettings.model_validate("supervised")
        assert settings.level == "supervised"
        assert settings.active_hours is None
        ```
    """

    model_config = _MODEL_CONFIG

    level: AutonomyLevel = "interactive"
    active_hours: ActiveHoursSpec | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_level(cls, data: object) -> object:
        """Read a bare autonomy level as a mapping.

        Args:
            data: Raw model input.

        Returns:
            A mapping when `data` is a level string, otherwise the input.
        """
        if isinstance(data, str):
            return {"level": data}
        return data


class Limits(BaseModel):
    """Bound how much work an agent may do.

    The v0.1 fields stay valid on their own. `per_call`, `concurrency`, `run`,
    and `quotas` add the v0.3 ceilings. A token or cost quota that applies to
    models still requires `per_call.max_output_tokens`; that check lives on the
    policy document.

    Attributes:
        cost_usd_per_day: Maximum model spend per day, in US dollars. This
            shorthand does not by itself require a per-call output cap.
        tokens_per_day: Maximum total tokens per day.
        calls_per_minute: Glob pattern to the maximum matching tool calls in a
            minute.
        max_steps_per_task: Maximum model and tool steps in one task.
        per_call: Caps for one call.
        concurrency: Caps for in-flight work.
        run: Caps for one run.
        quotas: General quotas. An empty tuple adds none.

    Example:
        ```python
        limits = Limits(tokens_per_day=1000)
        assert limits.quotas == ()
        ```
    """

    model_config = _MODEL_CONFIG

    cost_usd_per_day: PositiveFloat | None = None
    tokens_per_day: PositiveInt | None = None
    calls_per_minute: dict[str, PositiveInt] = Field(default_factory=dict)
    max_steps_per_task: PositiveInt | None = None
    per_call: PerCallLimits = Field(default_factory=PerCallLimits)
    concurrency: ConcurrencyLimits = Field(default_factory=ConcurrencyLimits)
    run: RunLimits = Field(default_factory=RunLimits)
    quotas: tuple[QuotaSpec, ...] = ()


class Guardrails(BaseModel):
    """Name the guardrails an agent must run.

    Attributes:
        required: Guardrail names, each with at least one dot, such as
            `input.prompt_injection`.
        on_missing: What to do when a required guardrail has not run. One of
            `block`, `warn`, or `flag`.
    """

    model_config = _MODEL_CONFIG

    required: tuple[Annotated[str, StringConstraints(pattern=GUARDRAIL_NAME)], ...] = ()
    on_missing: OnMissing = "flag"


class SupervisionSpec(BaseModel):
    """Choose how a supervisor limits an agent.

    Attributes:
        cap_to_supervisor: When `True`, a model or tool call must also match
            the resolved supervisor's grant document. A missing document
            denies the action.
        required: When `True`, an action with no resolved supervisor is denied.
        max_unacknowledged: How long a supervisor may go without acknowledging
            the agent. `None` disables the dead-man switch. A baseline may set
            a shorter duration.
    """

    model_config = _MODEL_CONFIG

    cap_to_supervisor: bool = False
    required: bool = False
    max_unacknowledged: str | None = None

    @field_validator("max_unacknowledged", mode="before")
    @classmethod
    def _stringify_ack(cls, value: object) -> object:
        """Accept an unquoted number of seconds.

        Args:
            value: Raw duration.

        Returns:
            Duration text, or the original value when it is not an integer.
        """
        return _duration_text(value)

    @field_validator("max_unacknowledged")
    @classmethod
    def _validate_ack(cls, value: str | None) -> str | None:
        """Reject a duration the schema cannot parse.

        Args:
            value: Duration text.

        Returns:
            The duration text.

        Raises:
            ValueError: If the text is not a positive duration.
        """
        return _checked_duration(value)


class SupervisorAllowLists(BaseModel):
    """Allow globs for one kind of action in a supervisor grant.

    Attributes:
        allow: Glob patterns. Empty grants nothing of this kind.
    """

    model_config = _MODEL_CONFIG

    allow: tuple[Annotated[str, StringConstraints(min_length=1)], ...] = ()


class SupervisorGrantsSpec(BaseModel):
    """Model and tool allows a supervisor grants.

    Attributes:
        models: Model globs.
        tools: Tool globs.
    """

    model_config = _MODEL_CONFIG

    models: SupervisorAllowLists = Field(default_factory=SupervisorAllowLists)
    tools: SupervisorAllowLists = Field(default_factory=SupervisorAllowLists)


class SupervisorGrantsMetadata(BaseModel):
    """Name the supervisor a grant document belongs to.

    Attributes:
        supervisor: `user:` or `group:` id, at most 200 characters.
    """

    model_config = _MODEL_CONFIG

    supervisor: str = Field(pattern=r"^(?:user|group):\S+$", max_length=200)


class SupervisorGrants(BaseModel):
    """Allow lists for one supervisor.

    Attributes:
        api_version: Always `tessera/v1`.
        kind: Always `SupervisorGrants`.
        metadata: The supervisor id.
        spec: Model and tool allow lists.

    Example:
        ```python
        grants = SupervisorGrants.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "SupervisorGrants",
                "metadata": {"supervisor": "user:ada"},
                "spec": {"tools": {"allow": ["files.read"]}},
            }
        )
        assert grants.spec.tools.allow == ("files.read",)
        ```
    """

    model_config = _MODEL_CONFIG

    api_version: Literal["tessera/v1"] = Field(alias="apiVersion")
    kind: Literal["SupervisorGrants"]
    metadata: SupervisorGrantsMetadata
    spec: SupervisorGrantsSpec = Field(default_factory=SupervisorGrantsSpec)


class AuditSettings(BaseModel):
    """Choose what an audit event may store.

    Attributes:
        capture_content: Whether prompts and responses may be stored.
    """

    model_config = _MODEL_CONFIG

    capture_content: bool = False


class AgentPolicySpec(BaseModel):
    """Describe the permissions and limits of one agent.

    Attributes:
        models: Rules for model calls.
        tools: Rules for tool calls.
        data: Rules for data scopes.
        agents: Rules for agent-to-agent calls.
        delegation: On-behalf-of settings.
        limits: Usage bounds.
        guardrails: Required guardrails.
        audit: Audit capture settings.
        circuit_breaker: Denial breaker. `None` disables it.
        approvals: How an approval is routed. The default offers it to the
            principal for 15 minutes.
        effects: Tool effect tags. Empty classification leaves every tool
            unclassified.
        supervision: Whether model and tool calls are capped to the
            supervisor's grants.
    """

    model_config = _MODEL_CONFIG

    models: ResourceRules = Field(default_factory=ResourceRules)
    tools: ResourceRules = Field(default_factory=ResourceRules)
    data: ResourceRules = Field(default_factory=ResourceRules)
    agents: AgentRules = Field(default_factory=AgentRules)
    delegation: Delegation = Field(default_factory=Delegation)
    limits: Limits = Field(default_factory=Limits)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    circuit_breaker: CircuitBreakerSpec | None = None
    approvals: ApprovalsSpec = Field(default_factory=ApprovalsSpec)
    effects: EffectsSpec = Field(default_factory=EffectsSpec)
    supervision: SupervisionSpec = Field(default_factory=SupervisionSpec)


class AgentPolicyMetadata(BaseModel):
    """Identify an agent and the selectors that apply to it.

    Attributes:
        agent: Agent name. Lowercase letters, digits, and hyphens, up to 63
            characters, and it cannot start or end with a hyphen.
        environment: Environment name, using the same shape as `agent`.
        owner: Accountable owner. `None` when the policy names nobody.
        risk_tier: `low`, `medium`, or `high`.
        labels: Free-form labels used by baseline selectors.
        autonomy: How independently the agent acts, and its operating window.
    """

    model_config = _MODEL_CONFIG

    agent: str = Field(pattern=SLUG)
    environment: str = Field(default="dev", pattern=SLUG)
    owner: str | None = None
    risk_tier: RiskTier = "low"
    labels: dict[str, str] = Field(default_factory=dict)
    autonomy: AutonomySettings = Field(default_factory=AutonomySettings)


class AgentPolicy(BaseModel):
    """Grant an agent's permissions in one environment.

    Attributes:
        api_version: Policy schema version. Always `tessera/v1`. Accepts the
            alias `apiVersion`.
        kind: Document kind. Always `AgentPolicy`.
        metadata: Agent identity.
        spec: Permissions, limits, and guardrails.

    Example:
        ```python
        policy = AgentPolicy(
            api_version="tessera/v1",
            kind="AgentPolicy",
            metadata=AgentPolicyMetadata(agent="support-triage", environment="prod"),
        )
        assert policy.spec.guardrails.on_missing == "flag"
        ```
    """

    model_config = _MODEL_CONFIG

    api_version: Literal["tessera/v1"] = Field(alias="apiVersion")
    kind: Literal["AgentPolicy"]
    metadata: AgentPolicyMetadata
    spec: AgentPolicySpec = Field(default_factory=AgentPolicySpec)

    @model_validator(mode="after")
    def _check_control_fields(self) -> AgentPolicy:
        """Reject quotas and reviewers this document cannot carry.

        An agent policy cannot use `scope: org`. A token or cost quota that
        applies to models requires `limits.per_call.max_output_tokens`. An
        `agent:` reviewer is rejected when `risk_tier` is `high`. The v0.1
        shorthand limits do not count as those quotas.

        Returns:
            This policy.

        Raises:
            ValueError: If a quota or reviewer breaks one of those rules.
        """
        _check_quotas(self.spec, allow_org=False)
        if self.metadata.risk_tier == "high":
            for entry in self.spec.approvals.route_to:
                if entry.startswith("agent:"):
                    raise ValueError("agent reviewers are rejected when risk_tier is high")
        return self


class Selector(BaseModel):
    """Choose which agents a baseline applies to.

    Attributes:
        environment: Environment that must match. `None` does not filter.
        risk_tier: Risk tiers that match. Empty does not filter.
        labels: Label keys and values that must all be present on the agent.
        autonomy: Autonomy levels that match. Empty does not filter.
    """

    model_config = _MODEL_CONFIG

    environment: str | None = None
    risk_tier: tuple[RiskTier, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)
    autonomy: tuple[AutonomyLevel, ...] = ()

    def matches(self, metadata: AgentPolicyMetadata) -> bool:
        """Return whether every set criterion applies to an agent.

        An empty selector matches every agent. Environment is compared only
        when set, risk tier only when the list is non-empty, and each label
        only when present. Extra labels on the agent are ignored.

        Args:
            metadata: Agent identity to test.

        Returns:
            `True` when the agent satisfies every criterion this selector sets.

        Example:
            ```python
            selector = Selector(environment="prod")
            agent = AgentPolicyMetadata(
                agent="support-triage",
                environment="prod",
                risk_tier="medium",
            )
            assert selector.matches(agent) is True
            ```
        """
        if self.environment is not None and metadata.environment != self.environment:
            return False
        if self.risk_tier and metadata.risk_tier not in self.risk_tier:
            return False
        if self.autonomy and metadata.autonomy.level not in self.autonomy:
            return False
        return all(metadata.labels.get(key) == value for key, value in self.labels.items())


class BaselinePolicySpec(BaseModel):
    """Restrict agents that match a selector.

    Attributes:
        selector: Agents this baseline applies to.
        models: Extra model denials and approvals.
        tools: Extra tool denials and approvals.
        data: Extra data denials and approvals.
        limits: Upper bounds combined with the agent policy.
        guardrails: Guardrails every matching agent must run.
        require_owner: Whether a matching agent policy must name an owner.
        circuit_breaker: Denial breaker. `None` disables it.
        approvals: Extra approval routing. A baseline may name an `agent:`
            reviewer; the agent's risk tier is checked on the agent policy.
        effects: Effect tags added to matching agents.
        supervision: Extra supervisor requirements. `required` and
            `max_unacknowledged` merge with the agent. `cap_to_supervisor`
            on a baseline is ignored.
    """

    model_config = _MODEL_CONFIG

    selector: Selector = Field(default_factory=Selector)
    models: BaselineResourceRules = Field(default_factory=BaselineResourceRules)
    tools: BaselineResourceRules = Field(default_factory=BaselineResourceRules)
    data: BaselineResourceRules = Field(default_factory=BaselineResourceRules)
    limits: Limits = Field(default_factory=Limits)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    require_owner: bool = False
    circuit_breaker: CircuitBreakerSpec | None = None
    approvals: ApprovalsSpec = Field(default_factory=ApprovalsSpec)
    effects: EffectsSpec = Field(default_factory=EffectsSpec)
    supervision: SupervisionSpec = Field(default_factory=SupervisionSpec)


class BaselinePolicyMetadata(BaseModel):
    """Name a baseline policy.

    Attributes:
        name: Baseline name, using the same shape as an agent name.
    """

    model_config = _MODEL_CONFIG

    name: str = Field(pattern=SLUG)


class BaselinePolicy(BaseModel):
    """Restrict agents across an organization.

    Attributes:
        api_version: Policy schema version. Always `tessera/v1`. Accepts the
            alias `apiVersion`.
        kind: Document kind. Always `BaselinePolicy`.
        metadata: Baseline name.
        spec: Restrictions and the selector that chooses agents.
    """

    model_config = _MODEL_CONFIG

    api_version: Literal["tessera/v1"] = Field(alias="apiVersion")
    kind: Literal["BaselinePolicy"]
    metadata: BaselinePolicyMetadata
    spec: BaselinePolicySpec = Field(default_factory=BaselinePolicySpec)

    @model_validator(mode="after")
    def _check_control_fields(self) -> BaselinePolicy:
        """Reject a token or cost quota that has no model output cap.

        A baseline may use `scope: org`. The v0.1 shorthand limits do not
        count as those quotas.

        Returns:
            This baseline.

        Raises:
            ValueError: If a token or cost quota applies to models and
                `limits.per_call.max_output_tokens` is unset.
        """
        _check_quotas(self.spec, allow_org=True)
        return self


def _check_quotas(spec: AgentPolicySpec | BaselinePolicySpec, *, allow_org: bool) -> None:
    """Reject quotas the document is not allowed to declare.

    Args:
        spec: Agent or baseline spec.
        allow_org: Whether `scope: org` is valid on this document.

    Raises:
        ValueError: If an agent policy uses org scope, or a token or cost
            quota that applies to models has no per-call output cap.
    """
    for quota in spec.limits.quotas:
        if quota.scope == "org" and not allow_org:
            raise ValueError("scope org is only valid on a baseline")
        if _quota_reserves_model_output(quota) and spec.limits.per_call.max_output_tokens is None:
            raise ValueError(
                "limits.per_call.max_output_tokens is required when a token or "
                "cost quota applies to models"
            )


__all__ = [
    "GUARDRAIL_NAME",
    "SLUG",
    "ActiveHoursSpec",
    "AgentPolicy",
    "AgentPolicyMetadata",
    "AgentPolicySpec",
    "AgentRules",
    "ApprovalRule",
    "ApprovalsSpec",
    "AuditSettings",
    "AutonomyLevel",
    "AutonomySettings",
    "BaselinePolicy",
    "BaselinePolicyMetadata",
    "BaselinePolicySpec",
    "BaselineResourceRules",
    "CallerConstraint",
    "CallerConstraintRule",
    "CircuitBreakerSpec",
    "ConcurrencyLimits",
    "Condition",
    "Delegation",
    "EffectTag",
    "EffectsSpec",
    "Guardrails",
    "Limits",
    "OnMissing",
    "Operator",
    "PerCallLimits",
    "PrincipalMode",
    "QuotaMetric",
    "QuotaScope",
    "QuotaSpec",
    "QuotaWindow",
    "ResourceRules",
    "RiskTier",
    "RunLimits",
    "Selector",
    "parse_duration",
]
