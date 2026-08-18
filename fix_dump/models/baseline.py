from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutcomeProbabilities:
    p1: float
    draw: float
    p2: float

    def __post_init__(self) -> None:
        values = (self.p1, self.draw, self.p2)
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
            raise ValueError("outcome probabilities must be finite and within [0, 1]")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("outcome probabilities must sum to one")


def _normalize(p1: float, draw: float, p2: float) -> OutcomeProbabilities:
    if any(not math.isfinite(value) or value < 0 for value in (p1, draw, p2)):
        raise ValueError("baseline inputs must be finite and non-negative")
    total = p1 + draw + p2
    if total <= 0:
        return OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3)
    return OutcomeProbabilities(p1 / total, draw / total, p2 / total)


def baseline_player_frequency(
    *,
    player1_win_rate: float,
    player1_draw_rate: float,
    player2_win_rate: float,
    player2_draw_rate: float,
) -> OutcomeProbabilities:
    """Baseline 1/2: individual win frequencies and mean draw frequency."""

    return _normalize(
        player1_win_rate,
        (player1_draw_rate + player2_draw_rate) / 2,
        player2_win_rate,
    )


def weighted_recent_probability(
    rates: dict[int | str, float],
    *,
    weights: dict[int | str, float],
) -> float:
    """Baseline 3 for one binary target; weights are configuration/train artifacts."""

    if set(rates) != set(weights):
        raise ValueError("rates and weights must have identical windows")
    if any(weight < 0 or not math.isfinite(weight) for weight in weights.values()):
        raise ValueError("weights must be finite and non-negative")
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise ValueError("at least one weight must be positive")
    if any(value < 0 or value > 1 or not math.isfinite(value) for value in rates.values()):
        raise ValueError("rates must be probabilities")
    return sum(rates[key] * weights[key] for key in rates) / weight_sum


def blend_h2h_and_individual(
    *,
    player1: OutcomeProbabilities,
    player2: OutcomeProbabilities,
    h2h: OutcomeProbabilities,
    weights: tuple[float, float, float],
) -> OutcomeProbabilities:
    """Baseline 4; callers must estimate and freeze weights using train only."""

    if any(weight < 0 or not math.isfinite(weight) for weight in weights):
        raise ValueError("weights must be finite and non-negative")
    if sum(weights) <= 0:
        raise ValueError("at least one weight must be positive")
    return _normalize(
        weights[0] * player1.p1 + weights[1] * player2.p1 + weights[2] * h2h.p1,
        weights[0] * player1.draw
        + weights[1] * player2.draw
        + weights[2] * h2h.draw,
        weights[0] * player1.p2 + weights[1] * player2.p2 + weights[2] * h2h.p2,
    )
