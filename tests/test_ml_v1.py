from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.backtest.baseline_evaluation import Outcome, OutcomePrediction
from app.backtest.walk_forward import WalkForwardSplit
from app.features import FeatureSet
from app.models.baseline import OutcomeProbabilities
from app.models.ml_v1 import (
    ML_FEATURE_NAMES,
    LogisticMLV1Evaluator,
    MLDataRow,
    MLDataset,
    reliability_bins,
    vectorize,
)


def test_vectorize_has_stable_order_and_fills_missing_values() -> None:
    first, second = ML_FEATURE_NAMES[:2]
    features = FeatureSet(
        event_id=1,
        cutoff_at=datetime(2026, 8, 1, tzinfo=UTC),
        eligible_match_ids=(),
        values={second: 2.0, first: 1.0, "not_selected": 99.0},
    )

    values = vectorize(features)

    assert len(values) == len(ML_FEATURE_NAMES)
    assert values[:2] == (1.0, 2.0)
    assert set(values[2:]) == {0.0}


def test_reliability_bins_account_for_every_prediction() -> None:
    started_at = datetime(2026, 8, 1, tzinfo=UTC)
    rows = [
        OutcomePrediction(
            event_id=1,
            started_at=started_at,
            model="test",
            probabilities=OutcomeProbabilities(0.7, 0.2, 0.1),
            actual="P1",
            player1_samples=0,
            player2_samples=0,
            h2h_samples=0,
        ),
        OutcomePrediction(
            event_id=2,
            started_at=started_at,
            model="test",
            probabilities=OutcomeProbabilities(0.2, 0.4, 0.4),
            actual="X",
            player1_samples=0,
            player2_samples=0,
            h2h_samples=0,
        ),
    ]

    bins = reliability_bins(rows, bins=5)

    assert sum(item.predictions for item in bins) == len(rows)
    with pytest.raises(ValueError, match="positive"):
        reliability_bins(rows, bins=0)


def _synthetic_row(index: int, started_at: datetime) -> MLDataRow:
    outcomes: tuple[Outcome, ...] = ("P1", "X", "P2")
    actual = outcomes[index % len(outcomes)]
    values = [0.0] * len(ML_FEATURE_NAMES)
    values[0] = float(index % 3)
    values[1] = float((index * 2) % 5)
    values[2] = float(index) / 100
    return MLDataRow(index, started_at, actual, tuple(values))


def test_logistic_evaluator_selects_calibration_on_validation_and_scores_test() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = tuple(_synthetic_row(index, start + timedelta(hours=index)) for index in range(72))
    dataset = MLDataset(
        source="synthetic",
        candidates_seen=len(rows),
        skipped_for_samples=0,
        rows=rows,
    )
    split = WalkForwardSplit(
        train_start=start,
        train_end=start + timedelta(hours=36),
        validation_start=start + timedelta(hours=36),
        validation_end=start + timedelta(hours=54),
        test_start=start + timedelta(hours=54),
        test_end=start + timedelta(hours=72),
    )

    report = LogisticMLV1Evaluator().run(dataset, (split,))

    assert len(report.folds) == 1
    assert report.folds[0].selected_calibration in {
        "uncalibrated",
        "platt",
        "isotonic",
    }
    assert set(report.folds[0].validation_metrics) == {
        "uncalibrated",
        "platt",
        "isotonic",
    }
    assert report.out_of_sample.predictions == 18
    assert sum(item.predictions for item in report.reliability) == 18


def test_logistic_evaluator_requires_all_classes_in_train_and_validation() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    values = (0.0,) * len(ML_FEATURE_NAMES)
    rows = tuple(
        MLDataRow(index, start + timedelta(hours=index), "P1", values)
        for index in range(6)
    )
    split = WalkForwardSplit(
        train_start=start,
        train_end=start + timedelta(hours=2),
        validation_start=start + timedelta(hours=2),
        validation_end=start + timedelta(hours=4),
        test_start=start + timedelta(hours=4),
        test_end=start + timedelta(hours=6),
    )

    with pytest.raises(ValueError, match="train split"):
        LogisticMLV1Evaluator().run(MLDataset("test", 6, 0, rows), (split,))
