"""Local quota counters and the model-call reservation estimate.

Instance-scoped quotas, and every quota while the SDK is offline, are counted
here. A server lease is used for the other scopes. The reservation for a model
call is `ceil(estimate_input * 1.1) + max_output_tokens`, where
`estimate_input` is `max(1, ceil(text_chars / 4))`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from threading import Lock

from agent_tessera.policy.effective import QuotaLimit
from agent_tessera.policy.matching import matches_pattern
from agent_tessera.types import Action, ActionKind

_TEXT_DEPTH = 6
_SERVER_SCOPES = frozenset({"agent", "org", "principal", "supervisor", "run"})
_COUNT_METRICS = frozenset(
    {"model_calls", "tool_calls", "agent_calls", "spawns", "steps", "effects"}
)


def estimate_input_tokens(text_chars: int) -> int:
    """Estimate input tokens from a character count.

    Args:
        text_chars: Characters of prompt text. Zero still estimates one token.

    Returns:
        `max(1, ceil(text_chars / 4))`.

    Example:
        ```python
        from agent_tessera.quotas import estimate_input_tokens

        assert estimate_input_tokens(8) == 2
        assert estimate_input_tokens(0) == 1
        ```
    """
    if text_chars <= 0:
        return 1
    return max(1, math.ceil(text_chars / 4))


def reserved_input_tokens(text_chars: int) -> int:
    """Return the input side of a model reservation.

    Args:
        text_chars: Characters of prompt text.

    Returns:
        `ceil(estimate_input_tokens(text_chars) * 1.1)`.
    """
    return math.ceil(estimate_input_tokens(text_chars) * 1.1)


def model_reservation(text_chars: int, max_output_tokens: int) -> int:
    """Return the tokens held before a model call.

    Args:
        text_chars: Characters of prompt text.
        max_output_tokens: Policy output cap. Zero when the policy sets none.

    Returns:
        The input reservation plus the output cap.

    Example:
        ```python
        from agent_tessera.quotas import model_reservation

        assert model_reservation(0, 100) == 102
        ```
    """
    return reserved_input_tokens(text_chars) + max(0, max_output_tokens)


def text_chars(value: object) -> int:
    """Count characters in strings nested inside a call's arguments.

    Args:
        value: Arguments or keyword arguments. Mappings and sequences are
            walked. Other objects contribute nothing.

    Returns:
        The total character count, stopping after six levels of nesting.
    """
    return _chars(value, 0)


def quota_applies(quota: QuotaLimit, action: Action) -> bool:
    """Return whether a quota counts an action.

    Args:
        quota: Quota from the effective policy.
        action: Action being governed.

    Returns:
        `True` when the kind and resource filters match.
    """
    if quota.kinds is not None and action.kind not in quota.kinds:
        return False
    if quota.resources is None:
        return True
    return any(matches_pattern(action.resource, pattern) for pattern in quota.resources)


def server_leased(quota: QuotaLimit) -> bool:
    """Return whether the server, not this process, holds the counter.

    Args:
        quota: Quota from the effective policy.

    Returns:
        `True` for an agent, org, principal, supervisor, or run quota whose
        window is not `call`.
    """
    return quota.scope in _SERVER_SCOPES and quota.window != "call"


def count_amount(metric: str, kind: ActionKind) -> float:
    """Return how many units one allowed call adds to a count metric.

    Args:
        metric: Quota metric.
        kind: Kind of the allowed action.

    Returns:
        `1` when the metric counts this kind, otherwise `0`.
    """
    if metric not in _COUNT_METRICS:
        return 0
    if metric == "model_calls":
        return 1 if kind is ActionKind.MODEL else 0
    if metric == "tool_calls":
        return 1 if kind is ActionKind.TOOL else 0
    if metric in {"agent_calls", "spawns"}:
        return 1 if kind is ActionKind.AGENT else 0
    if metric == "steps":
        return 1 if kind in {ActionKind.MODEL, ActionKind.TOOL} else 0
    return 1


def usage_amount(
    metric: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> float:
    """Return the recorded size of one model call for a metric.

    Args:
        metric: Quota metric.
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.
        cost_usd: Estimated cost in dollars.

    Returns:
        The amount to add. Count metrics return `0` here; they are counted
        when the call is allowed.
    """
    if metric == "tokens":
        return float(input_tokens + output_tokens)
    if metric == "input_tokens":
        return float(input_tokens)
    if metric == "output_tokens":
        return float(output_tokens)
    if metric == "cost_usd":
        return cost_usd
    return 0


class LocalQuotas:
    """Count quota usage inside one process.

    Holds are included in `try_reserve` but not in `used`, so the call that
    placed a hold is not denied by its own reservation.

    Example:
        ```python
        from datetime import UTC, datetime

        from agent_tessera.quotas import LocalQuotas

        book = LocalQuotas()
        moment = datetime(2026, 11, 6, tzinfo=UTC)
        held = book.try_reserve("calls", "day", None, amount=1, maximum=2, moment=moment)
        assert held is True
        book.reconcile("calls", "day", None, reserved=1, actual=1, moment=moment)
        assert book.used("calls", "day", None, moment) == 1
        ```
    """

    def __init__(self) -> None:
        """Start with empty counters."""
        self._lock = Lock()
        self._used: dict[tuple[str, str], float] = {}
        self._held: dict[tuple[str, str], float] = {}

    def used(self, name: str, window: str, run_id: str | None, moment: datetime) -> float:
        """Return permanent usage for one quota bucket.

        Args:
            name: Quota name.
            window: Quota window.
            run_id: Active run, used when the window is `run`.
            moment: Time that selects a calendar bucket.

        Returns:
            Usage already reconciled. A `call` window returns `0`.
        """
        key = _key(name, window, run_id, moment)
        if key is None:
            return 0
        with self._lock:
            return self._used.get(key, 0)

    def try_reserve(
        self,
        name: str,
        window: str,
        run_id: str | None,
        *,
        amount: float,
        maximum: float,
        moment: datetime,
    ) -> bool:
        """Hold units when they still fit under the ceiling.

        Args:
            name: Quota name.
            window: Quota window.
            run_id: Active run, used when the window is `run`.
            amount: Units to hold.
            maximum: Quota ceiling in the same units.
            moment: Time that selects a calendar bucket.

        Returns:
            `True` when the hold was taken. A `call` window is not held here.
        """
        if amount <= 0 or window == "call":
            return True
        key = _key(name, window, run_id, moment)
        if key is None:
            return False
        with self._lock:
            current = self._used.get(key, 0) + self._held.get(key, 0)
            if current + amount > maximum:
                return False
            self._held[key] = self._held.get(key, 0) + amount
            return True

    def release(
        self, name: str, window: str, run_id: str | None, amount: float, moment: datetime
    ) -> None:
        """Give back a hold.

        Args:
            name: Quota name.
            window: Quota window.
            run_id: Active run, used when the window is `run`.
            amount: Units to release.
            moment: Time that selects a calendar bucket.
        """
        key = _key(name, window, run_id, moment)
        if key is None or amount <= 0:
            return
        with self._lock:
            self._held[key] = max(0, self._held.get(key, 0) - amount)

    def consume(
        self, name: str, window: str, run_id: str | None, amount: float, moment: datetime
    ) -> None:
        """Add permanent usage.

        Args:
            name: Quota name.
            window: Quota window.
            run_id: Active run, used when the window is `run`.
            amount: Units to add.
            moment: Time that selects a calendar bucket.
        """
        key = _key(name, window, run_id, moment)
        if key is None or amount <= 0:
            return
        with self._lock:
            self._used[key] = self._used.get(key, 0) + amount

    def reconcile(
        self,
        name: str,
        window: str,
        run_id: str | None,
        *,
        reserved: float,
        actual: float,
        moment: datetime,
    ) -> None:
        """Replace a hold with the measured usage.

        Args:
            name: Quota name.
            window: Quota window.
            run_id: Active run, used when the window is `run`.
            reserved: Units previously held.
            actual: Units to keep.
            moment: Time that selects a calendar bucket.
        """
        self.release(name, window, run_id, reserved, moment)
        self.consume(name, window, run_id, actual, moment)


def _chars(value: object, depth: int) -> int:
    """Count characters under a value.

    Args:
        value: Object to inspect.
        depth: Current nesting depth.

    Returns:
        The character count.
    """
    if depth > _TEXT_DEPTH:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_chars(item, depth + 1) for item in value.values())
    if isinstance(value, Sequence):
        return sum(_chars(item, depth + 1) for item in value)
    return 0


def _key(name: str, window: str, run_id: str | None, moment: datetime) -> tuple[str, str] | None:
    """Return the counter key for a window, or `None` when it is not stored.

    Args:
        name: Quota name.
        window: Quota window.
        run_id: Active run.
        moment: Time that selects a calendar bucket.

    Returns:
        The name and bucket, or `None` for a call window or a run window with
        no run id.
    """
    bucket = _bucket(window, run_id, moment)
    if bucket is None:
        return None
    return (name, bucket)


def _bucket(window: str, run_id: str | None, moment: datetime) -> str | None:
    """Return the bucket name for a window.

    Args:
        window: Quota window.
        run_id: Active run.
        moment: Time that selects a calendar bucket.

    Returns:
        The bucket, or `None` when this window is not counted locally.
    """
    if window == "call":
        return None
    if window == "run":
        return None if not run_id else f"run:{run_id}"
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    aware = aware.astimezone(UTC)
    if window == "month":
        return f"{aware.year:04d}-{aware.month:02d}"
    pattern = {"hour": "%Y-%m-%dT%H", "minute": "%Y-%m-%dT%H:%M"}.get(window)
    if pattern is not None:
        return aware.strftime(pattern)
    return aware.date().isoformat()


__all__ = [
    "LocalQuotas",
    "count_amount",
    "estimate_input_tokens",
    "model_reservation",
    "quota_applies",
    "reserved_input_tokens",
    "server_leased",
    "text_chars",
    "usage_amount",
]
