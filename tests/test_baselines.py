from __future__ import annotations

import pytest

from app.models.baseline import (
    OutcomeProbabilities,
    baseline_player_frequency,
    blend_h2h_and_individual,
    weighted_recent_probability,
)
from app.models.statistical import poisson_outcomes, poisson_total_over_probability


def test_player_frequency_baseline_is_a_distribution() -> None:
    probabilities = baseline_player_frequency(
        player1_win_rate=0.55,
        player1_draw_rate=0.20,
        player2_win_rate=0.40,
        player2_draw_rate=0.30,
    )

    assert probabilities.p1 + probabilities.draw + probabilities.p2 == pytest.approx(1)
    assert probabilities.p1 > probabilities.p2


def test_weighted_recent_form_uses_explicit_weights() -> None:
    probability = weighted_recent_probability(
        {10: 0.6, 30: 0.5, "all": 0.4},
        weights={10: 0.5, 30: 0.3, "all": 0.2},
    )

    assert probability == pytest.approx(0.53)


def test_h2h_blend_is_normalized() -> None:
    distribution = blend_h2h_and_individual(
        player1=OutcomeProbabilities(0.5, 0.2, 0.3),
        player2=OutcomeProbabilities(0.4, 0.3, 0.3),
        h2h=OutcomeProbabilities(0.6, 0.1, 0.3),
        weights=(0.4, 0.3, 0.3),
    )

    assert distribution.p1 + distribution.draw + distribution.p2 == pytest.approx(1)


def test_poisson_total_and_outcome_models_are_valid() -> None:
    over = poisson_total_over_probability(goals1_rate=1.8, goals2_rate=1.2, line=2.5)
    outcomes = poisson_outcomes(goals1_rate=1.8, goals2_rate=1.2)

    assert 0 < over < 1
    assert outcomes.p1 > outcomes.p2
    assert outcomes.p1 + outcomes.draw + outcomes.p2 == pytest.approx(1)
