"""Background leases for the daily token budget and named quotas."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from agent_tessera.policy.effective import QuotaLimit
from agent_tessera.quotas import server_leased
from agent_tessera.remote.http import RemoteError, request

logger = logging.getLogger("agent_tessera")

_RENEW_INTERVAL = 1.0
_TTL_FRACTION = 0.8


class LeaseKeeper:
    """Hold one token lease and renew it off the decision path.

    `tokens_today` reports `limit - remaining` so evaluation denies a model
    call once the local grant is used up. Renewal runs when less than 20% of
    the grant remains or 80% of its lifetime has elapsed.

    Args:
        http: Open control-plane client.
        token: Returns the current runtime JWT, or `None`.
        limit: Daily token limit from the effective policy.
    """

    def __init__(
        self,
        http: httpx.Client,
        token: Callable[[], str | None],
        limit: int,
    ) -> None:
        """Store the client and the policy limit.

        Args:
            http: Open control-plane client.
            token: Returns the current runtime JWT.
            limit: Daily token limit from the effective policy.
        """
        self._http = http
        self._token = token
        self._limit: int | None = limit
        self._lease_id: str | None = None
        self._granted = 0
        self._remaining = 0
        self._held = 0
        self._used = 0
        self._acquired = False
        self._opened = time.monotonic()
        self._ttl = 0.0
        self._lock = threading.Lock()
        self._call = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Take the first lease, then renew it in the background."""
        self._exchange()
        thread = threading.Thread(target=self._loop, name="tessera-leases", daemon=True)
        thread.start()
        self._thread = thread

    def set_limit(self, limit: int | None) -> None:
        """Use a new daily limit from a replaced policy.

        Args:
            limit: New limit, or `None` when the policy no longer sets one.
        """
        with self._lock:
            self._limit = limit
        self._wake.set()

    def record(self, count: int) -> None:
        """Subtract tokens from the current grant.

        Args:
            count: Tokens just recorded. Zero and negative counts are ignored.
        """
        if count <= 0:
            return
        with self._lock:
            self._used += count
            self._remaining = max(0, self._remaining - count)
            due = self._due()
        if due:
            self._wake.set()

    def try_reserve(self, count: int) -> bool:
        """Hold tokens from the current grant for one in-flight model call.

        Args:
            count: Tokens to hold. Zero succeeds without changing the grant.

        Returns:
            `True` when the grant can cover the hold. The hold is not visible
            to `tokens_today`, so the call that placed it is not denied by it.
        """
        if count <= 0:
            return True
        with self._lock:
            if not self._acquired or self._remaining < count:
                return False
            self._remaining -= count
            self._held += count
            return True

    def release(self, count: int) -> None:
        """Return a hold to the grant.

        Args:
            count: Tokens to release.
        """
        if count <= 0:
            return
        with self._lock:
            give = min(count, self._held)
            self._held -= give
            self._remaining += give

    def reconcile(self, reserved: int, actual: int) -> None:
        """Replace a hold with measured token usage.

        Args:
            reserved: Tokens previously held.
            actual: Tokens to report. The unused part of the hold is returned.
        """
        with self._lock:
            give = min(max(0, reserved), self._held)
            self._held -= give
            self._remaining += give
            spent = max(0, actual)
            self._used += spent
            self._remaining = max(0, self._remaining - spent)
            due = self._due()
        if due:
            self._wake.set()

    def tokens_today(self) -> int | None:
        """Return the usage figure the evaluator should see.

        Returns:
            `limit - remaining - held`, never below zero. The full limit when
            no lease has been acquired yet. `None` when the policy sets no
            daily limit. A hold does not increase this figure.
        """
        with self._lock:
            limit = self._limit
            if limit is None:
                return None
            if not self._acquired:
                return limit
            return max(0, limit - self._remaining - self._held)

    def settle(self) -> None:
        """Stop renewal and report usage for the open lease."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1)
            self._thread = None
        self._exchange()

    def _loop(self) -> None:
        """Renew until the keeper is stopped."""
        while not self._stop.is_set():
            self._wake.wait(_RENEW_INTERVAL)
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                due = self._due()
            if due:
                self._exchange()

    def _due(self) -> bool:
        """Return whether the lease should be renewed. Call with the lock held.

        Returns:
            `True` when there is no grant, the remainder is under 20%, or 80%
            of the lifetime has elapsed.
        """
        if self._limit is None:
            return False
        if not self._acquired or self._granted <= 0:
            return not self._acquired
        if self._remaining * 5 < self._granted:
            return True
        if self._ttl <= 0:
            return False
        return time.monotonic() - self._opened >= self._ttl * _TTL_FRACTION

    def _exchange(self) -> None:
        """Report usage and store the next grant."""
        with self._call:
            access = self._token()
            if access is None:
                return
            with self._lock:
                if self._limit is None and self._lease_id is None:
                    return
                lease_id = self._lease_id
                used = self._used
                self._used = 0
            try:
                response = request(
                    self._http,
                    "POST",
                    "/v1/quotas/leases",
                    json_body={"lease_id": lease_id, "used": used},
                    headers={"Authorization": f"Bearer {access}"},
                )
            except RemoteError:
                self._restore(used)
                return
            if response.status_code != httpx.codes.OK:
                self._restore(used)
                logger.warning("token lease was rejected")
                return
            if not self._apply(response.json()):
                self._restore(used)

    def _restore(self, used: int) -> None:
        """Put unreported usage back after a failed exchange.

        Args:
            used: Usage that was not accepted.
        """
        with self._lock:
            self._used += used

    def _apply(self, body: object) -> bool:
        """Store a grant. Usage recorded during the request stays pending.

        Args:
            body: JSON response object.

        Returns:
            `True` when the body was a lease grant.
        """
        if not isinstance(body, dict):
            return False
        granted = body.get("granted")
        lease_id = body.get("lease_id")
        limit = body.get("limit")
        if isinstance(granted, bool) or not isinstance(granted, int):
            return False
        if lease_id is not None and not isinstance(lease_id, str):
            return False
        opened = time.monotonic()
        ttl = _ttl(body.get("expires_at"))
        with self._lock:
            self._lease_id = lease_id if isinstance(lease_id, str) else None
            self._granted = granted
            self._remaining = max(0, granted - self._used - self._held)
            self._opened = opened
            self._ttl = ttl
            self._acquired = True
            if limit is None or isinstance(limit, int):
                self._limit = limit if isinstance(limit, int) else None
        return True


def _ttl(value: object) -> float:
    """Return the lease lifetime in seconds.

    Args:
        value: `expires_at` from the response.

    Returns:
        Seconds until expiry, or zero when the timestamp cannot be read.
    """
    if not isinstance(value, str):
        return 0.0
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


_COST_SCALE = Decimal(1_000_000)


@dataclass
class _Slot:
    """One named lease and the units still unused in its grant."""

    quota_name: str
    metric: str
    scope: str
    scope_id: str
    maximum: float
    window: str
    limit_units: int
    lease_id: str | None = None
    remaining: int = 0
    held: int = 0
    pending: int = 0
    acquired: bool = False
    opened: float = 0.0
    ttl: float = 0.0
    resource: str | None = None
    principal: str | None = None
    run_id: str | None = None


class QuotaKeeper:
    """Hold server leases for named quotas and renew them off the decision path.

    Agent and org quotas are leased when the policy is installed. Principal
    and run quotas are leased after a call names that id. Until a lease
    arrives, `usage` reports the whole ceiling so evaluation fails closed.
    A supervisor quota has no field on the lease body, so it stays at the
    ceiling.

    Args:
        http: Open control-plane client.
        token: Returns the current runtime JWT, or `None`.
    """

    def __init__(self, http: httpx.Client, token: Callable[[], str | None]) -> None:
        """Store the client.

        Args:
            http: Open control-plane client.
            token: Returns the current runtime JWT.
        """
        self._http = http
        self._token = token
        self._lock = threading.Lock()
        self._call = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._quotas: tuple[QuotaLimit, ...] = ()
        self._slots: dict[tuple[str, str, str], _Slot] = {}

    def start(self) -> None:
        """Lease the agent and org quotas, then renew in the background."""
        self._exchange_ready()
        thread = threading.Thread(target=self._loop, name="tessera-quota-leases", daemon=True)
        thread.start()
        self._thread = thread

    def replace(self, quotas: Sequence[QuotaLimit]) -> None:
        """Keep leases only for the named quotas in a new policy.

        Args:
            quotas: Quotas that should use a server lease.
        """
        kept = tuple(quota for quota in quotas if server_leased(quota))
        with self._lock:
            self._quotas = kept
            names = {quota.name for quota in kept}
            self._slots = {
                key: slot for key, slot in self._slots.items() if slot.quota_name in names
            }
        self._wake.set()

    def settle(self) -> None:
        """Stop renewal and report usage for every open lease."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1)
            self._thread = None
        self._exchange_ready()

    def usage(
        self,
        quota: QuotaLimit,
        *,
        principal: str | None,
        run_id: str | None,
        supervisor: str | None,
    ) -> float:
        """Return consumption the evaluator should compare with `max`.

        Args:
            quota: Named quota.
            principal: Principal on the action.
            run_id: Active run id.
            supervisor: Bound supervisor.

        Returns:
            Consumed units in the quota's own scale. The whole ceiling when
            the lease is not available yet.
        """
        if quota.scope == "supervisor":
            return quota.max
        scope_id = _scope_id(quota, principal, run_id, supervisor)
        if scope_id is None:
            return quota.max
        self._ensure(quota, scope_id, principal, run_id)
        with self._lock:
            slot = self._slots.get((quota.name, quota.scope, scope_id))
            if slot is None or not slot.acquired:
                return quota.max
            consumed = max(0, slot.limit_units - slot.remaining - slot.held)
        return _from_units(quota.metric, consumed)

    def try_reserve(
        self,
        quota: QuotaLimit,
        amount: float,
        *,
        principal: str | None,
        run_id: str | None,
        supervisor: str | None,
        resource: str | None,
    ) -> bool:
        """Hold units of a named quota for one in-flight model call.

        Args:
            quota: Named quota.
            amount: Units to hold, in the quota's own scale.
            principal: Principal on the action.
            run_id: Active run id.
            supervisor: Bound supervisor.
            resource: Model resource, sent when the quota filters resources.

        Returns:
            `True` when the current grant covers the hold.
        """
        units = _to_units(quota.metric, amount)
        if units <= 0:
            return True
        if quota.scope == "supervisor":
            return False
        scope_id = _scope_id(quota, principal, run_id, supervisor)
        if scope_id is None:
            return False
        self._ensure(quota, scope_id, principal, run_id, resource)
        with self._lock:
            slot = self._slots.get((quota.name, quota.scope, scope_id))
            if slot is None or not slot.acquired or slot.remaining < units:
                return False
            slot.remaining -= units
            slot.held += units
            return True

    def release(
        self,
        quota: QuotaLimit,
        amount: float,
        *,
        principal: str | None,
        run_id: str | None,
        supervisor: str | None,
    ) -> None:
        """Return a hold to the grant.

        Args:
            quota: Named quota.
            amount: Units to release, in the quota's own scale.
            principal: Principal on the action.
            run_id: Active run id.
            supervisor: Bound supervisor.
        """
        units = _to_units(quota.metric, amount)
        scope_id = _scope_id(quota, principal, run_id, supervisor)
        if units <= 0 or scope_id is None:
            return
        with self._lock:
            slot = self._slots.get((quota.name, quota.scope, scope_id))
            if slot is None:
                return
            give = min(units, slot.held)
            slot.held -= give
            slot.remaining += give

    def consume(
        self,
        quota: QuotaLimit,
        amount: float,
        *,
        principal: str | None,
        run_id: str | None,
        supervisor: str | None,
        resource: str | None,
    ) -> None:
        """Record measured usage against the grant.

        Args:
            quota: Named quota.
            amount: Units to record, in the quota's own scale.
            principal: Principal on the action.
            run_id: Active run id.
            supervisor: Bound supervisor.
            resource: Resource to send on the next lease, when the quota
                filters resources.
        """
        units = _to_units(quota.metric, amount)
        if units <= 0 or quota.scope == "supervisor":
            return
        scope_id = _scope_id(quota, principal, run_id, supervisor)
        if scope_id is None:
            return
        self._ensure(quota, scope_id, principal, run_id, resource)
        with self._lock:
            slot = self._slots.get((quota.name, quota.scope, scope_id))
            if slot is None:
                return
            slot.pending += units
            slot.remaining = max(0, slot.remaining - units)
            due = _slot_due(slot)
        if due:
            self._wake.set()

    def reconcile(
        self,
        quota: QuotaLimit,
        reserved: float,
        actual: float,
        *,
        principal: str | None,
        run_id: str | None,
        supervisor: str | None,
        resource: str | None,
    ) -> None:
        """Replace a hold with measured usage.

        Args:
            quota: Named quota.
            reserved: Units previously held.
            actual: Units to keep.
            principal: Principal on the action.
            run_id: Active run id.
            supervisor: Bound supervisor.
            resource: Resource to send on the next lease.
        """
        self.release(quota, reserved, principal=principal, run_id=run_id, supervisor=supervisor)
        self.consume(
            quota,
            actual,
            principal=principal,
            run_id=run_id,
            supervisor=supervisor,
            resource=resource,
        )

    def _loop(self) -> None:
        """Renew until the keeper is stopped."""
        while not self._stop.is_set():
            self._wake.wait(_RENEW_INTERVAL)
            self._wake.clear()
            if self._stop.is_set():
                return
            self._exchange_ready()

    def _exchange_ready(self) -> None:
        """Exchange every slot that exists, and open agent and org slots."""
        with self._lock:
            quotas = self._quotas
        for quota in quotas:
            if quota.scope in {"agent", "org"} and quota.resources is None:
                self._ensure(quota, "", None, None)
        with self._lock:
            keys = [
                key for key, slot in self._slots.items() if not slot.acquired or _slot_due(slot)
            ]
        for key in keys:
            self._exchange(key)

    def _ensure(
        self,
        quota: QuotaLimit,
        scope_id: str,
        principal: str | None,
        run_id: str | None,
        resource: str | None = None,
    ) -> None:
        """Create a slot and ask the background thread to fill it.

        Args:
            quota: Named quota.
            scope_id: Principal, run, or supervisor id. Empty for agent and org.
            principal: Principal to send, when the scope needs one.
            run_id: Run id to send, when the scope or window needs one.
            resource: Resource to send when the quota filters resources.
        """
        key = (quota.name, quota.scope, scope_id)
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                slot = _Slot(
                    quota_name=quota.name,
                    metric=quota.metric,
                    scope=quota.scope,
                    scope_id=scope_id,
                    maximum=quota.max,
                    window=quota.window,
                    limit_units=_to_units(quota.metric, quota.max),
                    principal=principal,
                    run_id=run_id if quota.scope == "run" or quota.window == "run" else None,
                    resource=resource,
                )
                self._slots[key] = slot
                fresh = True
            else:
                fresh = not slot.acquired
                if resource is not None:
                    slot.resource = resource
        if fresh:
            self._wake.set()

    def _exchange(self, key: tuple[str, str, str]) -> None:
        """Report one slot's usage and store the next grant.

        Args:
            key: Slot key.
        """
        with self._call:
            access = self._token()
            if access is None:
                return
            with self._lock:
                slot = self._slots.get(key)
                if slot is None:
                    return
                lease_id = slot.lease_id
                used = slot.pending
                slot.pending = 0
                body = _body(slot, lease_id, used)
            try:
                response = request(
                    self._http,
                    "POST",
                    "/v1/quotas/leases",
                    json_body=body,
                    headers={"Authorization": f"Bearer {access}"},
                )
            except RemoteError:
                self._restore(key, used)
                return
            if response.status_code != httpx.codes.OK:
                self._restore(key, used)
                logger.warning("quota lease was rejected")
                return
            if not self._apply(key, response.json()):
                self._restore(key, used)

    def _restore(self, key: tuple[str, str, str], used: int) -> None:
        """Put unreported usage back after a failed exchange.

        Args:
            key: Slot key.
            used: Usage that was not accepted.
        """
        with self._lock:
            slot = self._slots.get(key)
            if slot is not None:
                slot.pending += used

    def _apply(self, key: tuple[str, str, str], body: object) -> bool:
        """Store a grant. Usage recorded during the request stays pending.

        Args:
            key: Slot key.
            body: JSON response object.

        Returns:
            `True` when the body was a lease grant.
        """
        if not isinstance(body, dict):
            return False
        granted = body.get("granted")
        lease_id = body.get("lease_id")
        limit = body.get("limit")
        if isinstance(granted, bool) or not isinstance(granted, int):
            return False
        if lease_id is not None and not isinstance(lease_id, str):
            return False
        opened = time.monotonic()
        ttl = _ttl(body.get("expires_at"))
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return False
            slot.lease_id = lease_id if isinstance(lease_id, str) else None
            slot.remaining = max(0, granted - slot.pending - slot.held)
            slot.opened = opened
            slot.ttl = ttl
            slot.acquired = True
            if isinstance(limit, int) and not isinstance(limit, bool):
                slot.limit_units = limit
        return True


def _body(slot: _Slot, lease_id: str | None, used: int) -> dict[str, object]:
    """Build the lease request for one slot.

    Args:
        slot: Slot being renewed.
        lease_id: Previous lease, or `None`.
        used: Units to report.

    Returns:
        The JSON body.
    """
    body: dict[str, object] = {
        "lease_id": lease_id,
        "used": used,
        "metric": slot.metric,
        "quota_name": slot.quota_name,
        "requested": None,
    }
    if slot.resource is not None:
        body["resource"] = slot.resource
    if slot.scope == "principal" and slot.principal is not None:
        body["principal"] = slot.principal
    if slot.run_id is not None:
        body["run_id"] = slot.run_id
    return body


def _scope_id(
    quota: QuotaLimit, principal: str | None, run_id: str | None, supervisor: str | None
) -> str | None:
    """Return the id that shares a server counter.

    Args:
        quota: Named quota.
        principal: Principal on the action.
        run_id: Active run id.
        supervisor: Bound supervisor.

    Returns:
        The id, an empty string for agent and org, or `None` when the id is
        missing.
    """
    if quota.scope in {"agent", "org"}:
        return ""
    if quota.scope == "principal":
        return principal or None
    if quota.scope == "run":
        return run_id or None
    if quota.scope == "supervisor":
        return supervisor or None
    return None


def _to_units(metric: str, amount: float) -> int:
    """Convert a metric amount into the integer units the server stores.

    Args:
        metric: Quota metric.
        amount: Amount in the quota's own scale.

    Returns:
        Whole units. `cost_usd` is millionths of a dollar. Any positive amount
        is at least one unit.
    """
    if amount <= 0:
        return 0
    if metric == "cost_usd":
        units = int(Decimal(str(amount)) * _COST_SCALE)
        return max(units, 1)
    return max(1, math.ceil(amount))


def _from_units(metric: str, units: int) -> float:
    """Convert server units back to the quota's scale.

    Args:
        metric: Quota metric.
        units: Integer units.

    Returns:
        Dollars for `cost_usd`, otherwise the same integer as a float.
    """
    if metric == "cost_usd":
        return float(Decimal(units) / _COST_SCALE)
    return float(units)


def _slot_due(slot: _Slot) -> bool:
    """Return whether a slot should be renewed. Call with the lock held.

    Args:
        slot: Slot to inspect.

    Returns:
        `True` when there is no grant, the remainder is under 20%, or 80% of
        the lifetime has elapsed.
    """
    if not slot.acquired or slot.remaining <= 0:
        return not slot.acquired or slot.pending > 0
    if slot.remaining * 5 < max(slot.limit_units, 1):
        return True
    if slot.ttl <= 0:
        return False
    return time.monotonic() - slot.opened >= slot.ttl * _TTL_FRACTION


__all__ = ["LeaseKeeper", "QuotaKeeper"]
