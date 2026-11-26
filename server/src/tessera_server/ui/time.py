"""Short relative timestamps for the admin UI."""

from __future__ import annotations

from datetime import UTC, datetime

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


def iso_datetime(value: object) -> str:
    """Format a timestamp as UTC ISO-8601 with a `Z` suffix.

    Args:
        value: Aware or naive datetime, an ISO-8601 string, or anything else.

    Returns:
        An ISO-8601 UTC string, or `""` when the value cannot be read.
    """
    moment = _as_datetime(value)
    if moment is None:
        return ""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_title(value: object) -> str:
    """Format a timestamp as an absolute UTC title.

    Args:
        value: Aware or naive datetime, an ISO-8601 string, or anything else.

    Returns:
        `YYYY-MM-DD HH:MM:SS UTC`, or `""` when the value cannot be read.
    """
    moment = _as_datetime(value)
    if moment is None:
        return ""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def relative_time(value: object, *, now: datetime | None = None) -> str:
    """Format a timestamp as a short relative phrase.

    Args:
        value: Aware or naive datetime, an ISO-8601 string, or anything else.
        now: Clock. Defaults to the current time.

    Returns:
        `never` when `value` is missing, `just now` under a minute, otherwise
        a count of minutes, hours, or days.

    Example:
        ```python
        from datetime import UTC, datetime, timedelta

        clock = datetime(2026, 11, 6, tzinfo=UTC)
        assert relative_time(clock - timedelta(minutes=5), now=clock) == "5m ago"
        ```
    """
    moment = _as_datetime(value)
    if moment is None:
        return "never"
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    seconds = int((clock - moment).total_seconds())
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m ago"
    if seconds < _DAY:
        return f"{seconds // _HOUR}h ago"
    return f"{seconds // _DAY}d ago"


def _as_datetime(value: object) -> datetime | None:
    """Normalize a timestamp argument.

    Args:
        value: Datetime, ISO-8601 text, or `None`.

    Returns:
        An aware datetime, or `None` when the value cannot be read.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value != "":
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


__all__ = ["iso_datetime", "relative_time", "utc_title"]
