"""Match resource names and call-argument conditions."""

from __future__ import annotations

import re
from collections.abc import Container, Mapping, Sequence
from fnmatch import fnmatchcase
from typing import Protocol, cast

from agent_tessera.policy.effective import PatternRule
from agent_tessera.policy.models import Condition


def matches_pattern(resource: str, pattern: str) -> bool:
    """Return whether a resource name matches a shell-style glob.

    Matching is case-sensitive. `*` matches any characters, including `.` and
    `/`.

    Args:
        resource: Resource name, such as `zendesk.refund`.
        pattern: Glob, such as `zendesk.*`.

    Returns:
        `True` when the resource matches the pattern.

    Example:
        ```python
        assert matches_pattern("zendesk.a.b", "zendesk.*") is True
        assert matches_pattern("Zendesk.refund", "zendesk.*") is False
        ```
    """
    if not any(char in "*?[" for char in pattern):
        return resource == pattern
    return fnmatchcase(resource, pattern)


def first_match(resource: str, rules: Sequence[PatternRule]) -> PatternRule | None:
    """Return the first pattern rule that matches a resource.

    Args:
        resource: Resource name to test.
        rules: Rules in evaluation order.

    Returns:
        The first matching rule, or `None` when nothing matches.
    """
    for rule in rules:
        if rule.literal:
            if rule.pattern == resource:
                return rule
            continue
        if fnmatchcase(resource, rule.pattern):
            return rule
    return None


def conditions_match(conditions: Sequence[Condition], args: Mapping[str, object]) -> bool:
    """Return whether every condition holds for a call's arguments.

    An empty list matches. A missing argument, or a comparison that raises
    `TypeError`, counts as a match so the decision fails closed.

    Args:
        conditions: Conditions combined with AND.
        args: Top-level call arguments.

    Returns:
        `True` when every condition matches.

    Example:
        ```python
        from agent_tessera.policy.models import Condition

        amount = Condition(arg="amount", op="gt", value=100)
        assert conditions_match((amount,), {"amount": 250}) is True
        assert conditions_match((), {}) is True
        ```
    """
    return all(_condition_matches(condition, args) for condition in conditions)


def _condition_matches(condition: Condition, args: Mapping[str, object]) -> bool:
    """Return whether one condition matches, failing closed.

    Args:
        condition: Comparison to apply.
        args: Top-level call arguments.

    Returns:
        `True` when the argument is missing, the comparison holds, or the
        comparison raises `TypeError`.
    """
    if condition.arg not in args:
        return True
    try:
        return _compare(condition.op, args[condition.arg], condition.value)
    except TypeError:
        return True


class _Ordered(Protocol):
    """Values that support the ordering operators used by conditions."""

    def __gt__(self, other: object) -> bool: ...

    def __ge__(self, other: object) -> bool: ...

    def __lt__(self, other: object) -> bool: ...

    def __le__(self, other: object) -> bool: ...


def _compare(op: str, actual: object, expected: object) -> bool:
    """Apply one condition operator.

    Args:
        op: Operator name stored on the condition.
        actual: Argument value from the call.
        expected: Value stored on the condition.

    Returns:
        Whether the comparison holds.

    Raises:
        TypeError: When the values cannot be compared.
    """
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op in {"gt", "gte", "lt", "lte"}:
        return _order(op, actual, expected)
    if op == "in":
        return _contains(actual, expected)
    if op == "not_in":
        return not _contains(actual, expected)
    if not isinstance(expected, str):
        raise TypeError("matches comparison requires a string pattern")
    return re.fullmatch(expected, str(actual)) is not None


def _order(op: str, actual: object, expected: object) -> bool:
    """Compare two values with an ordering operator.

    Args:
        op: One of `gt`, `gte`, `lt`, or `lte`.
        actual: Argument value from the call.
        expected: Threshold stored on the condition.

    Returns:
        Whether the ordering comparison holds.

    Raises:
        TypeError: When the values do not support that ordering.
    """
    left = cast(_Ordered, actual)
    if op == "gt":
        return left > expected
    if op == "gte":
        return left >= expected
    if op == "lt":
        return left < expected
    return left <= expected


def _contains(actual: object, expected: object) -> bool:
    """Return whether `actual` is a member of `expected`.

    Args:
        actual: Argument value from the call.
        expected: Membership collection stored on the condition.

    Returns:
        Whether `expected` contains `actual`.

    Raises:
        TypeError: When `expected` is not a container.
    """
    if not isinstance(expected, Container):
        raise TypeError("membership comparison requires a container")
    return actual in expected


__all__ = ["conditions_match", "first_match", "matches_pattern"]
