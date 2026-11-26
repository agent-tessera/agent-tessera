"""Estimate energy and carbon from an overridable model table.

Rates are joules per 1,000 tokens. Carbon uses the table's grid intensity,
in grams of CO2e per kilowatt-hour. A model that matches no pattern has no
estimate. The first matching glob wins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from agent_tessera.errors import ConfigError

_JOULES_PER_KWH = 3_600_000
_DEFAULT_PATH = Path(__file__).with_name("energy.json")


@dataclass(frozen=True, slots=True)
class EnergyRate:
    """Joules per 1,000 tokens for one model glob.

    Attributes:
        pattern: `<provider>/<model>` glob.
        joules_per_1k: Energy for 1,000 tokens.

    Example:
        ```python
        rate = EnergyRate("openai/gpt-4o", 1000)
        assert rate.joules_per_1k == 1000
        ```
    """

    pattern: str
    joules_per_1k: float


@dataclass(frozen=True, slots=True)
class EnergyTable:
    """Ordered energy rates and one grid intensity.

    Attributes:
        models: Rates in match order.
        grid_g_co2_per_kwh: Grams of CO2e per kilowatt-hour.

    Example:
        ```python
        table = EnergyTable((EnergyRate("local/*", 0),), 400)
        assert table.grid_g_co2_per_kwh == 400
        ```
    """

    models: tuple[EnergyRate, ...]
    grid_g_co2_per_kwh: float


def load_energy_table(path: Path | None = None) -> EnergyTable:
    """Load an energy table from JSON, or the built-in table.

    Args:
        path: JSON file to read. `None` reads the built-in table.

    Returns:
        The parsed table. An empty `models` list estimates nothing.

    Raises:
        ConfigError: If the file cannot be read or is not an energy table.

    Example:
        ```python
        from agent_tessera.pricing.energy import load_energy_table

        table = load_energy_table()
        assert table.grid_g_co2_per_kwh > 0
        ```
    """
    source = _DEFAULT_PATH if path is None else path
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as err:
        raise ConfigError(f"cannot read energy table {source}") from err
    except json.JSONDecodeError as err:
        raise ConfigError(f"energy table {source} is not valid JSON") from err
    if not isinstance(raw, dict):
        raise ConfigError(f"energy table {source} must be an object")
    return EnergyTable(tuple(_rates(raw, source)), _grid(raw, source))


def estimate_energy(
    model: str,
    input_tokens: int,
    output_tokens: int,
    table: EnergyTable,
) -> tuple[float, float] | None:
    """Estimate joules and grams of CO2e for one model call.

    Args:
        model: `<provider>/<model>` name.
        input_tokens: Prompt tokens. Must be zero or greater.
        output_tokens: Completion tokens. Must be zero or greater.
        table: Rates to use. The first matching pattern wins.

    Returns:
        `(joules, grams)`, or `None` when no pattern matches. Both numbers
        are estimates.

    Raises:
        ValueError: If a token count is negative.

    Example:
        ```python
        from agent_tessera.pricing.energy import EnergyRate, EnergyTable, estimate_energy

        table = EnergyTable((EnergyRate("openai/gpt-4o", 1000),), 400)
        joules, grams = estimate_energy("openai/gpt-4o", 1000, 0, table)
        assert joules == 1000
        assert grams > 0
        ```
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be zero or greater")
    rate = _match(model, table)
    if rate is None:
        return None
    joules = (input_tokens + output_tokens) / 1000 * rate.joules_per_1k
    grams = joules / _JOULES_PER_KWH * table.grid_g_co2_per_kwh
    return joules, grams


def _rates(raw: dict[str, object], source: Path) -> list[EnergyRate]:
    """Read the model list.

    Args:
        raw: Parsed JSON object.
        source: File the object came from.

    Returns:
        Rates in file order.

    Raises:
        ConfigError: When `models` is not a list of rate objects.
    """
    models = raw.get("models", [])
    if not isinstance(models, list):
        raise ConfigError(f"energy table {source} models must be a list")
    rates: list[EnergyRate] = []
    for index, item in enumerate(models):
        if not isinstance(item, dict):
            raise ConfigError(f"energy table {source} models[{index}] must be an object")
        pattern = item.get("pattern")
        joules = item.get("joules_per_1k_tokens")
        if not isinstance(pattern, str) or pattern == "":
            raise ConfigError(f"energy table {source} models[{index}].pattern must be a string")
        if isinstance(joules, bool) or not isinstance(joules, int | float) or joules < 0:
            raise ConfigError(
                f"energy table {source} models[{index}].joules_per_1k_tokens must be a number"
            )
        rates.append(EnergyRate(pattern, float(joules)))
    return rates


def _grid(raw: dict[str, object], source: Path) -> float:
    """Read the grid intensity.

    Args:
        raw: Parsed JSON object.
        source: File the object came from.

    Returns:
        Grams of CO2e per kilowatt-hour. Missing means 0.

    Raises:
        ConfigError: When the value is not a non-negative number.
    """
    value = raw.get("grid_g_co2_per_kwh", 0)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigError(f"energy table {source} grid_g_co2_per_kwh must be a number")
    return float(value)


def _match(model: str, table: EnergyTable) -> EnergyRate | None:
    """Return the first rate whose pattern matches.

    Args:
        model: Model name.
        table: Rates in match order.

    Returns:
        The rate, or `None`.
    """
    for rate in table.models:
        if _matches(model, rate.pattern):
            return rate
    return None


def _matches(model: str, pattern: str) -> bool:
    """Return whether a model name matches an energy glob.

    Args:
        model: `<provider>/<model>` name.
        pattern: Exact name, or a shell glob.

    Returns:
        `True` when the name matches. Matching is case-sensitive.
    """
    if not any(char in "*?[" for char in pattern):
        return model == pattern
    return fnmatchcase(model, pattern)


__all__ = ["EnergyRate", "EnergyTable", "estimate_energy", "load_energy_table"]
