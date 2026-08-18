from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ImpliedProbability:
    selection: str
    odds: float
    raw_probability: float
    normalized_probability: float


@dataclass(frozen=True, slots=True)
class OddsPoint:
    odds: float
    received_at: datetime


@dataclass(frozen=True, slots=True)
class OddsMovement:
    opening_odds: float
    current_odds: float
    change_absolute: float
    change_percent: float
    seconds_since_open: float
    observations: int


def normalized_implied_probabilities(
    odds_by_selection: dict[str, float],
) -> dict[str, ImpliedProbability]:
    if len(odds_by_selection) < 2:
        raise ValueError("at least two selections are required to remove margin")
    raw: dict[str, float] = {}
    for selection, odds in odds_by_selection.items():
        if not math.isfinite(odds) or odds <= 1:
            raise ValueError(f"odds for {selection} must be finite and greater than one")
        raw[selection] = 1 / odds
    overround = sum(raw.values())
    return {
        selection: ImpliedProbability(
            selection=selection,
            odds=odds_by_selection[selection],
            raw_probability=probability,
            normalized_probability=probability / overround,
        )
        for selection, probability in raw.items()
    }


def odds_movement_before(
    points: list[OddsPoint],
    *,
    cutoff_at: datetime,
) -> OddsMovement | None:
    available = sorted(
        (point for point in points if point.received_at < cutoff_at),
        key=lambda point: point.received_at,
    )
    if not available:
        return None
    for point in available:
        if not math.isfinite(point.odds) or point.odds <= 1:
            raise ValueError("all odds must be finite and greater than one")
    opening = available[0]
    current = available[-1]
    change = current.odds - opening.odds
    return OddsMovement(
        opening_odds=opening.odds,
        current_odds=current.odds,
        change_absolute=change,
        change_percent=100 * change / opening.odds,
        seconds_since_open=(current.received_at - opening.received_at).total_seconds(),
        observations=len(available),
    )
