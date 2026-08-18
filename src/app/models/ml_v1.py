from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

import numpy as np
from sklearn.calibration import CalibratedClassifierCV  # type: ignore[import-untyped]
from sklearn.frozen import FrozenEstimator  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.baseline_evaluation import (
    Outcome,
    OutcomePrediction,
    ProbabilityMetrics,
    calculate_probability_metrics,
)
from app.backtest.walk_forward import WalkForwardSplit
from app.database.models import Event, Result, Source
from app.features import RESEARCH_WINDOWS, DatabaseFeatureBuilder, FeatureSet
from app.models.baseline import OutcomeProbabilities

CalibrationMethod = Literal["uncalibrated", "platt", "isotonic"]


class ProbabilityEstimator(Protocol):
    classes_: Any

    def predict_proba(self, values: np.ndarray[Any, np.dtype[np.float64]]) -> Any: ...


def _feature_names() -> tuple[str, ...]:
    names: list[str] = []
    aggregate_fields = (
        "matches",
        "win_rate",
        "draw_rate",
        "loss_rate",
        "goals_for_avg",
        "goals_against_avg",
        "total_avg",
        "std_total",
        "over_2_5",
        "over_3_5",
        "over_4_5",
    )
    for side in ("p1", "p2"):
        for window in (*[f"last_{value}" for value in RESEARCH_WINDOWS], "all"):
            names.extend(
                f"{side}_global_{window}_{field}" for field in aggregate_fields
            )
        for scope in ("source", "tournament", "player_team"):
            names.extend(f"{side}_{scope}_all_{field}" for field in aggregate_fields)
        names.extend(
            f"{side}_session_{field}"
            for field in (
                "matches_last_30m",
                "matches_last_1h",
                "matches_last_3h",
                "minutes_since_previous_game",
                "wins",
                "draws",
                "losses",
                "goals",
                "consecutive_wins",
                "consecutive_draws",
                "consecutive_losses",
            )
        )
    for window in (*[f"last_{value}" for value in RESEARCH_WINDOWS], "all"):
        names.extend(f"h2h_{window}_{field}" for field in aggregate_fields)
    for scope in ("h2h_same_direction", "player_team_h2h"):
        names.extend(f"{scope}_all_{field}" for field in aggregate_fields)
    return tuple(names)


ML_FEATURE_NAMES = _feature_names()


@dataclass(frozen=True, slots=True)
class MLDataRow:
    event_id: int
    started_at: datetime
    actual: Outcome
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class MLDataset:
    source: str
    candidates_seen: int
    skipped_for_samples: int
    rows: tuple[MLDataRow, ...]


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lower: float
    upper: float
    predictions: int
    average_confidence: float
    observed_accuracy: float


@dataclass(frozen=True, slots=True)
class MLFoldEvaluation:
    fold: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    train_rows: int
    validation_rows: int
    test_rows: int
    validation_metrics: dict[CalibrationMethod, ProbabilityMetrics]
    selected_calibration: CalibrationMethod
    test_metrics: ProbabilityMetrics
    reliability: tuple[ReliabilityBin, ...]


@dataclass(frozen=True, slots=True)
class MLEvaluationReport:
    model: str
    source: str
    feature_count: int
    candidates_seen: int
    eligible_events: int
    skipped_for_samples: int
    folds: tuple[MLFoldEvaluation, ...]
    out_of_sample: ProbabilityMetrics
    reliability: tuple[ReliabilityBin, ...]
    note: str


def vectorize(features: FeatureSet) -> tuple[float, ...]:
    return tuple(float(features.values.get(name, 0.0)) for name in ML_FEATURE_NAMES)


def reliability_bins(
    predictions: list[OutcomePrediction],
    *,
    bins: int = 10,
) -> tuple[ReliabilityBin, ...]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    output: list[ReliabilityBin] = []
    labels: tuple[Outcome, ...] = ("P1", "X", "P2")
    rows: list[tuple[float, float]] = []
    for prediction in predictions:
        values = (
            prediction.probabilities.p1,
            prediction.probabilities.draw,
            prediction.probabilities.p2,
        )
        predicted = max(range(3), key=values.__getitem__)
        rows.append((values[predicted], float(labels[predicted] == prediction.actual)))
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        selected = [
            row
            for row in rows
            if lower <= row[0] <= upper and (index == bins - 1 or row[0] < upper)
        ]
        if not selected:
            continue
        output.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                predictions=len(selected),
                average_confidence=sum(row[0] for row in selected) / len(selected),
                observed_accuracy=sum(row[1] for row in selected) / len(selected),
            )
        )
    return tuple(output)


class DatabaseMLDatasetBuilder:
    def __init__(self, feature_builder: DatabaseFeatureBuilder | None = None) -> None:
        self._feature_builder = feature_builder or DatabaseFeatureBuilder()

    async def build(
        self,
        session: AsyncSession,
        *,
        source_code: str,
        min_player_samples: int,
    ) -> MLDataset:
        if min_player_samples < 0:
            raise ValueError("min_player_samples must be non-negative")
        rows = (
            await session.execute(
                select(Event, Result)
                .join(Source, Source.id == Event.source_id)
                .join(Result, Result.event_id == Event.id)
                .where(
                    Source.code == source_code,
                    Event.player1_id.is_not(None),
                    Event.player2_id.is_not(None),
                )
                .order_by(Event.started_at, Event.id)
            )
        ).all()
        dataset: list[MLDataRow] = []
        skipped = 0
        for event, result in rows:
            features = await self._feature_builder.build(session, event_id=event.id)
            sample1 = round(features.values.get("p1_global_all_matches", 0.0))
            sample2 = round(features.values.get("p2_global_all_matches", 0.0))
            if min(sample1, sample2) < min_player_samples:
                skipped += 1
                continue
            if result.winner not in {"P1", "X", "P2"}:
                raise ValueError(f"Unsupported 1X2 result: {result.winner}")
            dataset.append(
                MLDataRow(
                    event_id=event.id,
                    started_at=self._aware(event.started_at),
                    actual=cast(Outcome, result.winner),
                    values=vectorize(features),
                )
            )
        return MLDataset(source_code, len(rows), skipped, tuple(dataset))

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class LogisticMLV1Evaluator:
    def run(
        self,
        dataset: MLDataset,
        splits: tuple[WalkForwardSplit, ...],
    ) -> MLEvaluationReport:
        if not splits:
            raise ValueError("at least one walk-forward split is required")
        folds: list[MLFoldEvaluation] = []
        out_of_sample: list[OutcomePrediction] = []
        for fold_index, split in enumerate(splits, start=1):
            train = self._slice(dataset.rows, split.train_start, split.train_end)
            validation = self._slice(
                dataset.rows,
                split.validation_start,
                split.validation_end,
            )
            test = self._slice(dataset.rows, split.test_start, split.test_end)
            if not train or not validation or not test:
                continue
            self._require_all_classes(train, "train")
            self._require_all_classes(validation, "validation")
            base = make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=1000, solver="lbfgs"),
            )
            train_x, train_y = self._arrays(train)
            base.fit(train_x, train_y)
            estimators: dict[CalibrationMethod, ProbabilityEstimator] = {
                "uncalibrated": base,
            }
            validation_x, validation_y = self._arrays(validation)
            for name, method in (("platt", "sigmoid"), ("isotonic", "isotonic")):
                calibrated = CalibratedClassifierCV(
                    FrozenEstimator(base),
                    method=method,
                )
                calibrated.fit(validation_x, validation_y)
                estimators[cast(CalibrationMethod, name)] = calibrated

            validation_predictions = {
                name: self._predict(estimator, validation, model=name)
                for name, estimator in estimators.items()
            }
            validation_metrics = {
                name: calculate_probability_metrics(items)
                for name, items in validation_predictions.items()
            }
            selected = min(
                estimators,
                key=lambda name: (
                    validation_metrics[name].log_loss,
                    ("uncalibrated", "platt", "isotonic").index(name),
                ),
            )
            test_predictions = self._predict(estimators[selected], test, model="FON_ml_v1")
            out_of_sample.extend(test_predictions)
            folds.append(
                MLFoldEvaluation(
                    fold=fold_index,
                    train_start=split.train_start,
                    train_end=split.train_end,
                    validation_start=split.validation_start,
                    validation_end=split.validation_end,
                    test_start=split.test_start,
                    test_end=split.test_end,
                    train_rows=len(train),
                    validation_rows=len(validation),
                    test_rows=len(test),
                    validation_metrics=validation_metrics,
                    selected_calibration=selected,
                    test_metrics=calculate_probability_metrics(test_predictions),
                    reliability=reliability_bins(test_predictions),
                )
            )
        if not out_of_sample:
            raise ValueError("walk-forward splits produced no out-of-sample predictions")
        return MLEvaluationReport(
            model="logistic_regression_v1",
            source=dataset.source,
            feature_count=len(ML_FEATURE_NAMES),
            candidates_seen=dataset.candidates_seen,
            eligible_events=len(dataset.rows),
            skipped_for_samples=dataset.skipped_for_samples,
            folds=tuple(folds),
            out_of_sample=calculate_probability_metrics(out_of_sample),
            reliability=reliability_bins(out_of_sample),
            note=(
                "Each fold fits on train, chooses uncalibrated/Platt/isotonic by "
                "validation LogLoss, and scores once on the later test interval. "
                "No betting ROI is claimed without verified historical market odds."
            ),
        )

    @staticmethod
    def _slice(
        rows: tuple[MLDataRow, ...],
        start: datetime,
        end: datetime,
    ) -> list[MLDataRow]:
        return [row for row in rows if start <= row.started_at < end]

    @staticmethod
    def _arrays(
        rows: list[MLDataRow],
    ) -> tuple[np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.str_]]]:
        features = np.asarray([row.values for row in rows], dtype=np.float64)
        labels = np.asarray([row.actual for row in rows], dtype=np.str_)
        return features, labels

    @staticmethod
    def _require_all_classes(rows: list[MLDataRow], name: str) -> None:
        classes = {row.actual for row in rows}
        if classes != {"P1", "X", "P2"}:
            raise ValueError(f"{name} split must contain all 1X2 classes")

    @staticmethod
    def _predict(
        estimator: ProbabilityEstimator,
        rows: list[MLDataRow],
        *,
        model: str,
    ) -> list[OutcomePrediction]:
        features, _ = LogisticMLV1Evaluator._arrays(rows)
        raw_probabilities = np.asarray(estimator.predict_proba(features), dtype=np.float64)
        classes = [str(value) for value in estimator.classes_]
        indexes = {label: classes.index(label) for label in ("P1", "X", "P2")}
        output: list[OutcomePrediction] = []
        for row, probabilities in zip(rows, raw_probabilities, strict=True):
            output.append(
                OutcomePrediction(
                    event_id=row.event_id,
                    started_at=row.started_at,
                    model=model,
                    probabilities=OutcomeProbabilities(
                        float(probabilities[indexes["P1"]]),
                        float(probabilities[indexes["X"]]),
                        float(probabilities[indexes["P2"]]),
                    ),
                    actual=row.actual,
                    player1_samples=0,
                    player2_samples=0,
                    h2h_samples=0,
                )
            )
        return output
