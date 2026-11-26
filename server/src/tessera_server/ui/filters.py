"""Jinja filters for the admin UI."""

from __future__ import annotations

_CENT = 0.01
_THOUSAND = 1_000
_MILLION = 1_000_000


def money(value: object) -> str:
    """Format a USD amount for tiles and tables.

    Args:
        value: Numeric amount, or anything else (treated as zero).

    Returns:
        `$0.00` for zero, `<$0.01` for a positive amount under one cent,
        otherwise a two-decimal dollar string.

    Example:
        ```python
        assert money(0) == "$0.00"
        assert money(0.004) == "<$0.01"
        assert money(12.5) == "$12.50"
        ```
    """
    amount = _number(value)
    if amount == 0:
        return "$0.00"
    if 0 < amount < _CENT:
        return "<$0.01"
    return f"${amount:.2f}"


def compact(value: object) -> str:
    """Format a count with a `k` or `M` suffix when large.

    Args:
        value: Numeric count, or anything else (treated as zero).

    Returns:
        A short string such as `999`, `1.2k`, or `3.4M`.

    Example:
        ```python
        assert compact(999) == "999"
        assert compact(1200) == "1.2k"
        assert compact(3_400_000) == "3.4M"
        ```
    """
    amount = _number(value)
    sign = "-" if amount < 0 else ""
    magnitude = abs(amount)
    if magnitude >= _MILLION:
        return f"{sign}{magnitude / _MILLION:.1f}M"
    if magnitude >= _THOUSAND:
        return f"{sign}{magnitude / _THOUSAND:.1f}k"
    if magnitude == int(magnitude):
        return f"{sign}{int(magnitude)}"
    return f"{sign}{magnitude:g}"


def _number(value: object) -> float:
    """Coerce a filter argument to a float.

    Args:
        value: Numeric value or anything else.

    Returns:
        The float, or `0.0` when the value is missing or not numeric.
    """
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value != "":
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


__all__ = ["compact", "money"]
