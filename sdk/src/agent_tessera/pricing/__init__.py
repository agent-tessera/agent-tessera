"""Estimate model cost from a built-in or caller-supplied price table.

Prices are USD per one million tokens. The built-in table is a small estimate
for common OpenAI and Anthropic model names. Load another file with
`load_price_table` when those rates are wrong. The first matching glob wins,
so a specific pattern has to be listed before a broader one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
from pathlib import Path
from threading import Lock

from agent_tessera.errors import ConfigError

_PER_MILLION = Decimal(1_000_000)
_DEFAULT_PATH = Path(__file__).with_name("default.json")
_UNKNOWN: set[str] = set()
_UNKNOWN_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD rates for one model glob.

    Attributes:
        pattern: `<provider>/<model>` glob. `*` and `?` are shell wildcards.
        input_per_million: USD for one million input tokens.
        output_per_million: USD for one million output tokens.

    Example:
        ```python
        price = ModelPrice("openai/gpt-4.1", Decimal("2"), Decimal("8"))
        assert price.input_per_million == Decimal("2")
        ```
    """

    pattern: str
    input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True, slots=True)
class PriceTable:
    """Ordered model rates. The first matching pattern wins.

    Attributes:
        models: Rates in match order.

    Example:
        ```python
        table = PriceTable((ModelPrice("local/*", Decimal("0"), Decimal("0")),))
        assert table.models[0].pattern == "local/*"
        ```
    """

    models: tuple[ModelPrice, ...]


def load_price_table(path: Path | None = None) -> PriceTable:
    """Load a price table from JSON, or the built-in table.

    Args:
        path: JSON file to read. `None` reads the built-in table.

    Returns:
        The parsed table. An empty `models` list prices every model at zero.

    Raises:
        ConfigError: If the file cannot be read or is not a price table.

    Example:
        ```python
        from agent_tessera.pricing import load_price_table

        table = load_price_table()
        assert table.models
        ```
    """
    source = _DEFAULT_PATH if path is None else path
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as err:
        raise ConfigError(f"cannot read price table {source}") from err
    except json.JSONDecodeError as err:
        raise ConfigError(f"price table {source} is not valid JSON") from err
    return PriceTable(tuple(_prices(raw, source)))


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    table: PriceTable,
) -> Decimal:
    """Estimate the USD cost of one model call.

    Args:
        model: `<provider>/<model>` name.
        input_tokens: Prompt tokens. Must be zero or greater.
        output_tokens: Completion tokens. Must be zero or greater.
        table: Rates to use. The first matching pattern wins.

    Returns:
        The estimated cost. Zero when no pattern matches.

    Raises:
        ValueError: If a token count is negative.

    Example:
        ```python
        from decimal import Decimal

        from agent_tessera.pricing import PriceTable, ModelPrice, estimate_cost

        table = PriceTable((ModelPrice("openai/gpt-4.1", Decimal("2"), Decimal("8")),))
        assert estimate_cost("openai/gpt-4.1", 1_000_000, 0, table) == Decimal("2")
        assert estimate_cost("local/echo", 10, 10, table) == Decimal("0")
        ```
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    price = model_price(model, table)
    if price is None:
        return Decimal(0)
    return (
        Decimal(input_tokens) * price.input_per_million
        + Decimal(output_tokens) * price.output_per_million
    ) / _PER_MILLION


def model_price(model: str, table: PriceTable) -> ModelPrice | None:
    """Return the first rate whose pattern matches a model.

    Args:
        model: `<provider>/<model>` name.
        table: Rates in match order.

    Returns:
        The matching rate, or `None` when every pattern misses.

    Example:
        ```python
        from decimal import Decimal

        from agent_tessera.pricing import PriceTable, ModelPrice, model_price

        table = PriceTable((ModelPrice("openai/*", Decimal("1"), Decimal("1")),))
        assert model_price("openai/gpt-4.1", table) is not None
        ```
    """
    for price in table.models:
        if _matches(model, price.pattern):
            return price
    return None


def claim_unknown_model(model: str) -> bool:
    """Remember an unknown model for this process.

    Args:
        model: Model name that had no price.

    Returns:
        `True` the first time this process sees `model`, otherwise `False`.

    Example:
        ```python
        from agent_tessera.pricing import claim_unknown_model

        assert claim_unknown_model("vendor/example-once") is True
        assert claim_unknown_model("vendor/example-once") is False
        ```
    """
    with _UNKNOWN_LOCK:
        if model in _UNKNOWN:
            return False
        _UNKNOWN.add(model)
        return True


def _prices(raw: object, source: Path) -> tuple[ModelPrice, ...]:
    """Parse the `models` list from a price-table document.

    Args:
        raw: Decoded JSON value.
        source: Path named in errors.

    Returns:
        One rate per entry, in file order.

    Raises:
        ConfigError: If the document is not a list of rates.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise ConfigError(f"price table {source} must contain a models list")
    return tuple(_price(entry, source, index) for index, entry in enumerate(raw["models"]))


def _price(entry: object, source: Path, index: int) -> ModelPrice:
    """Parse one rate.

    Args:
        entry: One `models` element.
        source: Path named in errors.
        index: Position in the list, starting at zero.

    Returns:
        The rate.

    Raises:
        ConfigError: If the pattern or either rate is missing or invalid.
    """
    if not isinstance(entry, dict):
        raise ConfigError(f"price table {source} models[{index}] must be an object")
    pattern = entry.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError(f"price table {source} models[{index}] needs a pattern")
    return ModelPrice(
        pattern,
        _rate(entry.get("input_per_million"), source, index, "input_per_million"),
        _rate(entry.get("output_per_million"), source, index, "output_per_million"),
    )


def _rate(value: object, source: Path, index: int, field: str) -> Decimal:
    """Parse a non-negative USD rate.

    Args:
        value: JSON number or numeric string.
        source: Path named in errors.
        index: Position in the models list.
        field: Rate field name.

    Returns:
        The rate.

    Raises:
        ConfigError: If the value is not a non-negative number.
    """
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise ConfigError(f"price table {source} models[{index}].{field} must be a number")
    try:
        rate = Decimal(str(value))
    except InvalidOperation as err:
        raise ConfigError(f"price table {source} models[{index}].{field} must be a number") from err
    if not rate.is_finite() or rate < 0:
        raise ConfigError(f"price table {source} models[{index}].{field} must be non-negative")
    return rate


def _matches(model: str, pattern: str) -> bool:
    """Return whether a model name matches a price glob.

    Args:
        model: `<provider>/<model>` name.
        pattern: Exact name, or a shell glob.

    Returns:
        `True` when the name matches. Matching is case-sensitive.
    """
    if not any(char in "*?[" for char in pattern):
        return model == pattern
    return fnmatchcase(model, pattern)


__all__ = [
    "ModelPrice",
    "PriceTable",
    "claim_unknown_model",
    "estimate_cost",
    "load_price_table",
    "model_price",
]
