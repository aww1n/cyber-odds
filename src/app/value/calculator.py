from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

Numeric = Decimal | float | int


@dataclass(frozen=True, slots=True)
class ValueMetrics:
    """Metrics derived from one model probability and one immutable odds snapshot."""

    probability: Decimal
    odds: Decimal
    fair_odds: Decimal
    value_ratio: Decimal
    value_percent: Decimal


def _decimal(value: Numeric, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be finite and greater than zero") from error
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return result


def fair_odds(probability: Numeric) -> Decimal:
    """Return decimal fair odds for a probability in the interval (0, 1]."""

    decimal_probability = _decimal(probability, "probability")
    if decimal_probability > 1:
        raise ValueError("probability must not exceed one")
    return Decimal("1") / decimal_probability


def calculate_value(probability: Numeric, odds: Numeric) -> ValueMetrics:
    """Calculate fair odds, value ratio ``v`` and displayed value percent."""

    decimal_probability = _decimal(probability, "probability")
    decimal_odds = _decimal(odds, "odds")
    fair = fair_odds(decimal_probability)
    ratio = decimal_odds * decimal_probability
    return ValueMetrics(
        probability=decimal_probability,
        odds=decimal_odds,
        fair_odds=fair,
        value_ratio=ratio,
        value_percent=(ratio - Decimal("1")) * Decimal("100"),
    )
