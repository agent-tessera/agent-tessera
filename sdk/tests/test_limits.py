"""Tests for the token and call-rate tracker."""

import threading
from datetime import date

from agent_tessera.limits import LimitTracker
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.types import Action


def _tracker(
    *,
    patterns: dict[str, int] | None = None,
    clock: dict[str, float] | None = None,
    day: dict[str, date] | None = None,
) -> LimitTracker:
    agent = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot"},
            "spec": {"limits": {"calls_per_minute": patterns or {"zendesk.*": 30}}},
        }
    )
    return LimitTracker(
        build_effective_policy(agent),
        monotonic=lambda: 0.0 if clock is None else clock["t"],
        today=lambda: date(2026, 1, 1) if day is None else day["d"],
    )


def test_sliding_window_keeps_59_seconds_and_drops_61() -> None:
    clock = {"t": 0.0}
    tracker = _tracker(clock=clock)
    tracker.record_call(Action.tool("zendesk.refund"))
    clock["t"] = 59
    assert tracker.calls_last_minute() == {"zendesk.*": 1}
    clock["t"] = 61
    assert tracker.calls_last_minute() == {"zendesk.*": 0}


def test_recording_prunes_expired_calls() -> None:
    clock = {"t": 0.0}
    tracker = _tracker(clock=clock)
    tracker.record_call(Action.tool("zendesk.refund"))
    clock["t"] = 61
    tracker.record_call(Action.tool("zendesk.refund"))
    assert tracker.calls_last_minute() == {"zendesk.*": 1}


def test_only_tool_calls_are_counted() -> None:
    tracker = _tracker()
    tracker.record_call(Action.model("openai/gpt-4.1"))
    tracker.record_call(Action.data("crm.customers:read"))
    tracker.record_call(Action.agent("billing"))
    tracker.record_call(Action.tool("files.read"))
    assert tracker.calls_last_minute() == {"zendesk.*": 0}
    tracker.record_call(Action.tool("zendesk.read"))
    assert tracker.calls_last_minute() == {"zendesk.*": 1}


def test_a_tool_matching_two_patterns_increments_both() -> None:
    tracker = _tracker(patterns={"zendesk.*": 30, "*.refund": 5})
    tracker.record_call(Action.tool("zendesk.refund"))
    assert tracker.calls_last_minute() == {"zendesk.*": 1, "*.refund": 1}
    tracker.record_call(Action.tool("zendesk.read"))
    assert tracker.calls_last_minute() == {"zendesk.*": 2, "*.refund": 1}


def test_day_rollover_resets_tokens() -> None:
    day = {"d": date(2026, 1, 1)}
    tracker = _tracker(day=day)
    tracker.record_tokens(5)
    tracker.record_tokens(2)
    assert tracker.tokens_today() == 7
    day["d"] = date(2026, 1, 2)
    assert tracker.tokens_today() == 0
    tracker.record_tokens(3)
    assert tracker.tokens_today() == 3


def test_token_updates_are_thread_safe() -> None:
    tracker = _tracker()

    def add() -> None:
        for _ in range(100):
            tracker.record_tokens(1)

    threads = [threading.Thread(target=add) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert tracker.tokens_today() == 400
