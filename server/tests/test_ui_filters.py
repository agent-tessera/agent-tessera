"""Admin UI Jinja filter helpers (still used by the login stylesheet era)."""

from __future__ import annotations

from datetime import UTC, datetime

from tessera_server.ui.filters import compact, money
from tessera_server.ui.time import iso_datetime, relative_time, utc_title

_CLOCK = datetime(2026, 11, 9, 15, 30, 0, tzinfo=UTC)


def test_money_filter() -> None:
    assert money(0) == "$0.00"
    assert money(0.0) == "$0.00"
    assert money(0.004) == "<$0.01"
    assert money(12.5) == "$12.50"
    assert money(None) == "$0.00"


def test_compact_filter() -> None:
    assert compact(0) == "0"
    assert compact(999) == "999"
    assert compact(1200) == "1.2k"
    assert compact(3_400_000) == "3.4M"
    assert compact(None) == "0"


def test_time_helpers() -> None:
    stamp = _CLOCK.isoformat().replace("+00:00", "Z")
    assert iso_datetime(_CLOCK) == stamp
    assert "UTC" in utc_title(_CLOCK)
    assert relative_time(_CLOCK, now=_CLOCK) == "just now"
