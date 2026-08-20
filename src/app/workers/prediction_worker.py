from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.backtest.baseline_evaluation import predict_baselines, predict_total_probability
from app.backtest.engine import BacktestEngine, BacktestOpportunity
from app.backtest.settlement import normalize_market_selection
from app.config.settings import get_settings
from app.config.strategies import StrategySettings
from app.corridors.repository import CorridorRepository
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
from app.models.statistical import TotalSelection


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
    bookmaker_external_id: str
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
    line: Decimal | None


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
        corridor_repository: CorridorRepository | None = None,
    ) -> None:
        self._feature_builder = feature_builder or DatabaseFeatureBuilder()
        self._engine = backtest_engine or BacktestEngine()
        self._corridors = corridor_repository or CorridorRepository()

    async def generate_once(
        self,
        session: AsyncSession,
        *,
        strategy_name: str,
        strategy: StrategySettings,
        now: datetime | None = None,
    ) -> PredictionSignalBatch:
        allowed_markets = set(strategy.allowed_markets)
        if strategy.model_name == "individual_h2h_fixed":
            if allowed_markets != {"1x2"}:
                raise ValueError(
                    "individual_h2h_fixed live worker requires allowed_markets=[1x2]"
                )
        elif strategy.model_name == "poisson_goals":
            if allowed_markets != {"total"}:
                raise ValueError("poisson_goals live worker requires allowed_markets=[total]")
        else:
            raise ValueError(
                "live prediction worker supports individual_h2h_fixed and poisson_goals"
            )
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
                    Tournament.family,
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

        bookmaker_ids = [event.id for _, _, event, _, _ in rows]
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
            try:
                selection = normalize_market_selection(
                    odds.selection,
                    market=market_code,
                    line=odds.line,
                )
            except ValueError:
                continue
            key = (odds.event_id, market_code, selection, odds.line)
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
            bookmaker.id: (match, source_item, bookmaker, name, family)
            for match, source_item, bookmaker, name, family in rows
        }
        features_cache: dict[tuple[int, datetime], FeatureSet] = {}
        pending_by_event: dict[int, list[_PendingSignalDecision]] = {}
        predictions_created = alerts_created = skips_created = 0
        for (bookmaker_id, market_code, selection, _), odds in latest.items():
            valid_selection = (
                selection in {"P1", "X", "P2"}
                if market_code == "1x2"
                else selection in {"over", "under"} and odds.line is not None
            )
            if odds.id in existing or not valid_selection:
                continue
            (
                match,
                source_item,
                bookmaker,
                tournament_name,
                tournament_family,
            ) = match_by_bookmaker[bookmaker_id]
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
            prediction_features: dict[str, object] = dict(features.values)
            if market_code == "1x2":
                probabilities = predict_baselines(features)[strategy.model_name]
                probability = probabilities_by_bookmaker_selection(
                    probabilities,
                    reversed_sides=bool(match.components.get("reversed_sides")),
                )[selection]
            else:
                assert odds.line is not None
                total_probabilities = predict_total_probability(
                    features,
                    line=float(odds.line),
                    selection=cast(TotalSelection, selection),
                )
                probability = total_probabilities.conditional_win
                prediction_features["total_win_probability"] = total_probabilities.win
                prediction_features["total_push_probability"] = total_probabilities.push
                prediction_features["total_loss_probability"] = total_probabilities.loss
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
                probability=probability,
                odds=odds.odds,
                market=market_code,
                selection=selection,
                tournament=tournament_name or "Unknown tournament",
                sample_size=min(sample1, sample2),
                h2h_samples=h2h_samples,
                match_confidence=float(match.confidence),
                score1=None,
                score2=None,
                line=odds.line,
            )
            simulated = self._engine.run(
                [opportunity],
                strategy.backtest_config(),
            ).predictions[0]
            decision = simulated.decision
            reasons = list(simulated.filter_reasons)
            corridor = await self._corridors.lookup(
                session,
                bookmaker_source_id=odds.bookmaker_source_id,
                sport=bookmaker.sport,
                game=bookmaker.game,
                tournament_family=tournament_family,
                market_code=market_code,
                selection=selection,
                line=odds.line,
                odds=odds.odds,
                cutoff_at=current,
                min_samples=strategy.corridor_min_samples,
            )
            if corridor is None:
                prediction_features["corridor"] = {"status": "insufficient"}
                if decision == "alert" and strategy.corridor_required_for_alert:
                    decision = "skip"
                    reasons.append("corridor_unavailable")
            else:
                prediction_features["corridor"] = {
                    "status": corridor.verdict,
                    "scope_type": corridor.scope_type,
                    "scope_value": corridor.scope_value,
                    "odds_min": str(corridor.odds_min),
                    "odds_max": str(corridor.odds_max),
                    "sample_size": corridor.sample_size,
                    "wins": corridor.wins,
                    "losses": corridor.losses,
                    "returns": corridor.returns,
                    "win_rate": str(corridor.win_rate),
                    "roi_percent": str(corridor.roi_percent),
                    "confidence": str(corridor.confidence),
                }
                if (
                    decision == "alert"
                    and strategy.corridor_required_for_alert
                    and corridor.verdict != "confirm"
                ):
                    decision = "skip"
                    reasons.append("corridor_not_confirmed")
            if decision == "alert" and not strategy.alerts_enabled:
                decision = "skip"
                reasons.append("alerts_disabled")
            delivery_expires_at = min(
                event_start,
                cutoff + timedelta(seconds=strategy.stale_after_seconds),
            )

            # Check insufficient delivery window (critical timing constraint)
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
                features=prediction_features,
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
                    bookmaker_external_id=bookmaker.external_id,
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
                    line=odds.line,
                )
            )
            predictions_created += 1

        settings = get_settings()
        bankroll = settings.bankroll.quantize(Decimal("0.01"))
        for candidates in pending_by_event.values():
            alert_candidates = [item for item in candidates if item.decision == "alert"]
            best_by_market: dict[str, _PendingSignalDecision] = {}
            for candidate in alert_candidates:
                current_best = best_by_market.get(candidate.prediction.market_code)
                ranking = (
                    candidate.value_percent,
                    candidate.prediction.probability,
                    -candidate.prediction.id,
                )
                if current_best is None or ranking > (
                    current_best.value_percent,
                    current_best.prediction.probability,
                    -current_best.prediction.id,
                ):
                    best_by_market[candidate.prediction.market_code] = candidate
            selected_alerts = set(
                id(item)
                for item in sorted(
                    best_by_market.values(),
                    key=lambda item: (item.value_percent, item.prediction.probability),
                    reverse=True,
                )[: strategy.max_alerts_per_event]
            )
            for item in candidates:
                multiplier = item.bet_multiplier or Decimal("1")
                stake_percent = settings.default_stake_percent * multiplier
                stake_percent = min(
                    settings.max_stake_percent,
                    max(settings.min_stake_percent, stake_percent),
                ).quantize(Decimal("0.01"))
                stake_amount = (
                    bankroll * stake_percent / Decimal("100")
                ).quantize(Decimal("0.01"))

                signal_kwargs = dict(
                    prediction_id=item.prediction.id,
                    strategy=strategy_name,
                    minimum_odds=item.minimum_odds,
                    safety_multiplier=item.safety_multiplier,
                    stake_mode=item.stake_mode,
                    suggested_stake=item.suggested_stake,
                    bet_multiplier=item.bet_multiplier,
                    bankroll_at_signal=bankroll,
                    stake_percent=stake_percent,
                    stake_amount=stake_amount,
                    expires_at=item.expires_at,
                    created_at=item.created_at,
                )
                if id(item) in selected_alerts:
                    market = item.prediction.market_code
                    selection = item.prediction.selection
                    line_key = (
                        format(item.line.normalize(), "f")
                        if item.line is not None
                        else "-"
                    )
                    alert_key = (
                        f"{strategy_name}:{item.bookmaker_external_id}:"
                        f"{market}:{selection}:{line_key}"
                    )
                    inserted = await self._insert_alert_atomic(
                        session,
                        values={
                            **signal_kwargs,
                            "decision": "alert",
                            "alert_key": alert_key,
                            "filter_reasons": item.reasons,
                        },
                    )
                    if not inserted:
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
    async def _insert_alert_atomic(
        session: AsyncSession,
        *,
        values: dict[str, object],
    ) -> bool:
        """Insert an alert without raising a unique-key error under concurrency."""
        if session.bind is None:
            raise RuntimeError("session is not bound to a database engine")
        dialect = session.bind.dialect.name
        statement: Any
        if dialect == "postgresql":
            statement = postgresql_insert(Signal).values(**values)
        elif dialect == "sqlite":
            statement = sqlite_insert(Signal).values(**values)
        else:  # pragma: no cover - supported deployments use PostgreSQL/SQLite
            raise RuntimeError(f"unsupported database dialect: {dialect}")
        statement = statement.on_conflict_do_nothing(
            index_elements=[Signal.alert_key]
        ).returning(Signal.id)
        return (await session.scalar(statement)) is not None

    @staticmethod
    def _decimal(value: Decimal | float) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
