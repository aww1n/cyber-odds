from __future__ import annotations

import math

import pytest

from app.value.calculator import calculate_value, fair_odds


def test_value_example_from_specification() -> None:
    metrics = calculate_value(probability=1.231 / 6.40, odds=6.40)

    assert metrics.probability == pytest.approx(0.19234375)
    assert metrics.fair_odds == pytest.approx(6.40 / 1.231)
    assert metrics.value_ratio == pytest.approx(1.231)
    assert metrics.value_percent == pytest.approx(23.1)


@pytest.mark.parametrize("probability", [0.0, -0.1, 1.01, math.nan, math.inf])
def test_fair_odds_rejects_invalid_probability(probability: float) -> None:
    with pytest.raises(ValueError):
        fair_odds(probability)


@pytest.mark.parametrize("odds", [0.0, -2.0, math.nan, math.inf])
def test_value_rejects_invalid_odds(odds: float) -> None:
    with pytest.raises(ValueError):
        calculate_value(probability=0.5, odds=odds)

