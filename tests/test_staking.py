from __future__ import annotations

import math

import pytest

from app.value.staking import (
    fractional_kelly_stake,
    kelly_fraction,
    observed_ml_bet_multiplier,
)


@pytest.mark.parametrize(
    ("value_ratio", "expected"),
    [
        (0.90, 0.0),
        (1.00, 0.0),
        (1.154, 1.54),
        (1.235, 2.35),
        (1.364, 3.0),
        (2.00, 3.0),
    ],
)
def test_observed_ml_bet_multiplier(value_ratio: float, expected: float) -> None:
    assert observed_ml_bet_multiplier(value_ratio) == pytest.approx(expected)


@pytest.mark.parametrize("value_ratio", [-0.1, math.nan, math.inf])
def test_observed_ml_bet_multiplier_rejects_invalid_value(value_ratio: float) -> None:
    with pytest.raises(ValueError):
        observed_ml_bet_multiplier(value_ratio)


def test_fractional_kelly_is_positive_only_for_positive_edge() -> None:
    assert kelly_fraction(probability=0.6, odds=2.0) == pytest.approx(0.2)
    assert kelly_fraction(probability=0.4, odds=2.0) == 0
    assert fractional_kelly_stake(
        1000,
        probability=0.6,
        odds=2.0,
        fraction=0.25,
        max_stake=40,
    ) == pytest.approx(40)
