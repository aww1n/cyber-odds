from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.backtest.baseline_evaluation import predict_baselines
from app.backtest.engine import BacktestEngine, BacktestOpportunity
from app.config.strategies import StrategySettings
from app.database.models import (
    Event,
    EventMatch,
    Market,
    ModelPrediction,
    OddsSnapshot,
    Signal,
    Source,
    Tournament,
)
from app.features import DatabaseFeatureBuilder, FeatureSet
from app.models.baseline import OutcomeProbabilities


@dataclass(frozen=True, slots=True)
class PredictionSignalBatch:
    matched_events: int
    eligible_snapshots: int
    predictions_created: int
    alerts_created: int
    skips_created: int


@dataclass(slots=True)
class _PendingSignalDecision:
    bookmaker_event_id: int
    prediction: ModelPrediction
    decision: str
    reasons: list[str]
    minimum_odds: Decimal
    safety_multiplier: Decimal
    stake_mode: str
    suggested_stake: Decimal | None
    bet_multiplier: Decimal | None
    expires_at: datetime
    created_at: datetime
    value_percent: Decimal


def probabilities_by_bookmaker_selection(
    probabilities: OutcomeProbabilities,
    *,
    reversed_sides: bool,
) -> dict[str, float]:
    """Orient source probabilities to the bookmaker's displayed participants."""

    if reversed_sides:
        return {
            "P1": probabilities.p2,
            "X": probabilities.draw,
            "P2": probabilities.p1,
        }
    return {
        "P1": probabilities.p1,
        "X": probabilities.draw,
        "P2": probabilities.p2,
    }


class PredictionSignalWorker:
    def __init__(
        self,
        *,
        feature_builder: DatabaseFeatureBuilder | None = None,
        backtest_engine: BacktestEngine | None = None,
    ) -> None:
        self._feature_builder = feature_builder or DatabaseFeatureBuilder()
        self._engine = backtest_engine or BacktestEngine()

    async def generate_once(
        self,
        session: AsyncSession,
        *,
        strategy_name: str,
        strategy: StrategySettings,
        now: datetime | None = None,
    ) -> PredictionSignalBatch:
        if strategy.model_name != "individual_h2h_fixed":
            raise ValueError("live prediction worker supports individual_h2h_fixed only")
        if set(strategy.allowed_markets) != {"1x2"}:
            raise ValueError("individual_h2h_fixed live worker requires allowed_markets=[1x2]")
        current = self._aware(now or datetime.now(UTC))
        source_event = aliased(Event)
        bookmaker_event = aliased(Event)
        source = aliased(Source)
        rows = (
            await session.execute(
                select(
                    EventMatch,
                    source_event,
                    bookmaker_event,
                    Tournament.name,
                )
                .join(source_event, source_event.id == EventMatch.source_event_id)
                .join(source, source.id == source_event.source_id)
                .join(bookmaker_event, bookmaker_event.id == EventMatch.bookmaker_event_id)
                .outerjoin(Tournament, Tournament.id == bookmaker_event.tournament_id)
                .where(
                    source.code == strategy.source,
                    EventMatch.status == "matched",
                    bookmaker_event.status == "scheduled",
                    bookmaker_event.started_at > current,
                )
                .order_by(bookmaker_event.started_at, bookmaker_event.id)
            )
        ).all()
        if not rows:
            return PredictionSignalBatch(0, 0, 0, 0, 0)

        bookmaker_ids = [event.id for _, _, event, _ in rows]
        odds_rows = (
            await session.execute(
                select(OddsSnapshot, Market.code)
                .join(Market, Market.id == OddsSnapshot.market_id)
                .where(
                    OddsSnapshot.event_id.in_(bookmaker_ids),
                    OddsSnapshot.received_at <= current,
                    Market.code.in_(strategy.allowed_markets),
                )
                .order_by(OddsSnapshot.received_at.desc(), OddsSnapshot.id.desc())
            )
        ).all()
        latest: dict[tuple[int, str, str, Decimal | None], OddsSnapshot] = {}
        for odds, market_code in odds_rows:
            key = (odds.event_id, market_code, odds.selection, odds.line)
            latest.setdefault(key, odds)

        snapshot_ids = [item.id for item in latest.values()]
        existing = set(
            (
                await session.scalars(
                    select(ModelPrediction.odds_snapshot_id).where(
                        ModelPrediction.odds_snapshot_id.in_(snapshot_ids),
                        ModelPrediction.model_name == strategy.model_name,
                        ModelPrediction.model_version == strategy.model_version,
                    )
                )
            ).all()
        )
        match_by_bookmaker = {
            bookmaker.id: (match, source_item, bookmaker, name)
            for match, source_item, bookmaker, name in rows
        }
        features_cache: dict[tuple[int, datetime], FeatureSet] = {}
        pending_by_event: dict[int, list[_PendingSignalDecision]] = {}
        predictions_created = alerts_created = skips_created = 0
        for (bookmaker_id, market_code, selection, _), odds in latest.items():
            if odds.id in existing or selection not in {"P1", "X", "P2"}:
                continue
            match, source_item, bookmaker, tournament_name = match_by_bookmaker[bookmaker_id]
            cutoff = self._aware(odds.received_at)
            event_start = self._aware(bookmaker.started_at)
            if cutoff >= event_start:
                continue
            cache_key = (source_item.id, cutoff)
            features = features_cache.get(cache_key)
            if features is None:
                features = await self._feature_builder.build(
                    session,
                    event_id=source_item.id,
                    cutoff_at=cutoff,
                )
                features_cache[cache_key] = features
            probabilities = predict_baselines(features)[strategy.model_name]
            probability_by_selection = probabilities_by_bookmaker_selection(
                probabilities,
                reversed_sides=bool(match.components.get("reversed_sides")),
            )
            sample1 = round(features.values.get("p1_global_all_matches", 0.0))
            sample2 = round(features.values.get("p2_global_all_matches", 0.0))
            h2h_samples = round(features.values.get("h2h_all_matches", 0.0))
            opportunity = BacktestOpportunity(
                event_id=bookmaker.id,
                event_started_at=event_start,
                # The signal is decided now, not at the historical snapshot timestamp.
                # Keeping feature_cutoff_at/odds_received_at at the snapshot time preserves
                # leakage safety while allowing stale_after_seconds to actually reject old odds.
                decision_at=current,
                feature_cutoff_at=features.cutoff_at,
                odds_received_at=cutoff,
                odds_snapshot_id=odds.id,
                probability=probability_by_selection[selection],
                odds=float(odds.odds),
                market=market_code,
                selection=selection,
                tournament=tournament_name or "Unknown tournament",
                sample_size=min(sample1, sample2),
                h2h_samples=h2h_samples,
                match_confidence=float(match.confidence),
                score1=None,
                score2=None,
            )
            simulated = self._engine.run(
                [opportunity],
                strategy.backtest_config(),
            ).predictions[0]
            decision = simulated.decision
            reasons = list(simulated.filter_reasons)
            if decision == "alert" and not strategy.alerts_enabled:
                decision = "skip"
                reasons.append("alerts_disabled")
            delivery_expires_at = min(
                event_start,
                cutoff + timedelta(seconds=strategy.stale_after_seconds),
            )
            if (
                decision == "alert"
                and (delivery_expires_at - current).total_seconds()
                < strategy.min_alert_lead_seconds
            ):
                # The odds can technically still pass stale_after_seconds while
                # having only a fraction of a publisher interval left. Do not
                # create an alert that is likely to expire before Telegram can
                # attempt it.
                decision = "skip"
                reasons.append("insufficient_delivery_window")
            prediction = ModelPrediction(
                event_id=bookmaker.id,
                event_match_id=match.id,
                mapping_reversed_sides=bool(match.components.get("reversed_sides")),
                odds_snapshot_id=odds.id,
                model_name=strategy.model_name,
                model_version=strategy.model_version,
                market_code=market_code,
                selection=selection,
                probability=self._decimal(simulated.metrics.probability),
                fair_odds=self._decimal(simulated.metrics.fair_odds),
                value_ratio=self._decimal(simulated.metrics.value_ratio),
                value_percent=self._decimal(simulated.metrics.value_percent),
                display_odds=odds.odds,
                sample_size=min(sample1, sample2),
                features=features.values,
                anomaly_flags=list(simulated.anomaly_flags),
                feature_cutoff_at=cutoff,
                created_at=current,
            )

            # generate_once can run from the scheduler while an operator also
            # invokes ``python -m app predict``. The database unique constraint
            # remains the source of truth for prediction idempotency.
            try:
                async with session.begin_nested():
                    session.add(prediction)
                    await session.flush()
            except IntegrityError as exc:
                if "uq_prediction_snapshot_model_selection" not in str(exc):
                    raise
                continue

            pending_by_event.setdefault(bookmaker.id, []).append(
                _PendingSignalDecision(
                    bookmaker_event_id=bookmaker.id,
                    prediction=prediction,
                    decision=decision,
                    reasons=reasons,
                    minimum_odds=self._decimal(simulated.minimum_odds),
                    safety_multiplier=self._decimal(strategy.safety_multiplier),
                    stake_mode=strategy.stake_mode,
                    suggested_stake=(
                        self._decimal(simulated.suggested_stake)
                        if simulated.suggested_stake is not None
                        else None
                    ),
                    bet_multiplier=(
                        self._decimal(simulated.bet_multiplier)
                        if simulated.bet_multiplier is not None
                        else None
                    ),
                    expires_at=delivery_expires_at,
                    created_at=current,
                    value_percent=self._decimal(simulated.metrics.value_percent),
                )
            )
            predictions_created += 1

        # 1X2 outcomes are mutually exclusive. Preserve every prediction for
        # research, but issue at most one real betting call per event/strategy:
        # the qualifying selection with the highest value edge in this batch.
        for bookmaker_id, candidates in pending_by_event.items():
            alert_candidates = [item for item in candidates if item.decision == "alert"]
            best_alert = (
                max(
                    alert_candidates,
                    key=lambda item: (
                        item.value_percent,
                        item.prediction.probability,
                        -item.prediction.id,
                    ),
                )
                if alert_candidates
                else None
            )
            for item in candidates:
                signal_kwargs = dict(
                    prediction_id=item.prediction.id,
                    strategy=strategy_name,
                    minimum_odds=item.minimum_odds,
                    safety_multiplier=item.safety_multiplier,
                    stake_mode=item.stake_mode,
                    suggested_stake=item.suggested_stake,
                    bet_multiplier=item.bet_multiplier,
                    expires_at=item.expires_at,
                    created_at=item.created_at,
                )
                if item is best_alert:
                    # DB-level event key protects against scheduler/manual races
                    # and later odds snapshots for the same match.
                    alert_key = f"{strategy_name}:{bookmaker_id}"
                    try:
                        async with session.begin_nested():
                            session.add(
                                Signal(
                                    **signal_kwargs,
                                    decision="alert",
                                    alert_key=alert_key,
                                    filter_reasons=item.reasons,
                                )
                            )
                            await session.flush()
                    except IntegrityError as exc:
                        if not self._is_duplicate_alert(exc):
                            raise
                        session.add(
                            Signal(
                                **signal_kwargs,
                                decision="skip",
                                alert_key=None,
                                filter_reasons=[*item.reasons, "duplicate_alert"],
                            )
                        )
                        skips_created += 1
                    else:
                        alerts_created += 1
                    continue

                reasons = list(item.reasons)
                if item.decision == "alert":
                    reasons.append("better_selection_available")
                session.add(
                    Signal(
                        **signal_kwargs,
                        decision="skip",
                        alert_key=None,
                        filter_reasons=reasons,
                    )
                )
                skips_created += 1

        return PredictionSignalBatch(
            matched_events=len(rows),
            eligible_snapshots=len(latest),
            predictions_created=predictions_created,
            alerts_created=alerts_created,
            skips_created=skips_created,
        )

    @staticmethod
    def _is_duplicate_alert(exc: IntegrityError) -> bool:
        message = str(exc).casefold()
        return "ux_signals_alert_key" in message or "signals.alert_key" in message

    @staticmethod
    def _decimal(value: float) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
