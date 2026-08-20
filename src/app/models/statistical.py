from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.models.baseline import OutcomeProbabilities

TotalSelection = Literal["over", "under"]


@dataclass(frozen=True, slots=True)
class TotalProbabilities:
    """Poisson probabilities for one total contract.

    ``conditional_win`` excludes an integer-line push.  That makes
    ``1 / conditional_win`` the fair decimal price for a bet whose stake is
    returned on the push.
    """

    win: float
    push: float
    loss: float
    conditional_win: float


def _validate_rate(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def _poisson_probability(rate: float, goals: int) -> float:
    return math.exp(-rate) * rate**goals / math.factorial(goals)


def poisson_total_over_probability(
    *, goals1_rate: float, goals2_rate: float, line: float
) -> float:
    _validate_rate(goals1_rate, "goals1_rate")
    _validate_rate(goals2_rate, "goals2_rate")
    if line < 0 or not math.isfinite(line) or float(line).is_integer():
        raise ValueError("Poisson total line must be a non-negative half line")
    maximum_under = math.floor(line)
    total_rate = goals1_rate + goals2_rate
    under_or_equal = sum(
        _poisson_probability(total_rate, goals) for goals in range(maximum_under + 1)
    )
    return max(0.0, min(1.0, 1 - under_or_equal))


def poisson_total_probabilities(
    *,
    goals1_rate: float,
    goals2_rate: float,
    line: float,
    selection: TotalSelection,
) -> TotalProbabilities:
    """Return leakage-free probabilities for any non-negative total line.

    Scores are discrete, so non-integer lines have no push.  Integer lines use
    the probability mass at the line as ``push`` and price the win conditional
    on the bet not being returned.
    """

    _validate_rate(goals1_rate, "goals1_rate")
    _validate_rate(goals2_rate, "goals2_rate")
    if not math.isfinite(line) or line < 0:
        raise ValueError("total line must be finite and non-negative")
    if selection not in {"over", "under"}:
        raise ValueError("total selection must be over or under")

    total_rate = goals1_rate + goals2_rate
    integer_line = float(line).is_integer()
    last_below = math.ceil(line) - 1
    below = sum(
        _poisson_probability(total_rate, goals)
        for goals in range(max(0, last_below + 1))
    )
    push = _poisson_probability(total_rate, int(line)) if integer_line else 0.0
    above = max(0.0, 1.0 - below - push)
    win, loss = (above, below) if selection == "over" else (below, above)
    decided = win + loss
    conditional = win / decided if decided > 0 else 0.0
    return TotalProbabilities(
        win=max(0.0, min(1.0, win)),
        push=max(0.0, min(1.0, push)),
        loss=max(0.0, min(1.0, loss)),
        conditional_win=max(0.0, min(1.0, conditional)),
    )


def poisson_outcomes(
    *, goals1_rate: float, goals2_rate: float, max_goals: int = 20
) -> OutcomeProbabilities:
    _validate_rate(goals1_rate, "goals1_rate")
    _validate_rate(goals2_rate, "goals2_rate")
    if max_goals <= 0:
        raise ValueError("max_goals must be positive")
    p1 = draw = p2 = 0.0
    for goals1 in range(max_goals + 1):
        probability1 = _poisson_probability(goals1_rate, goals1)
        for goals2 in range(max_goals + 1):
            probability = probability1 * _poisson_probability(goals2_rate, goals2)
            if goals1 > goals2:
                p1 += probability
            elif goals1 < goals2:
                p2 += probability
            else:
                draw += probability
    total = p1 + draw + p2
    return OutcomeProbabilities(p1 / total, draw / total, p2 / total)
