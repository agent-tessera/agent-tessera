"""Energy and carbon estimates."""

from agent_tessera.pricing.energy import estimate_energy, load_energy_table


def test_known_model_has_energy_and_unknown_model_does_not() -> None:
    table = load_energy_table()
    known = estimate_energy("openai/gpt-4o", 1000, 0, table)
    assert known is not None
    joules, grams = known
    assert joules == 1000
    assert grams == 1000 / 3_600_000 * table.grid_g_co2_per_kwh
    assert estimate_energy("local/echo", 1000, 0, table) is None
