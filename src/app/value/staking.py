from __future__ import annotations

from decimal import Decimal, InvalidOperation

Numeric = Decimal | float | int


def _decimal(value: Numeric, *, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


def observed_ml_bet_multiplier(
    value_ratio: Numeric,
    *,
    slope: Numeric = Decimal("10"),
    cap: Numeric = Decimal("3"),
) -> Decimal:
    """Evaluate the observed FON_ml_v1 formula without claiming it is confirmed."""

    ratio = _decimal(value_ratio, name="value_ratio")
    decimal_slope = _decimal(slope, name="slope")
    decimal_cap = _decimal(cap, name="cap")
    if ratio < 0:
        raise ValueError("value_ratio must be finite and non-negative")
    if decimal_slope <= 0:
        raise ValueError("slope must be finite and greater than zero")
    if decimal_cap <= 0:
        raise ValueError("cap must be finite and greater than zero")
    return min(
        decimal_cap,
        max(Decimal("0"), (ratio - Decimal("1")) * decimal_slope),
    )


def fixed_stake(base_stake: Numeric) -> Decimal:
    stake = _decimal(base_stake, name="base_stake")
    if stake <= 0:
        raise ValueError("base_stake must be finite and greater than zero")
    return stake


def observed_ml_stake(
    base_stake: Numeric,
    value_ratio: Numeric,
    *,
    cap: Numeric = Decimal("3"),
) -> Decimal:
    return fixed_stake(base_stake) * observed_ml_bet_multiplier(value_ratio, cap=cap)


def kelly_fraction(*, probability: Numeric, odds: Numeric) -> Decimal:
    decimal_probability = _decimal(probability, name="probability")
    decimal_odds = _decimal(odds, name="odds")
    if decimal_probability < 0 or decimal_probability > 1:
        raise ValueError("probability must be finite and within [0, 1]")
    if decimal_odds <= 1:
        raise ValueError("odds must be finite and greater than one")
    b = decimal_odds - Decimal("1")
    raw = (
        b * decimal_probability - (Decimal("1") - decimal_probability)
    ) / b
    return max(Decimal("0"), raw)


def fractional_kelly_stake(
    bankroll: Numeric,
    *,
    probability: Numeric,
    odds: Numeric,
    fraction: Numeric = Decimal("0.1"),
    max_stake: Numeric | None = None,
) -> Decimal:
    decimal_bankroll = _decimal(bankroll, name="bankroll")
    decimal_fraction = _decimal(fraction, name="fraction")
    if decimal_bankroll <= 0:
        raise ValueError("bankroll must be finite and greater than zero")
    if decimal_fraction <= 0 or decimal_fraction > 1:
        raise ValueError("fraction must be within (0, 1]")
    stake = decimal_bankroll * decimal_fraction * kelly_fraction(
        probability=probability,
        odds=odds,
    )
    if max_stake is not None:
        decimal_max_stake = _decimal(max_stake, name="max_stake")
        if decimal_max_stake <= 0:
            raise ValueError("max_stake must be finite and greater than zero")
        stake = min(stake, decimal_max_stake)
    return stake
