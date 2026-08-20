from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from math import sqrt


@dataclass(frozen=True, slots=True)
class CorridorMetrics:
    sample_size: int
    wins: int
    losses: int
    returns: int
    win_rate: Decimal
    average_odds: Decimal
    average_implied_probability: Decimal
    roi_percent: Decimal
    edge_percent: Decimal
    confidence: Decimal


def bucket_bounds(odds: Decimal, width: Decimal) -> tuple[Decimal, Decimal]:
    """Return the deterministic half-open odds bucket ``[low, high)``.

    Buckets are anchored at decimal odds 1.00, so an odds quote of 1.75 with a
    width of 0.25 is assigned to ``[1.75, 2.00)``. Anchoring avoids generating
    an invalid ``[1.00, 1.25)`` row for valid low-odds quotes.
    """

    if odds <= Decimal("1"):
        raise ValueError("odds must be greater than 1")
    if width <= Decimal("0"):
        raise ValueError("bucket width must be positive")
    steps = ((odds - Decimal("1")) / width).to_integral_value(rounding=ROUND_FLOOR)
    low = Decimal("1") + steps * width
    high = low + width
    return low, high


def calculate_metrics_from_totals(
    *,
    sample_size: int,
    wins: int,
    odds_total: Decimal,
    implied_probability_total: Decimal,
    profit_total: Decimal,
    returns: int = 0,
) -> CorridorMetrics:
    """Calculate metrics from exact aggregate totals without loading all rows."""

    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    if not 0 <= wins <= sample_size or not 0 <= returns <= sample_size - wins:
        raise ValueError("wins and returns must fit within sample_size")
    losses = sample_size - wins - returns
    divisor = Decimal(sample_size)
    win_rate = Decimal(wins) / divisor
    average_odds = odds_total / divisor
    average_implied = implied_probability_total / divisor
    roi_percent = profit_total / divisor * Decimal("100")
    edge_percent = (win_rate - average_implied) * Decimal("100")

    # This is a bounded reliability score, not a p-value or an assertion that
    # the observed edge is statistically significant. It combines sample depth
    # with Bernoulli uncertainty and remains in [0, 1].
    p = float(win_rate)
    standard_error = sqrt(max(0.0, p * (1.0 - p) / sample_size))
    uncertainty_factor = max(0.0, 1.0 - min(1.0, 3.92 * standard_error))
    depth_factor = min(1.0, sample_size / 100.0)
    confidence = Decimal(str(depth_factor * uncertainty_factor)).quantize(Decimal("0.0001"))

    return CorridorMetrics(
        sample_size=sample_size,
        wins=wins,
        losses=losses,
        returns=returns,
        win_rate=win_rate,
        average_odds=average_odds,
        average_implied_probability=average_implied,
        roi_percent=roi_percent,
        edge_percent=edge_percent,
        confidence=confidence,
    )


def calculate_metrics(observations: Iterable[tuple[Decimal, bool]]) -> CorridorMetrics:
    """Aggregate resolved 1-unit bets without rounding intermediate values."""

    sample_size = wins = 0
    odds_total = implied_total = profit_total = Decimal("0")
    for odds, won in observations:
        if odds <= Decimal("1"):
            raise ValueError("all observation odds must be greater than 1")
        sample_size += 1
        odds_total += odds
        implied_total += Decimal("1") / odds
        if won:
            wins += 1
            profit_total += odds - Decimal("1")
        else:
            profit_total -= Decimal("1")
    return calculate_metrics_from_totals(
        sample_size=sample_size,
        wins=wins,
        odds_total=odds_total,
        implied_probability_total=implied_total,
        profit_total=profit_total,
    )
