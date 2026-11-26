import pytest

from agent_tessera.policy.effective import PatternRule
from agent_tessera.policy.matching import conditions_match, first_match, matches_pattern
from agent_tessera.policy.models import Condition


@pytest.mark.parametrize(
    ("resource", "pattern", "expected"),
    [
        ("files.read", "files.read", True),
        ("files.write", "files.read", False),
        ("zendesk.refund", "zendesk.*", True),
        ("zendesk.a.b", "zendesk.*", True),
        ("other.refund", "zendesk.*", False),
        ("files", "file?", True),
        ("file", "file?", False),
        ("file10", "file?", False),
        ("Foo", "foo", False),
        ("foo", "foo", True),
        ("Zendesk.refund", "zendesk.*", False),
    ],
)
def test_matches_pattern(resource: str, pattern: str, expected: bool) -> None:
    assert matches_pattern(resource, pattern) is expected


def test_first_match_returns_the_first_hit() -> None:
    rules = (
        PatternRule("other.*", "agent:tools.allow[0]"),
        PatternRule("zendesk.*", "agent:tools.allow[1]"),
        PatternRule("zendesk.refund", "agent:tools.allow[2]"),
    )

    assert first_match("zendesk.refund", rules) == rules[1]
    assert first_match("shell.exec", rules) is None


def test_conditions_match_empty_list() -> None:
    assert conditions_match((), {}) is True


@pytest.mark.parametrize(
    ("op", "actual", "value", "expected"),
    [
        ("eq", 1, 1, True),
        ("eq", 1, 2, False),
        ("ne", 1, 2, True),
        ("ne", 1, 1, False),
        ("gt", 2, 1, True),
        ("gt", 1, 2, False),
        ("gte", 1, 1, True),
        ("gte", 1, 2, False),
        ("lt", 1, 2, True),
        ("lt", 2, 1, False),
        ("lte", 1, 1, True),
        ("lte", 2, 1, False),
        ("in", "open", ["open", "closed"], True),
        ("in", "pending", ["open", "closed"], False),
        ("not_in", "pending", ["open", "closed"], True),
        ("not_in", "open", ["open", "closed"], False),
        ("matches", "abb", "ab+", True),
        ("matches", "ac", "ab+", False),
    ],
)
def test_condition_operator(op: str, actual: object, value: object, expected: bool) -> None:
    condition = Condition(arg="value", op=op, value=value)

    assert conditions_match((condition,), {"value": actual}) is expected


def test_missing_argument_matches() -> None:
    condition = Condition(arg="amount", op="gt", value=100)

    assert conditions_match((condition,), {}) is True


def test_type_error_matches() -> None:
    ordering = Condition(arg="amount", op="gt", value=1)
    membership = Condition(arg="status", op="in", value=["open"])

    assert conditions_match((ordering,), {"amount": "250"}) is True
    assert conditions_match((membership,), {"status": "open"}) is True
    # A non-container expected value raises TypeError and fails closed.
    broken = Condition.model_construct(arg="status", op="in", value=5)
    assert conditions_match((broken,), {"status": "open"}) is True
    bad_pattern = Condition.model_construct(arg="name", op="matches", value=5)
    assert conditions_match((bad_pattern,), {"name": "ada"}) is True


def test_conditions_are_anded() -> None:
    low = Condition(arg="amount", op="gt", value=10)
    high = Condition(arg="amount", op="lt", value=100)

    assert conditions_match((low, high), {"amount": 50}) is True
    assert conditions_match((low, high), {"amount": 250}) is False
