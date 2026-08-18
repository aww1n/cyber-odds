from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from app.backtest.baseline_evaluation import (
    OutcomePrediction,
    calculate_probability_metrics,
    evaluate_walk_forward,
    predict_baselines,
)
from app.backtest.walk_forward import WalkForwardSplit
from app.features import FeatureSet
from app.models.baseline import OutcomeProbabilities


def _features() -> FeatureSet:
    values: dict[str, float] = {}
    for side, wins, draws, losses, goals_for, goals_against in (
        ("p1", 55.0, 20.0, 25.0, 1.8, 1.1),
        ("p2", 40.0, 25.0, 35.0, 1.4, 1.3),
    ):
        for window in ("all", "last_10", "last_30"):
            prefix = f"{side}_global_{window}"
            values[f"{prefix}_wins"] = wins
            values[f"{prefix}_draws"] = draws
            values[f"{prefix}_losses"] = losses
        values[f"{side}_global_all_goals_for_avg"] = goals_for
        values[f"{side}_global_all_goals_against_avg"] = goals_against
    values.update(
        {
            "h2h_all_wins": 6.0,
            "h2h_all_draws": 2.0,
            "h2h_all_losses": 2.0,
        }
    )
    return FeatureSet(
        event_id=1,
        cutoff_at=datetime(2026, 8, 1, tzinfo=UTC),
        eligible_match_ids=(),
        values=values,
    )


def test_baseline_predictions_are_smoothed_distributions() -> None:
    predictions = predict_baselines(_features())

    expected = {
        "uniform",
        "player_frequency",
        "weighted_recent_10_30_all",
        "individual_h2h_fixed",
        "poisson_goals",
    }
    expected.update(
        f"{prefix}_last_{window}"
        for prefix in ("player_frequency", "h2h")
        for window in (5, 10, 20, 25, 30, 50, 75, 100, 200)
    )
    assert set(predictions) == expected
    for probability in predictions.values():
        assert probability.p1 + probability.draw + probability.p2 == pytest.approx(1)
        assert min(probability.p1, probability.draw, probability.p2) > 0


def test_uniform_multiclass_metrics_have_expected_log_loss() -> None:
    probability = OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3)
    outcomes: tuple[Literal["P1", "X", "P2"], ...] = ("P1", "X", "P2")
    rows = [
        OutcomePrediction(
            event_id=index,
            started_at=datetime(2026, 8, index, tzinfo=UTC),
            model="uniform",
            probabilities=probability,
            actual=actual,
            player1_samples=20,
            player2_samples=20,
            h2h_samples=0,
        )
        for index, actual in enumerate(outcomes, start=1)
    ]

    metrics = calculate_probability_metrics(rows)

    assert metrics.log_loss == pytest.approx(math.log(3))
    assert metrics.brier_score == pytest.approx(2 / 3)
    assert metrics.predictions == 3


def test_walk_forward_metrics_use_test_interval_only() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    split = WalkForwardSplit(
        train_start=start,
        train_end=start + timedelta(days=2),
        validation_start=start + timedelta(days=2),
        validation_end=start + timedelta(days=3),
        test_start=start + timedelta(days=3),
        test_end=start + timedelta(days=4),
    )
    probabilities = OutcomeProbabilities(0.6, 0.2, 0.2)
    predictions = [
        OutcomePrediction(
            event_id=index,
            started_at=start + timedelta(days=index),
            model="test",
            probabilities=probabilities,
            actual="P1",
            player1_samples=20,
            player2_samples=20,
            h2h_samples=1,
        )
        for index in range(4)
    ]

    folds, aggregate = evaluate_walk_forward(predictions, (split,))

    assert len(folds) == 1
    assert folds[0].train_predictions == 2
    assert folds[0].validation_predictions == 1
    assert folds[0].metrics.predictions == 1
    assert aggregate["test"].predictions == 1
