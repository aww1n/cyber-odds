from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ValueMetrics:
    """Metrics derived from one model probability and one immutable odds snapshot."""

    probability: float
    odds: float
    fair_odds: float
    value_ratio: float
    value_percent: float


def _require_finite_positive(value: float, name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


def fair_odds(probability: float) -> float:
    """Return decimal fair odds for a probability in the interval (0, 1]."""

    _require_finite_positive(probability, "probability")
    if probability > 1:
        raise ValueError("probability must not exceed one")
    return 1.0 / probability


def calculate_value(probability: float, odds: float) -> ValueMetrics:
    """Calculate fair odds, value ratio ``v`` and displayed value percent."""

    _require_finite_positive(odds, "odds")
    fair = fair_odds(probability)
    ratio = odds * probability
    return ValueMetrics(
        probability=probability,
        odds=odds,
        fair_odds=fair,
        value_ratio=ratio,
        value_percent=(ratio - 1.0) * 100.0,
    )

