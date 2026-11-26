"""Relative timestamps used by the admin pages."""

from datetime import UTC, datetime, timedelta

from tessera_server.ui.time import relative_time

_CLOCK = datetime(2026, 11, 6, 12, tzinfo=UTC)


def test_relative_time_phrases() -> None:
    assert relative_time(None, now=_CLOCK) == "never"
    assert relative_time(_CLOCK - timedelta(seconds=10), now=_CLOCK) == "just now"
    assert relative_time(_CLOCK - timedelta(minutes=5), now=_CLOCK) == "5m ago"
    assert relative_time(_CLOCK - timedelta(hours=3), now=_CLOCK) == "3h ago"
    assert relative_time(_CLOCK - timedelta(days=2), now=_CLOCK) == "2d ago"
