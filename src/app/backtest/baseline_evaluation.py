from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.backtest.settlement import normalize_market_selection, settle_market
from app.backtest.walk_forward import WalkForwardSplit
from app.database.models import (
    Event,
    EventMatch,
    Market,
    OddsSnapshot,
    Result,
    Source,
)
from app.features import RESEARCH_WINDOWS, DatabaseFeatureBuilder, FeatureSet
from app.models.baseline import OutcomeProbabilities, blend_h2h_and_individual
from app.models.statistical import (
    TotalProbabilities,
    TotalSelection,
    poisson_outcomes,
    poisson_total_probabilities,
)

Outcome = Literal["P1", "X", "P2"]


@dataclass(frozen=True, slots=True)
class OutcomePrediction:
    event_id: int
    started_at: datetime
    model: str
    probabilities: OutcomeProbabilities
    actual: Outcome
    player1_samples: int
    player2_samples: int
    h2h_samples: int


@dataclass(frozen=True, slots=True)
class ProbabilityMetrics:
    predictions: int
    log_loss: float
    brier_score: float
    calibration_error: float
    accuracy: float


@dataclass(frozen=True, slots=True)
class FoldEvaluation:
    fold: int
    model: str
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    train_predictions: int
    validation_predictions: int
    metrics: ProbabilityMetrics


@dataclass(frozen=True, slots=True)
class BaselineEvaluationReport:
    source: str
    candidates_seen: int
    eligible_events: int
    skipped_for_samples: int
    folds: tuple[FoldEvaluation, ...]
    out_of_sample: dict[str, ProbabilityMetrics]
    note: str
    market_backtest: dict[str, MarketBacktestMetrics] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketBacktestMetrics:
    bets: int
    wins: int
    losses: int
    returns: int
    hit_rate_percent: Decimal
    total_stake: Decimal
    profit: Decimal
    roi_percent: Decimal
    yield_percent: Decimal


@dataclass(slots=True)
class _MarketAggregate:
    bets: int = 0
    wins: int = 0
    losses: int = 0
    returns: int = 0
    profit: Decimal = Decimal("0")

    def add(self, *, outcome: str, odds: Decimal) -> None:
        self.bets += 1
        if outcome == "win":
            self.wins += 1
            self.profit += odds - Decimal("1")
        elif outcome == "loss":
            self.losses += 1
            self.profit -= Decimal("1")
        elif outcome == "return":
            self.returns += 1
        else:
            raise ValueError(f"Unsupported backtest outcome: {outcome}")

    def metrics(self) -> MarketBacktestMetrics:
        stake = Decimal(self.bets)
        decisive = self.wins + self.losses
        hit_rate = (
            Decimal("100") * Decimal(self.wins) / Decimal(decisive)
            if decisive
            else Decimal("0")
        )
        roi = self.profit / stake * 100 if stake else Decimal("0")
        return MarketBacktestMetrics(
            bets=self.bets,
            wins=self.wins,
            losses=self.losses,
            returns=self.returns,
            hit_rate_percent=hit_rate,
            total_stake=stake,
            profit=self.profit,
            roi_percent=roi,
            yield_percent=roi,
        )


def _feature(features: FeatureSet, key: str) -> float:
    return features.values.get(key, 0.0)


def _smoothed_distribution(features: FeatureSet, prefix: str) -> OutcomeProbabilities:
    wins = _feature(features, f"{prefix}_wins")
    draws = _feature(features, f"{prefix}_draws")
    losses = _feature(features, f"{prefix}_losses")
    denominator = wins + draws + losses + 3.0
    return OutcomeProbabilities(
        (wins + 1.0) / denominator,
        (draws + 1.0) / denominator,
        (losses + 1.0) / denominator,
    )


def _orient_player2(distribution: OutcomeProbabilities) -> OutcomeProbabilities:
    return OutcomeProbabilities(distribution.p2, distribution.draw, distribution.p1)


def _mean_distribution(
    first: OutcomeProbabilities,
    second: OutcomeProbabilities,
) -> OutcomeProbabilities:
    return OutcomeProbabilities(
        (first.p1 + second.p1) / 2,
        (first.draw + second.draw) / 2,
        (first.p2 + second.p2) / 2,
    )


def _weighted_recent_distribution(
    features: FeatureSet,
    side: str,
    *,
    windows: tuple[tuple[str, float], ...] = (
        ("last_10", 0.5),
        ("last_30", 0.3),
        ("all", 0.2),
    ),
) -> OutcomeProbabilities:
    distributions = [
        (_smoothed_distribution(features, f"{side}_global_{window}"), weight)
        for window, weight in windows
    ]
    total_weight = sum(weight for _, weight in distributions)
    return OutcomeProbabilities(
        sum(item.p1 * weight for item, weight in distributions) / total_weight,
        sum(item.draw * weight for item, weight in distributions) / total_weight,
        sum(item.p2 * weight for item, weight in distributions) / total_weight,
    )


def predict_baselines(features: FeatureSet) -> dict[str, OutcomeProbabilities]:
    player1 = _smoothed_distribution(features, "p1_global_all")
    player2 = _orient_player2(_smoothed_distribution(features, "p2_global_all"))
    player_frequency = _mean_distribution(player1, player2)

    recent_player1 = _weighted_recent_distribution(features, "p1")
    recent_player2 = _orient_player2(_weighted_recent_distribution(features, "p2"))
    weighted_recent = _mean_distribution(recent_player1, recent_player2)

    h2h = _smoothed_distribution(features, "h2h_all")
    h2h_individual = blend_h2h_and_individual(
        player1=player1,
        player2=player2,
        h2h=h2h,
        weights=(0.35, 0.35, 0.30),
    )

    goals1_rate = (
        _feature(features, "p1_global_all_goals_for_avg")
        + _feature(features, "p2_global_all_goals_against_avg")
    ) / 2
    goals2_rate = (
        _feature(features, "p2_global_all_goals_for_avg")
        + _feature(features, "p1_global_all_goals_against_avg")
    ) / 2
    predictions = {
        "uniform": OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3),
        "player_frequency": player_frequency,
        "weighted_recent_10_30_all": weighted_recent,
        "individual_h2h_fixed": h2h_individual,
        "poisson_goals": poisson_outcomes(
            goals1_rate=goals1_rate,
            goals2_rate=goals2_rate,
        ),
    }
    for window in RESEARCH_WINDOWS:
        label = f"last_{window}"
        recent_p1 = _smoothed_distribution(features, f"p1_global_{label}")
        recent_p2 = _orient_player2(
            _smoothed_distribution(features, f"p2_global_{label}")
        )
        predictions[f"player_frequency_{label}"] = _mean_distribution(
            recent_p1,
            recent_p2,
        )
        predictions[f"h2h_{label}"] = _smoothed_distribution(
            features,
            f"h2h_{label}",
        )
    return predictions


def predict_total_probability(
    features: FeatureSet,
    *,
    line: float,
    selection: TotalSelection,
) -> TotalProbabilities:
    """Predict a total from historical score totals, independently of 1X2.

    The individual-player expectation is blended with H2H only as H2H sample
    depth grows.  All input aggregates are produced by ``FeatureBuilder`` using
    rows available strictly before the prediction cutoff.
    """

    player_total = (
        _feature(features, "p1_global_all_total_avg")
        + _feature(features, "p2_global_all_total_avg")
    ) / 2
    h2h_samples = _feature(features, "h2h_all_matches")
    h2h_total = _feature(features, "h2h_all_total_avg")
    h2h_weight = h2h_samples / (h2h_samples + 20.0) if h2h_samples > 0 else 0.0
    expected_total = (1.0 - h2h_weight) * player_total + h2h_weight * h2h_total
    # A zero rate would create an invalid persisted probability for OVER.  It
    # can occur only without useful history and is still rejected by min_samples.
    expected_total = max(expected_total, 1e-9)
    return poisson_total_probabilities(
        goals1_rate=expected_total,
        goals2_rate=0.0,
        line=line,
        selection=selection,
    )


def calculate_probability_metrics(
    predictions: list[OutcomePrediction],
    *,
    calibration_bins: int = 10,
) -> ProbabilityMetrics:
    if not predictions:
        raise ValueError("at least one prediction is required")
    if calibration_bins <= 0:
        raise ValueError("calibration_bins must be positive")
    epsilon = 1e-15
    log_losses: list[float] = []
    brier_scores: list[float] = []
    confidence_rows: list[tuple[float, float]] = []
    correct = 0
    labels: tuple[Outcome, ...] = ("P1", "X", "P2")
    for prediction in predictions:
        values = (
            prediction.probabilities.p1,
            prediction.probabilities.draw,
            prediction.probabilities.p2,
        )
        actual_index = labels.index(prediction.actual)
        actual_probability = max(epsilon, min(1 - epsilon, values[actual_index]))
        log_losses.append(-math.log(actual_probability))
        brier_scores.append(
            sum(
                (probability - (1.0 if index == actual_index else 0.0)) ** 2
                for index, probability in enumerate(values)
            )
        )
        predicted_index = max(range(3), key=values.__getitem__)
        is_correct = float(predicted_index == actual_index)
        correct += int(is_correct)
        confidence_rows.append((values[predicted_index], is_correct))

    calibration_error = 0.0
    for bin_index in range(calibration_bins):
        lower = bin_index / calibration_bins
        upper = (bin_index + 1) / calibration_bins
        rows = [
            row
            for row in confidence_rows
            if lower <= row[0] <= upper
            and (bin_index == calibration_bins - 1 or row[0] < upper)
        ]
        if rows:
            average_confidence = sum(row[0] for row in rows) / len(rows)
            observed_accuracy = sum(row[1] for row in rows) / len(rows)
            calibration_error += (
                len(rows) / len(confidence_rows)
            ) * abs(average_confidence - observed_accuracy)
    return ProbabilityMetrics(
        predictions=len(predictions),
        log_loss=sum(log_losses) / len(log_losses),
        brier_score=sum(brier_scores) / len(brier_scores),
        calibration_error=calibration_error,
        accuracy=correct / len(predictions),
    )


def evaluate_walk_forward(
    predictions: list[OutcomePrediction],
    splits: tuple[WalkForwardSplit, ...],
) -> tuple[tuple[FoldEvaluation, ...], dict[str, ProbabilityMetrics]]:
    if not splits:
        raise ValueError("at least one walk-forward split is required")
    models = sorted({item.model for item in predictions})
    folds: list[FoldEvaluation] = []
    out_of_sample: dict[str, list[OutcomePrediction]] = {model: [] for model in models}
    for fold_index, split in enumerate(splits, start=1):
        for model in models:
            model_rows = [item for item in predictions if item.model == model]
            train_rows = [
                item
                for item in model_rows
                if split.train_start <= item.started_at < split.train_end
            ]
            validation_rows = [
                item
                for item in model_rows
                if split.validation_start <= item.started_at < split.validation_end
            ]
            test_rows = [
                item
                for item in model_rows
                if split.test_start <= item.started_at < split.test_end
            ]
            if not test_rows:
                continue
            out_of_sample[model].extend(test_rows)
            folds.append(
                FoldEvaluation(
                    fold=fold_index,
                    model=model,
                    train_start=split.train_start,
                    train_end=split.train_end,
                    validation_start=split.validation_start,
                    validation_end=split.validation_end,
                    test_start=split.test_start,
                    test_end=split.test_end,
                    train_predictions=len(train_rows),
                    validation_predictions=len(validation_rows),
                    metrics=calculate_probability_metrics(test_rows),
                )
            )
    metrics = {
        model: calculate_probability_metrics(rows)
        for model, rows in out_of_sample.items()
        if rows
    }
    return tuple(folds), metrics


class DatabaseBaselineEvaluator:
    def __init__(self, feature_builder: DatabaseFeatureBuilder | None = None) -> None:
        self._feature_builder = feature_builder or DatabaseFeatureBuilder()

    async def run(
        self,
        session: AsyncSession,
        *,
        source_code: str,
        min_player_samples: int,
        splits: tuple[WalkForwardSplit, ...],
    ) -> BaselineEvaluationReport:
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
        predictions: list[OutcomePrediction] = []
        eligible_events = 0
        skipped = 0
        for event, result in rows:
            features = await self._feature_builder.build(session, event_id=event.id)
            player1_samples = round(_feature(features, "p1_global_all_matches"))
            player2_samples = round(_feature(features, "p2_global_all_matches"))
            h2h_samples = round(_feature(features, "h2h_all_matches"))
            if min(player1_samples, player2_samples) < min_player_samples:
                skipped += 1
                continue
            eligible_events += 1
            if result.winner not in {"P1", "X", "P2"}:
                raise ValueError(f"Unsupported 1X2 result: {result.winner}")
            actual = cast(Outcome, result.winner)
            for model, probabilities in predict_baselines(features).items():
                predictions.append(
                    OutcomePrediction(
                        event_id=event.id,
                        started_at=self._aware(event.started_at),
                        model=model,
                        probabilities=probabilities,
                        actual=actual,
                        player1_samples=player1_samples,
                        player2_samples=player2_samples,
                        h2h_samples=h2h_samples,
                    )
                )
        folds, out_of_sample = evaluate_walk_forward(predictions, splits)
        market_backtest = await self._market_backtest(
            session,
            source_code=source_code,
            min_player_samples=min_player_samples,
        )
        return BaselineEvaluationReport(
            source=source_code,
            candidates_seen=len(rows),
            eligible_events=eligible_events,
            skipped_for_samples=skipped,
            folds=folds,
            out_of_sample=out_of_sample,
            note=(
                "Probability metrics use walk-forward test intervals. Market ROI uses only "
                "matched, latest valid pre-match Fonbet snapshots and is empty when that "
                "historical corpus is unavailable."
            ),
            market_backtest=market_backtest,
        )

    async def _market_backtest(
        self,
        session: AsyncSession,
        *,
        source_code: str,
        min_player_samples: int,
    ) -> dict[str, MarketBacktestMetrics]:
        source_event = aliased(Event)
        bookmaker_event = aliased(Event)
        snapshot_rank = func.row_number().over(
            partition_by=(
                EventMatch.id,
                Market.code,
                OddsSnapshot.selection,
                OddsSnapshot.line,
            ),
            order_by=(OddsSnapshot.received_at.desc(), OddsSnapshot.id.desc()),
        ).label("snapshot_rank")
        ranked = (
            select(
                EventMatch.id.label("event_match_id"),
                EventMatch.source_event_id,
                EventMatch.confidence,
                EventMatch.components,
                bookmaker_event.started_at.label("bookmaker_started_at"),
                OddsSnapshot.id.label("odds_snapshot_id"),
                OddsSnapshot.received_at,
                OddsSnapshot.selection,
                OddsSnapshot.line,
                OddsSnapshot.odds,
                Market.code.label("market_code"),
                Result.score1,
                Result.score2,
                snapshot_rank,
            )
            .select_from(EventMatch)
            .join(source_event, source_event.id == EventMatch.source_event_id)
            .join(Source, Source.id == source_event.source_id)
            .join(Result, Result.event_id == source_event.id)
            .join(bookmaker_event, bookmaker_event.id == EventMatch.bookmaker_event_id)
            .join(OddsSnapshot, OddsSnapshot.event_id == bookmaker_event.id)
            .join(Market, Market.id == OddsSnapshot.market_id)
            .where(
                Source.code == source_code,
                EventMatch.status == "matched",
                Market.code.in_(("1x2", "total")),
                OddsSnapshot.received_at < bookmaker_event.started_at,
                OddsSnapshot.received_at
                >= bookmaker_event.started_at - timedelta(minutes=15),
            )
            .subquery()
        )
        rows = (
            await session.execute(select(ranked).where(ranked.c.snapshot_rank == 1))
        ).mappings().all()
        aggregates: dict[str, _MarketAggregate] = defaultdict(_MarketAggregate)
        for row in rows:
            market = str(row["market_code"])
            line = None if row["line"] is None else Decimal(str(row["line"]))
            try:
                selection = normalize_market_selection(
                    str(row["selection"]),
                    market=market,
                    line=line,
                )
            except ValueError:
                continue
            if market == "1x2" and selection not in {"P1", "X", "P2"}:
                continue
            if market == "total" and (selection not in {"over", "under"} or line is None):
                continue
            cutoff = self._aware(row["received_at"])
            features = await self._feature_builder.build(
                session,
                event_id=int(row["source_event_id"]),
                cutoff_at=cutoff,
            )
            sample_size = min(
                round(_feature(features, "p1_global_all_matches")),
                round(_feature(features, "p2_global_all_matches")),
            )
            if sample_size < min_player_samples:
                continue
            reversed_sides = bool((row["components"] or {}).get("reversed_sides"))
            if market == "1x2":
                outcome_probabilities = predict_baselines(features)["individual_h2h_fixed"]
                probabilities_by_selection = {
                    "P1": outcome_probabilities.p1,
                    "X": outcome_probabilities.draw,
                    "P2": outcome_probabilities.p2,
                }
                if reversed_sides:
                    probabilities_by_selection = {
                        "P1": probabilities_by_selection["P2"],
                        "X": probabilities_by_selection["X"],
                        "P2": probabilities_by_selection["P1"],
                    }
                model_probability = probabilities_by_selection[selection]
            else:
                assert line is not None
                model_probability = predict_total_probability(
                    features,
                    line=float(line),
                    selection=cast(TotalSelection, selection),
                ).conditional_win
            odds = Decimal(str(row["odds"]))
            model_probability = max(model_probability, 1e-12)
            fair_odds = Decimal("1") / Decimal(str(model_probability))
            if odds <= fair_odds:
                continue
            score1, score2 = int(row["score1"]), int(row["score2"])
            if reversed_sides:
                score1, score2 = score2, score1
            outcome = settle_market(
                selection,
                score1=score1,
                score2=score2,
                market=market,
                line=line,
            )
            keys = [market if market == "1x2" else f"total_{selection}"]
            if market == "total" and line is not None:
                keys.append(f"total_{selection}_{format(line.normalize(), 'f')}")
            for key in keys:
                aggregates[key].add(outcome=outcome, odds=odds)
        return {key: value.metrics() for key, value in sorted(aggregates.items())}

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
