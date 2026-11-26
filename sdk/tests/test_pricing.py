"""Tests for the model price table and usage cost."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from agent_tessera import Tessera
from agent_tessera.errors import ConfigError
from agent_tessera.policy.models import AgentPolicy
from agent_tessera.pricing import (
    ModelPrice,
    PriceTable,
    estimate_cost,
    load_price_table,
    model_price,
)

_TABLE = {
    "models": [
        {"pattern": "openai/gpt-4.1-mini", "input_per_million": "0.4", "output_per_million": "1.6"},
        {"pattern": "openai/gpt-4.1*", "input_per_million": "2", "output_per_million": "8"},
    ]
}


def _policy() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot"},
            "spec": {"models": {"allow": ["*"]}},
        }
    )


def _events(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_builtin_table_prices_a_known_model() -> None:
    table = load_price_table()
    price = model_price("openai/gpt-4.1", table)

    assert price is not None
    assert estimate_cost("openai/gpt-4.1", 1_000_000, 0, table) == price.input_per_million
    assert price.input_per_million > 0
    assert model_price("openai/gpt-4.1-mini", table) is not price


def test_specific_pattern_wins_over_a_later_glob(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(_TABLE), encoding="utf-8")
    table = load_price_table(path)

    assert estimate_cost("openai/gpt-4.1-mini", 1_000_000, 0, table) == Decimal("0.4")
    assert estimate_cost("openai/gpt-4.1", 3, 4, table) == Decimal("0.000038")
    assert estimate_cost("local/echo", 10, 10, table) == Decimal(0)


def test_negative_tokens_are_rejected() -> None:
    table = PriceTable((ModelPrice("openai/gpt-4.1", Decimal("2"), Decimal("8")),))

    with pytest.raises(ValueError, match="non-negative"):
        estimate_cost("openai/gpt-4.1", -1, 0, table)


def test_bad_price_table_is_a_config_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    missing = tmp_path / "missing.json"

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_price_table(broken)
    with pytest.raises(ConfigError, match="cannot read"):
        load_price_table(missing)


def test_usage_records_cost_and_one_unknown_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(_TABLE), encoding="utf-8")
    monkeypatch.setenv("TESSERA_PRICE_TABLE", str(path))
    audit = tmp_path / "audit.jsonl"
    other = tmp_path / "other.jsonl"
    first = Tessera("bot", policy=_policy(), audit_path=audit)
    second = Tessera("bot", policy=_policy(), audit_path=other, price_table=path)

    first.record_usage("openai/gpt-4.1", 3, 4)
    first.record_usage("vendor/not-priced-once", 8, 9)
    first.record_usage("vendor/not-priced-once", 1, 1)
    second.record_usage("vendor/not-priced-once", 2, 2)
    first.close()
    second.close()

    usage = [event for event in _events(audit) if event["type"] == "usage"]
    unknown = [event for event in _events(audit) if event["type"] == "cost.unknown_model"]
    assert usage[0]["cost_usd"] == "0.000038"
    assert usage[1]["cost_usd"] == "0"
    assert [event["model"] for event in unknown] == ["vendor/not-priced-once"]
    assert not any(event["type"] == "cost.unknown_model" for event in _events(other))
