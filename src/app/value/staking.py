from __future__ import annotations

from math import isfinite


def observed_ml_bet_multiplier(
    value_ratio: float, *, slope: float = 10.0, cap: float = 3.0
) -> float:
    """Evaluate the observed FON_ml_v1 formula without claiming it is confirmed."""

    if not isfinite(value_ratio) or value_ratio < 0:
        raise ValueError("value_ratio must be finite and non-negative")
    if not isfinite(slope) or slope <= 0:
        raise ValueError("slope must be finite and greater than zero")
    if not isfinite(cap) or cap <= 0:
        raise ValueError("cap must be finite and greater than zero")
    return min(cap, max(0.0, (value_ratio - 1.0) * slope))


def fixed_stake(base_stake: float) -> float:
    if not isfinite(base_stake) or base_stake <= 0:
        raise ValueError("base_stake must be finite and greater than zero")
    return base_stake


def observed_ml_stake(
    base_stake: float,
    value_ratio: float,
    *,
    cap: float = 3.0,
) -> float:
    return fixed_stake(base_stake) * observed_ml_bet_multiplier(value_ratio, cap=cap)


def kelly_fraction(*, probability: float, odds: float) -> float:
    if not isfinite(probability) or probability < 0 or probability > 1:
        raise ValueError("probability must be finite and within [0, 1]")
    if not isfinite(odds) or odds <= 1:
        raise ValueError("odds must be finite and greater than one")
    b = odds - 1
    raw = (b * probability - (1 - probability)) / b
    return max(0.0, raw)


def fractional_kelly_stake(
    bankroll: float,
    *,
    probability: float,
    odds: float,
    fraction: float = 0.1,
    max_stake: float | None = None,
) -> float:
    if not isfinite(bankroll) or bankroll <= 0:
        raise ValueError("bankroll must be finite and greater than zero")
    if not isfinite(fraction) or fraction <= 0 or fraction > 1:
        raise ValueError("fraction must be within (0, 1]")
    stake = bankroll * fraction * kelly_fraction(probability=probability, odds=odds)
    if max_stake is not None:
        if not isfinite(max_stake) or max_stake <= 0:
            raise ValueError("max_stake must be finite and greater than zero")
        stake = min(stake, max_stake)
    return stake
