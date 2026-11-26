"""Count tokens and tool calls against an effective policy."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from threading import Lock

from agent_tessera.policy.effective import EffectivePolicy
from agent_tessera.policy.matching import matches_pattern
from agent_tessera.types import Action, ActionKind

_WINDOW_SECONDS = 60.0


class LimitTracker:
    """Track daily tokens and per-pattern tool calls for one agent.

    Call timestamps older than 60 seconds are dropped when calls are recorded
    or read. The token total resets when `today` returns a new date.

    Args:
        policy: Effective policy whose `calls_per_minute` patterns are counted.
        monotonic: Clock used for the call window. Defaults to `time.monotonic`.
        today: Calendar day used for the token total. Defaults to the current
            UTC date.

    Example:
        ```python
        from agent_tessera.limits import LimitTracker
        from agent_tessera.policy.effective import build_effective_policy
        from agent_tessera.policy.models import AgentPolicy
        from agent_tessera.types import Action

        agent = AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {"limits": {"calls_per_minute": {"files.*": 5}}},
            }
        )
        tracker = LimitTracker(build_effective_policy(agent))
        tracker.record_call(Action.tool("files.read"))
        assert tracker.calls_last_minute()["files.*"] == 1
        ```
    """

    def __init__(
        self,
        policy: EffectivePolicy,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        """Create a tracker for the policy's call patterns.

        Args:
            policy: Effective policy whose call patterns are counted.
            monotonic: Clock used for the 60-second window.
            today: Calendar day used for the token total.
        """
        self._monotonic = monotonic
        self._today = today
        self._lock = Lock()
        self._patterns = tuple(pattern for pattern, _limit in policy.calls_per_minute)
        self._calls: dict[str, deque[float]] = {pattern: deque() for pattern in self._patterns}
        self._tokens = 0
        self._token_day = today()

    def tokens_today(self) -> int:
        """Return tokens recorded for the current day.

        Returns:
            The token total. Zero after the day changes.
        """
        with self._lock:
            self._roll_day()
            return self._tokens

    def calls_last_minute(self) -> dict[str, int]:
        """Return how many calls each pattern has seen in the last 60 seconds.

        Returns:
            A count for every `calls_per_minute` pattern. Patterns with no
            recent calls are zero.
        """
        with self._lock:
            now = self._monotonic()
            self._prune(now)
            return {pattern: len(stamps) for pattern, stamps in self._calls.items()}

    def record_call(self, action: Action) -> None:
        """Count a tool call against every matching pattern.

        Args:
            action: Action that just ran. Model, data, and agent actions are
                ignored.
        """
        if action.kind is not ActionKind.TOOL:
            return
        with self._lock:
            now = self._monotonic()
            self._prune(now)
            for pattern, stamps in self._calls.items():
                if matches_pattern(action.resource, pattern):
                    stamps.append(now)

    def record_tokens(self, count: int) -> None:
        """Add tokens to the current day's total.

        Args:
            count: Tokens to add. A day change discards the previous total
                before these tokens are added.
        """
        with self._lock:
            self._roll_day()
            self._tokens += count

    def _roll_day(self) -> None:
        """Reset the token total when the calendar day has changed."""
        current = self._today()
        if current != self._token_day:
            self._tokens = 0
            self._token_day = current

    def _prune(self, now: float) -> None:
        """Drop call timestamps older than 60 seconds.

        Args:
            now: Current monotonic time.
        """
        for stamps in self._calls.values():
            while stamps and now - stamps[0] > _WINDOW_SECONDS:
                stamps.popleft()


__all__ = ["LimitTracker"]
