"""Token leases and named quota leases shared across instances."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import EffectivePolicy, QuotaLimit, build_effective_policy
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.matching import matches_pattern
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import (
    Agent,
    Baseline,
    Event,
    Instance,
    Policy,
    QuotaCounter,
    QuotaLease,
    TokenCounter,
    TokenLease,
    utcnow,
)
from tessera_server.db.session import advisory_xact_lock
from tessera_server.ids import new_ulid
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.registry import transition_agent
from tessera_server.services.revocations import revoke_run

_LOCK = threading.Lock()
_QUOTA_LOCK = 7402
_SLICE_FLOOR = 1000
_COUNT_FLOOR = 1
_COST_SCALE = 1_000_000
_TOKEN_METRICS = frozenset({"tokens", "input_tokens", "output_tokens"})
_LEGACY_METRIC = "tokens"
_LEGACY_QUOTA = "tokens_per_day"


def grant_lease(
    session: Session,
    settings: Settings,
    token: str,
    *,
    lease_id: str | None,
    used: int,
    metric: str = _LEGACY_METRIC,
    quota_name: str = _LEGACY_QUOTA,
    resource: str | None = None,
    principal: str | None = None,
    run_id: str | None = None,
    requested: int | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Close a previous lease, if any, and grant the next slice of a budget.

    A request that omits `metric` and `quota_name` keeps the v0.2 daily token
    lease. Any other name reads that quota from the effective policy.

    Args:
        session: Open database session.
        settings: Issuer and lease lifetime.
        token: Runtime JWT for the calling instance.
        lease_id: Previous lease to close, or `None` on the first call.
        used: Units consumed under that lease. Cost quotas count millionths
            of a dollar. Every other metric counts whole units.
        metric: Quota metric. Defaults to `tokens`.
        quota_name: Quota name. Defaults to `tokens_per_day`.
        resource: Resource the caller is about to use. Required when the
            quota lists resource globs.
        principal: Principal id for a principal-scoped quota.
        run_id: Run id for a run-scoped quota or a `run` window.
        requested: Upper bound on this grant, in the same units as `used`.
            `None` asks for the normal slice.
        now: Clock for expiry, the window bucket, and abandoned leases.

    Returns:
        The lease id (or `None`), the grant, the budget still unreserved,
        its expiry, the UTC day, and the limit. `limit` and `remaining` are
        `None` when the daily token shorthand sets no budget.

    Raises:
        ApiError: Status 401 when the token is invalid, 403 when the instance
            is revoked or the agent is not active, 404 when the lease or quota
            is missing, 409 when that lease is already closed, or 422 when the
            quota cannot be leased.
    """
    moment = (now or utcnow)()
    with _LOCK:
        try:
            advisory_xact_lock(session, _QUOTA_LOCK)
            result = _grant(
                session,
                settings,
                token,
                lease_id=lease_id,
                used=used,
                metric=metric,
                quota_name=quota_name,
                resource=resource,
                principal=principal,
                run_id=run_id,
                requested=requested,
                moment=moment,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        return result


def _grant(
    session: Session,
    settings: Settings,
    token: str,
    *,
    lease_id: str | None,
    used: int,
    metric: str,
    quota_name: str,
    resource: str | None,
    principal: str | None,
    run_id: str | None,
    requested: int | None,
    moment: datetime,
) -> dict[str, object]:
    """Apply closes and reserve the next grant.

    Args:
        session: Open database session.
        settings: Lease lifetime.
        token: Runtime JWT.
        lease_id: Previous lease, or `None`.
        used: Reported consumption.
        metric: Quota metric.
        quota_name: Quota name.
        resource: Resource the caller is about to use.
        principal: Principal id, when the quota is principal-scoped.
        run_id: Run id, when the quota needs one.
        requested: Upper bound on the grant, or `None`.
        moment: Current time.

    Returns:
        The lease response.
    """
    agent, instance_id = _caller(session, settings, token, moment)
    _abandon(session, agent.id, lease_id, moment)
    _abandon_named(session, agent.id, lease_id, moment)
    if lease_id is not None:
        _close_any(session, lease_id, agent.id, instance_id, used)
    if metric == _LEGACY_METRIC and quota_name == _LEGACY_QUOTA:
        return _grant_daily(
            session, settings, agent, instance_id, requested=requested, moment=moment
        )
    return _grant_named(
        session,
        settings,
        agent,
        instance_id,
        metric=metric,
        quota_name=quota_name,
        resource=resource,
        principal=principal,
        run_id=run_id,
        requested=requested,
        moment=moment,
    )


def _grant_daily(
    session: Session,
    settings: Settings,
    agent: Agent,
    instance_id: str,
    *,
    requested: int | None,
    moment: datetime,
) -> dict[str, object]:
    """Reserve the next slice of the daily token shorthand.

    Args:
        session: Open database session.
        settings: Lease lifetime.
        agent: Calling agent.
        instance_id: Calling instance.
        requested: Upper bound on the grant, or `None`.
        moment: Current time.

    Returns:
        The lease response. `remaining` is what the shared budget still has
        after this reservation.
    """
    limit = _limit(session, agent)
    day = _aware(moment).date()
    expires_at = _aware(moment) + timedelta(seconds=settings.lease_ttl)
    if limit is None:
        return _response(None, 0, expires_at, day, None, remaining=None)
    counter = _counter(session, agent.id, day)
    granted = _slice(limit, counter)
    if requested is not None:
        granted = min(granted, requested)
    if granted == 0:
        return _response(None, 0, expires_at, day, limit, remaining=_left(limit, counter))
    row = TokenLease(
        id=new_ulid(),
        agent_id=agent.id,
        instance_id=instance_id,
        day=day,
        granted=granted,
        expires_at=expires_at,
        closed=False,
    )
    session.add(row)
    counter.reserved += granted
    return _response(row.id, granted, expires_at, day, limit, remaining=_left(limit, counter))


def _grant_named(
    session: Session,
    settings: Settings,
    agent: Agent,
    instance_id: str,
    *,
    metric: str,
    quota_name: str,
    resource: str | None,
    principal: str | None,
    run_id: str | None,
    requested: int | None,
    moment: datetime,
) -> dict[str, object]:
    """Reserve the next slice of one named quota.

    Args:
        session: Open database session.
        settings: Lease lifetime.
        agent: Calling agent.
        instance_id: Calling instance.
        metric: Metric the caller asked to lease.
        quota_name: Quota to lease.
        resource: Resource the caller is about to use.
        principal: Principal id, when the quota is principal-scoped.
        run_id: Run id, when the quota needs one.
        requested: Upper bound on the grant, or `None`.
        moment: Current time.

    Returns:
        The lease response. `scope: instance` and `window: call` return an
        empty grant whose `remaining` is the whole budget.

    Raises:
        ApiError: Status 404 when the quota is missing, or 422 when it cannot
            be leased.
    """
    quota = _named_quota(session, agent, quota_name)
    if metric != quota.metric:
        raise ApiError(422, "invalid_request", "metric does not match this quota")
    _covers(quota, resource)
    day = _aware(moment).date()
    expires_at = _aware(moment) + timedelta(seconds=settings.lease_ttl)
    units = _budget_units(quota.metric, quota.max)
    if quota.scope == "instance" or quota.window == "call":
        return _response(None, 0, expires_at, day, units, remaining=units)
    scope_key = _scope_key(quota, agent.id, principal, run_id)
    bucket = _bucket(quota.window, moment, run_id)
    counter = _named_counter(session, scope_key, quota.name, bucket)
    if units - counter.used - counter.reserved <= 0:
        if quota.on_exhausted == "suspend":
            _suspend(session, agent.id, quota.name, moment)
        return _response(None, 0, expires_at, day, units, remaining=0)
    granted = _named_slice(units, counter, quota.metric, requested)
    if granted == 0:
        return _response(None, 0, expires_at, day, units, remaining=_left(units, counter))
    row = QuotaLease(
        id=new_ulid(),
        agent_id=agent.id,
        instance_id=instance_id,
        scope_key=scope_key,
        quota_name=quota.name,
        bucket=bucket,
        granted=granted,
        expires_at=expires_at,
        closed=False,
    )
    session.add(row)
    counter.reserved += granted
    return _response(row.id, granted, expires_at, day, units, remaining=_left(units, counter))


def _caller(
    session: Session, settings: Settings, token: str, moment: datetime
) -> tuple[Agent, str]:
    """Resolve the agent and instance for a runtime token.

    Args:
        session: Open database session.
        settings: Expected issuer.
        token: Compact JWT.
        moment: Current time.

    Returns:
        The agent and its instance id.

    Raises:
        ApiError: Status 401, or 403 when the instance is revoked or the agent
            is not active.
    """
    try:
        claims = verify_runtime_token(session, settings, token, now=lambda: moment)
    except ValueError as err:
        raise ApiError(401, "invalid_token", "runtime token is not valid") from err
    agent_id = claims.get("agent_id")
    instance_id = claims.get("inst")
    if not isinstance(agent_id, str) or not isinstance(instance_id, str):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    agent = session.get(Agent, agent_id)
    instance = session.get(Instance, instance_id)
    if agent is None or instance is None or instance.agent_id != agent.id:
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    if instance.revoked:
        raise ApiError(403, "instance_revoked", "instance is revoked")
    if agent.status != "active":
        raise ApiError(403, f"agent_{agent.status}", f"agent is {agent.status}")
    return agent, instance_id


def _abandon(session: Session, agent_id: str, named: str | None, moment: datetime) -> None:
    """Count every expired open lease, other than the one being reported, as fully used.

    Args:
        session: Open database session.
        agent_id: Agent whose leases to sweep.
        named: Lease the caller is closing explicitly, which keeps its report.
        moment: Current time.
    """
    rows = session.scalars(
        select(TokenLease).where(TokenLease.agent_id == agent_id, TokenLease.closed.is_(False))
    ).all()
    current = _aware(moment)
    for row in rows:
        if row.id == named or _aware(row.expires_at) > current:
            continue
        _add_used(session, row, row.granted)
        row.closed = True


def _abandon_named(session: Session, agent_id: str, named: str | None, moment: datetime) -> None:
    """Count every expired open named lease, other than the one being reported, as fully used.

    Args:
        session: Open database session.
        agent_id: Agent whose leases to sweep.
        named: Lease the caller is closing explicitly, which keeps its report.
        moment: Current time.
    """
    rows = session.scalars(
        select(QuotaLease).where(QuotaLease.agent_id == agent_id, QuotaLease.closed.is_(False))
    ).all()
    current = _aware(moment)
    for row in rows:
        if row.id == named or _aware(row.expires_at) > current:
            continue
        _add_named_used(session, row, row.granted)
        row.closed = True


def _close_any(session: Session, lease_id: str, agent_id: str, instance_id: str, used: int) -> None:
    """Close whichever lease table holds this id.

    Args:
        session: Open database session.
        lease_id: Lease to close.
        agent_id: Calling agent.
        instance_id: Calling instance.
        used: Units to add to that lease's counter.

    Raises:
        ApiError: Status 404 when the lease is not this instance's, or 409
            when it is already closed.
    """
    if session.get(TokenLease, lease_id) is not None:
        _close(session, lease_id, agent_id, instance_id, used)
        return
    _close_named(session, lease_id, agent_id, instance_id, used)


def _close(session: Session, lease_id: str, agent_id: str, instance_id: str, used: int) -> None:
    """Close one lease with the caller's reported usage.

    Args:
        session: Open database session.
        lease_id: Lease to close.
        agent_id: Calling agent.
        instance_id: Calling instance.
        used: Tokens to add to that lease's day.

    Raises:
        ApiError: Status 404 when the lease is not this instance's, or 409
            when it is already closed.
    """
    row = session.get(TokenLease, lease_id)
    if row is None or row.agent_id != agent_id or row.instance_id != instance_id:
        raise ApiError(404, "not_found", "lease not found")
    if row.closed:
        raise ApiError(409, "conflict", "lease is already closed")
    _add_used(session, row, used)
    row.closed = True


def _add_used(session: Session, row: TokenLease, used: int) -> None:
    """Move a grant from reserved to used on the lease's day.

    Args:
        session: Open database session.
        row: Lease being closed.
        used: Tokens to record. Reserved falls by the original grant.
    """
    counter = _counter(session, row.agent_id, row.day)
    counter.used += used
    counter.reserved = max(0, counter.reserved - row.granted)


def _close_named(
    session: Session, lease_id: str, agent_id: str, instance_id: str, used: int
) -> None:
    """Close one named lease with the caller's reported usage.

    Args:
        session: Open database session.
        lease_id: Lease to close.
        agent_id: Calling agent.
        instance_id: Calling instance.
        used: Units to add to that lease's bucket.

    Raises:
        ApiError: Status 404 when the lease is not this instance's, or 409
            when it is already closed.
    """
    row = session.get(QuotaLease, lease_id)
    if row is None or row.agent_id != agent_id or row.instance_id != instance_id:
        raise ApiError(404, "not_found", "lease not found")
    if row.closed:
        raise ApiError(409, "conflict", "lease is already closed")
    _add_named_used(session, row, used)
    row.closed = True


def _add_named_used(session: Session, row: QuotaLease, used: int) -> None:
    """Move a grant from reserved to used on the lease's bucket.

    Args:
        session: Open database session.
        row: Lease being closed.
        used: Units to record. Reserved falls by the original grant.
    """
    counter = _named_counter(session, row.scope_key, row.quota_name, row.bucket)
    counter.used += used
    counter.reserved = max(0, counter.reserved - row.granted)


def _slice(limit: int, counter: TokenCounter) -> int:
    """Return the next grant for a daily budget.

    Args:
        limit: Daily token limit.
        counter: Today's used and reserved tokens.

    Returns:
        A non-negative grant no larger than the remaining budget.
    """
    window = max(_SLICE_FLOOR, (limit * 5 + 99) // 100)
    remaining = limit - counter.used - counter.reserved
    if remaining <= 0:
        return 0
    return min(remaining, window)


def _named_slice(limit: int, counter: QuotaCounter, metric: str, requested: int | None) -> int:
    """Return the next grant for a named quota.

    Args:
        limit: Budget in integer units.
        counter: Used and reserved units in this bucket.
        metric: Metric, which chooses the slice floor.
        requested: Upper bound on the grant, or `None`.

    Returns:
        A non-negative grant no larger than the remaining budget.
    """
    floor = _SLICE_FLOOR if metric in _TOKEN_METRICS else _COUNT_FLOOR
    window = max(floor, (limit * 5 + 99) // 100)
    remaining = limit - counter.used - counter.reserved
    if remaining <= 0:
        return 0
    grant = min(remaining, window)
    if requested is not None:
        grant = min(grant, requested)
    return grant


def _limit(session: Session, agent: Agent) -> int | None:
    """Read the agent's effective daily token limit.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.

    Returns:
        The limit, or `None` when the policy does not set one.

    Raises:
        ApiError: Status 422 when the stored policy cannot be built.
    """
    effective = _effective(session, agent)
    if effective is None or effective.tokens_per_day is None:
        return None
    return int(effective.tokens_per_day.value)


def _effective(session: Session, agent: Agent) -> EffectivePolicy | None:
    """Build the agent's effective policy.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.

    Returns:
        The effective policy, or `None` when the agent has no stored policy.

    Raises:
        ApiError: Status 422 when the stored policy cannot be built.
    """
    if agent.policy_id is None:
        return None
    policy = session.get(Policy, agent.policy_id)
    if policy is None:
        return None
    try:
        document = parse_policy(_one_document(policy.yaml))
        baselines = _baselines(session)
        if not isinstance(document, AgentPolicy):
            raise PolicyError("expected an AgentPolicy")
        return build_effective_policy(document, baselines)
    except PolicyError as err:
        raise ApiError(422, "invalid_policy", str(err)) from err


def _named_quota(session: Session, agent: Agent, name: str) -> QuotaLimit:
    """Return one named quota from the effective policy.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.
        name: Quota name.

    Returns:
        The first quota with that name.

    Raises:
        ApiError: Status 404 when the policy has no such quota, or 422 when
            the policy cannot be built.
    """
    effective = _effective(session, agent)
    if effective is not None:
        for quota in effective.controls.quotas:
            if quota.name == name:
                return quota
    raise ApiError(404, "not_found", "quota not found")


def _covers(quota: QuotaLimit, resource: str | None) -> None:
    """Reject a resource the quota does not count.

    Args:
        quota: Quota being leased.
        resource: Resource the caller is about to use.

    Raises:
        ApiError: Status 422 when the quota lists globs and the resource is
            missing or matches none of them.
    """
    if quota.resources is None:
        return
    if resource is not None and any(
        matches_pattern(resource, pattern) for pattern in quota.resources
    ):
        return
    raise ApiError(422, "invalid_request", "resource is not covered by this quota")


def _scope_key(quota: QuotaLimit, agent_id: str, principal: str | None, run_id: str | None) -> str:
    """Return the counter key for a quota scope.

    Args:
        quota: Quota being leased.
        agent_id: Calling agent.
        principal: Principal id from the request.
        run_id: Run id from the request.

    Returns:
        A key shared by every caller that draws on the same budget. `agent`
        and `org` are both keyed by the agent.

    Raises:
        ApiError: Status 422 `quota_unavailable` when the scope id is missing.
    """
    if quota.scope in {"agent", "org"}:
        return f"agent:{agent_id}"
    if quota.scope == "principal":
        if not principal:
            raise ApiError(422, "quota_unavailable", "principal is required")
        return f"principal:{principal}"
    if quota.scope == "supervisor":
        raise ApiError(422, "quota_unavailable", "supervisor is required")
    if not run_id:
        raise ApiError(422, "quota_unavailable", "run_id is required")
    return f"run:{run_id}"


def _bucket(window: str, moment: datetime, run_id: str | None) -> str:
    """Return the counter bucket for a quota window.

    Minute and hour windows use the UTC calendar bucket that contains
    `moment`. Day and month do the same. A `run` window uses the run id.

    Args:
        window: Quota window.
        moment: Current time.
        run_id: Run id from the request.

    Returns:
        The bucket name.

    Raises:
        ApiError: Status 422 `quota_unavailable` when a run window has no run id.
    """
    if window == "run":
        if not run_id:
            raise ApiError(422, "quota_unavailable", "run_id is required")
        return f"run:{run_id}"
    aware = _aware(moment)
    if window == "month":
        return f"{aware.year:04d}-{aware.month:02d}"
    if window == "hour":
        return aware.strftime("%Y-%m-%dT%H")
    if window == "minute":
        return aware.strftime("%Y-%m-%dT%H:%M")
    return aware.date().isoformat()


def _budget_units(metric: str, amount: float) -> int:
    """Convert a quota ceiling into integer counter units.

    Args:
        metric: Quota metric.
        amount: Ceiling from the policy.

    Returns:
        `floor(amount)` for every metric except `cost_usd`, which is
        millionths of a dollar.
    """
    if metric == "cost_usd":
        return int(Decimal(str(amount)) * _COST_SCALE)
    return math.floor(amount)


def _suspend(session: Session, agent_id: str, quota_name: str, moment: datetime) -> None:
    """Suspend an agent the same way an admin suspend does.

    Args:
        session: Open database session.
        agent_id: Agent to suspend.
        quota_name: Quota whose budget is empty.
        moment: Clock for the revocation and audit rows.
    """
    transition_agent(
        session,
        agent_id,
        "suspend",
        actor=f"quota:{quota_name}",
        now=lambda: moment,
    )


def _baselines(session: Session) -> list[BaselinePolicy]:
    """Load stored baselines in name order.

    Args:
        session: Open database session.

    Returns:
        Parsed baseline policies.
    """
    parsed: list[BaselinePolicy] = []
    rows = session.scalars(select(Baseline).order_by(Baseline.name)).all()
    for row in rows:
        document = parse_policy(_one_document(row.yaml))
        if isinstance(document, BaselinePolicy):
            parsed.append(document)
    return parsed


def _named_counter(session: Session, scope_key: str, quota_name: str, bucket: str) -> QuotaCounter:
    """Return the counter for one quota bucket, creating it at zero.

    Args:
        session: Open database session.
        scope_key: Who shares the counter.
        quota_name: Quota name.
        bucket: Window bucket.

    Returns:
        The counter row.
    """
    key = {"scope_key": scope_key, "quota_name": quota_name, "bucket": bucket}
    row = session.get(QuotaCounter, key)
    if row is None:
        row = QuotaCounter(
            scope_key=scope_key, quota_name=quota_name, bucket=bucket, used=0, reserved=0
        )
        session.add(row)
        session.flush()
    return row


def _counter(session: Session, agent_id: str, day: date) -> TokenCounter:
    """Return the counter for an agent and day, creating it at zero.

    Args:
        session: Open database session.
        agent_id: Agent the counter belongs to.
        day: UTC day.

    Returns:
        The counter row.
    """
    row = session.get(TokenCounter, {"agent_id": agent_id, "day": day})
    if row is None:
        row = TokenCounter(agent_id=agent_id, day=day, used=0, reserved=0)
        session.add(row)
        session.flush()
    return row


def _one_document(text: str) -> object:
    """Load the only YAML document in a stored policy.

    Args:
        text: YAML text.

    Returns:
        The document.

    Raises:
        PolicyError: If the YAML is invalid or is not exactly one document.
    """
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as err:
        raise PolicyError(f"invalid YAML: {err}") from err
    if len(documents) != 1:
        found = len(documents)
        raise PolicyError(f"expected exactly one YAML document, found {found}")
    return documents[0]


def _left(limit: int, counter: TokenCounter | QuotaCounter) -> int:
    """Return the unreserved remainder of a budget.

    Args:
        limit: Budget in integer units.
        counter: Used and reserved units.

    Returns:
        The remainder, never negative.
    """
    return max(0, limit - counter.used - counter.reserved)


def _response(
    lease_id: str | None,
    granted: int,
    expires_at: datetime,
    day: date,
    limit: int | None,
    *,
    remaining: int | None,
) -> dict[str, object]:
    """Build the lease response.

    Args:
        lease_id: New lease, or `None` when nothing was reserved.
        granted: Units reserved.
        expires_at: When an abandoned lease counts as fully used.
        day: UTC day the grant belongs to.
        limit: Budget, or `None` when the daily token shorthand sets none.
        remaining: Budget still unreserved after this grant.

    Returns:
        The response document. `day` and `limit` stay so a v0.2 client can
        read the daily token budget.
    """
    return {
        "lease_id": lease_id,
        "granted": granted,
        "remaining": remaining,
        "expires_at": _iso(expires_at),
        "day": day.isoformat(),
        "limit": limit,
    }


def _aware(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    Args:
        moment: Stored or injected time.

    Returns:
        A timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    """Format a timestamp as UTC ISO-8601 with millisecond precision.

    Args:
        moment: Time to format.

    Returns:
        A string ending in `Z`.
    """
    return _aware(moment).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def trip_circuit(session: Session, agent_id: str, moment: datetime) -> None:
    """Suspend the agent or revoke the run when the breaker is open.

    Args:
        session: Open database session. The denial events are already stored.
        agent_id: Agent whose recent decisions to count.
        moment: End of the sliding window.

    Example:
        ```python
        trip_circuit(session, agent.id, datetime.now(UTC))
        ```
    """
    agent = session.get(Agent, agent_id)
    if agent is None or agent.status != "active":
        return
    effective = _effective(session, agent)
    breaker = None if effective is None else effective.controls.circuit_breaker
    if breaker is None:
        return
    cutoff = moment - timedelta(seconds=breaker.window_seconds)
    rows = session.scalars(
        select(Event)
        .where(Event.agent_id == agent.id, Event.event_type == "decision", Event.ts >= cutoff)
        .order_by(Event.ts.desc())
    ).all()
    counted = 0
    run_id: str | None = None
    for row in rows:
        if not _counts_as_denial(row):
            continue
        counted += 1
        if run_id is None and row.task is not None:
            run_id = row.task
    if counted < breaker.denials:
        return
    try:
        if breaker.action == "revoke_run":
            if run_id is not None:
                revoke_run(session, run_id, actor="circuit", now=lambda: moment)
            return
        transition_agent(session, agent.id, "suspend", actor="circuit", now=lambda: moment)
    except ApiError:
        return


def _counts_as_denial(row: Event) -> bool:
    """Return whether a stored decision counts toward the breaker.

    Args:
        row: Decision event.

    Returns:
        `True` when the effect is `deny` and the reason is not `circuit_open`.
    """
    payload = row.payload
    if payload.get("effect") != "deny":
        return False
    return payload.get("reason") != "circuit_open"


def list_quota_snapshot(
    session: Session,
    *,
    environment: str | None = None,
    agent_id: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Return current usage vs ceilings for agent-scoped quotas.

    Args:
        session: Open database session.
        environment: Optional exact environment filter.
        agent_id: Optional single agent filter.
        now: Clock for the current window bucket.

    Returns:
        `{items: [...]}` where each item is
        `{agent_id, name, metric, window, scope, scope_key, max, used,
        outstanding, window_start}`. Agents without a readable policy
        contribute no rows. Principal/run/instance/call-scoped quotas are
        listed with `used`/`outstanding` of 0 and an empty `scope_key`
        (usage is not aggregated org-wide).
    """
    moment = (now or utcnow)()
    query = select(Agent).order_by(Agent.name, Agent.environment)
    if agent_id is not None:
        query = query.where(Agent.id == agent_id)
    if environment is not None:
        query = query.where(Agent.environment == environment)
    items: list[dict[str, object]] = []
    for agent in session.scalars(query).all():
        try:
            effective = _effective(session, agent)
        except ApiError:
            continue
        if effective is None:
            continue
        if effective.tokens_per_day is not None:
            day = _aware(moment).date()
            token_counter = session.get(TokenCounter, {"agent_id": agent.id, "day": day})
            used = int(token_counter.used) if token_counter is not None else 0
            outstanding = int(token_counter.reserved) if token_counter is not None else 0
            items.append(
                {
                    "agent_id": agent.id,
                    "name": _LEGACY_QUOTA,
                    "metric": _LEGACY_METRIC,
                    "window": "day",
                    "scope": "agent",
                    "scope_key": f"agent:{agent.id}",
                    "max": int(effective.tokens_per_day.value),
                    "used": used,
                    "outstanding": outstanding,
                    "window_start": day.isoformat(),
                }
            )
        for quota in effective.controls.quotas:
            units = _budget_units(quota.metric, quota.max)
            if quota.scope in {"agent", "org"} and quota.window not in {"call", "run"}:
                scope_key = f"agent:{agent.id}"
                bucket = _bucket(quota.window, moment, None)
                named_counter = session.get(
                    QuotaCounter,
                    {
                        "scope_key": scope_key,
                        "quota_name": quota.name,
                        "bucket": bucket,
                    },
                )
                used = int(named_counter.used) if named_counter is not None else 0
                outstanding = int(named_counter.reserved) if named_counter is not None else 0
                window_start = bucket
            else:
                scope_key = ""
                used = 0
                outstanding = 0
                window_start = ""
            items.append(
                {
                    "agent_id": agent.id,
                    "name": quota.name,
                    "metric": quota.metric,
                    "window": quota.window,
                    "scope": quota.scope,
                    "scope_key": scope_key,
                    "max": units,
                    "used": used,
                    "outstanding": outstanding,
                    "window_start": window_start,
                }
            )
    return {"items": items}


__all__ = ["grant_lease", "list_quota_snapshot", "trip_circuit"]
