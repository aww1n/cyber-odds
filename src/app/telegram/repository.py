from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database.models import (
    Event,
    EventMatch,
    EventParticipant,
    ModelPrediction,
    OddsSnapshot,
    ParserRun,
    Player,
    Result,
    Settlement,
    Signal,
    Source,
    Tournament,
)
from app.telegram.formatter import (
    DailyStatsView,
    ModelStatsView,
    ParserHealthView,
    SettlementView,
    SignalAlertView,
)


@dataclass(frozen=True, slots=True)
class PendingAlert:
    signal_id: int
    view: SignalAlertView


@dataclass(frozen=True, slots=True)
class PendingSettlement:
    settlement_id: int
    reply_to_message_id: int
    view: SettlementView


@dataclass(frozen=True, slots=True)
class SignalSummary:
    created_at: datetime
    started_at: datetime
    decision: str
    strategy: str
    model: str
    selection: str
    odds: Decimal
    value_percent: Decimal
    external_id: str


@dataclass(frozen=True, slots=True)
class ResultSummary:
    settled_at: datetime
    outcome: str
    score1: int
    score2: int
    profit: Decimal
    external_id: str


@dataclass(frozen=True, slots=True)
class SystemStatus:
    predictions: int
    pending_alerts: int
    sent_alerts: int
    last_alert_created_at: datetime | None
    last_alert_sent_at: datetime | None
    pending_settlements: int
    parsers_with_errors: int


@dataclass(frozen=True, slots=True)
class DataTotals:
    events: int
    results: int
    odds_snapshots: int
    players: int
    tournaments: int
    predictions: int


@dataclass(frozen=True, slots=True)
class AnalysisSummary:
    predictions: int
    alerts: int
    skips: int
    one_x_two_candidates: int
    total_candidates: int
    top_skip_reasons: tuple[tuple[str, int], ...]


class TelegramRepository:
    async def analysis_summary(self, session: AsyncSession) -> AnalysisSummary:
        predictions = int(
            await session.scalar(select(func.count(ModelPrediction.id))) or 0
        )
        market_rows = (
            await session.execute(
                select(ModelPrediction.market_code, func.count(ModelPrediction.id))
                .group_by(ModelPrediction.market_code)
            )
        ).all()
        markets = {str(market): int(count) for market, count in market_rows}
        signals = (await session.scalars(select(Signal))).all()
        alerts = sum(item.decision == "alert" for item in signals)
        skips = sum(item.decision == "skip" for item in signals)
        reasons = Counter(
            reason
            for item in signals
            if item.decision == "skip"
            for reason in (item.filter_reasons or [])
        )
        return AnalysisSummary(
            predictions=predictions,
            alerts=alerts,
            skips=skips,
            one_x_two_candidates=markets.get("1x2", 0),
            total_candidates=markets.get("total", 0),
            top_skip_reasons=tuple(reasons.most_common(10)),
        )

    async def data_totals(self, session: AsyncSession) -> DataTotals:
        return DataTotals(
            events=int(await session.scalar(select(func.count(Event.id))) or 0),
            results=int(await session.scalar(select(func.count(Result.id))) or 0),
            odds_snapshots=int(
                await session.scalar(select(func.count(OddsSnapshot.id))) or 0
            ),
            players=int(await session.scalar(select(func.count(Player.id))) or 0),
            tournaments=int(
                await session.scalar(select(func.count(Tournament.id))) or 0
            ),
            predictions=int(
                await session.scalar(select(func.count(ModelPrediction.id))) or 0
            ),
        )

    async def pending_alerts(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
        now: datetime | None = None,
        alert_window_minutes: int | None = None,
    ) -> tuple[PendingAlert, ...]:
        """Retrieve unsent alerts within the configured time window.

        Args:
            alert_window_minutes: Only return alerts for events starting within
                this many minutes from now. None = no window constraint.
        """
        current = self._aware(now or datetime.now(UTC))
        participant1 = aliased(EventParticipant)
        participant2 = aliased(EventParticipant)
        bookmaker = aliased(Source)

        # Build base query
        query = (
            select(
                Signal,
                ModelPrediction,
                OddsSnapshot,
                Event,
                Tournament.name,
                participant1.raw_name,
                participant2.raw_name,
                participant1.raw_team_name,
                participant2.raw_team_name,
                bookmaker.code,
            )
            .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
            .join(OddsSnapshot, OddsSnapshot.id == ModelPrediction.odds_snapshot_id)
            .join(Event, Event.id == ModelPrediction.event_id)
            .join(bookmaker, bookmaker.id == OddsSnapshot.bookmaker_source_id)
            .outerjoin(Tournament, Tournament.id == Event.tournament_id)
            .outerjoin(
                participant1,
                and_(participant1.event_id == Event.id, participant1.side == 1),
            )
            .outerjoin(
                participant2,
                and_(participant2.event_id == Event.id, participant2.side == 2),
            )
            .where(
                Signal.decision == "alert",
                Signal.sent_at.is_(None),
                Event.started_at > current,
                or_(Signal.expires_at.is_(None), Signal.expires_at > current),
            )
        )

        # Apply time window constraint if specified
        if alert_window_minutes is not None:
            max_start = current + timedelta(minutes=alert_window_minutes)
            query = query.where(Event.started_at <= max_start)

        # Order by event time (earliest first) then signal creation
        query = query.order_by(Event.started_at, Signal.id).limit(limit)

        rows = (await session.execute(query)).all()
        return tuple(
            PendingAlert(
                signal_id=signal.id,
                view=SignalAlertView(
                    source_tag=source_code,
                    sport=event.sport,
                    started_at=self._aware(event.started_at),
                    tournament=tournament_name or "",
                    event_format=event.format,
                    participant1=raw_player1 or "P1",
                    participant2=raw_player2 or "P2",
                    market=prediction.market_code,
                    selection=prediction.selection,
                    line=odds.line,
                    calculation_odds=odds.odds,
                    display_odds=prediction.display_odds,
                    probability=prediction.probability,
                    fair_odds=prediction.fair_odds,
                    value_percent=prediction.value_percent,
                    minimum_odds=signal.minimum_odds,
                    bet_multiplier=signal.bet_multiplier,
                    suggested_stake=signal.suggested_stake,
                    sample_size=prediction.sample_size,
                    model=f"{prediction.model_name} / {prediction.model_version}",
                    external_id=event.external_id,
                    bankroll_at_signal=signal.bankroll_at_signal,
                    stake_percent=signal.stake_percent,
                    stake_amount=signal.stake_amount,
                    game=event.game,
                    team1=raw_team1,
                    team2=raw_team2,
                    corridor=(
                        prediction.features.get("corridor")
                        if isinstance(prediction.features.get("corridor"), dict)
                        else None
                    ),
                    signal_id=signal.id,
                ),
            )
            for (
                signal,
                prediction,
                odds,
                event,
                tournament_name,
                raw_player1,
                raw_player2,
                raw_team1,
                raw_team2,
                source_code,
            ) in rows
        )

    async def expire_pending_alerts(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> int:
        """Demote queued alerts that can no longer be delivered at valid odds."""

        current = self._aware(now or datetime.now(UTC))
        rows = (
            await session.scalars(
                select(Signal)
                .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
                .join(Event, Event.id == ModelPrediction.event_id)
                .where(
                    Signal.decision == "alert",
                    Signal.sent_at.is_(None),
                    or_(
                        Event.started_at <= current,
                        and_(Signal.expires_at.is_not(None), Signal.expires_at <= current),
                    ),
                )
            )
        ).all()
        for signal in rows:
            signal.decision = "skip"
            signal.alert_key = None
            reasons = list(signal.filter_reasons or [])
            if "delivery_expired" not in reasons:
                reasons.append("delivery_expired")
            signal.filter_reasons = reasons
        return len(rows)

    async def pending_settlements(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
    ) -> tuple[PendingSettlement, ...]:
        participant1 = aliased(EventParticipant)
        participant2 = aliased(EventParticipant)
        rows = (
            await session.execute(
                select(
                    Settlement,
                    Signal.telegram_message_id,
                    ModelPrediction,
                    OddsSnapshot,
                    Event,
                    Result,
                    participant1.raw_name,
                    participant2.raw_name,
                    EventMatch.components,
                )
                .join(Signal, Signal.id == Settlement.signal_id)
                .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
                .join(OddsSnapshot, OddsSnapshot.id == ModelPrediction.odds_snapshot_id)
                .join(Event, Event.id == ModelPrediction.event_id)
                .join(Result, Result.id == Settlement.result_id)
                .outerjoin(EventMatch, EventMatch.id == ModelPrediction.event_match_id)
                .outerjoin(
                    participant1,
                    and_(participant1.event_id == Event.id, participant1.side == 1),
                )
                .outerjoin(
                    participant2,
                    and_(participant2.event_id == Event.id, participant2.side == 2),
                )
                .where(
                    Settlement.telegram_notified_at.is_(None),
                    Signal.decision == "alert",
                    Signal.sent_at.is_not(None),
                    Signal.telegram_message_id.is_not(None),
                )
                .order_by(Settlement.settled_at, Settlement.id)
                .limit(limit)
            )
        ).all()
        output: list[PendingSettlement] = []
        for (
            settlement,
            message_id,
            prediction,
            odds,
            event,
            result,
            raw_player1,
            raw_player2,
            match_components,
        ) in rows:
            if message_id is None:
                continue
            try:
                reply_to = int(message_id)
            except ValueError:
                continue
            score1, score2 = self._orient_score(
                result.score1,
                result.score2,
                match_components,
                frozen_reversed=prediction.mapping_reversed_sides,
            )
            output.append(
                PendingSettlement(
                    settlement_id=settlement.id,
                    reply_to_message_id=reply_to,
                    view=SettlementView(
                        external_id=event.external_id,
                        participant1=raw_player1 or "P1",
                        participant2=raw_player2 or "P2",
                        score1=score1,
                        score2=score2,
                        selection=prediction.selection,
                        odds=prediction.display_odds or odds.odds,
                        stake=settlement.stake,
                        payout=settlement.payout,
                        outcome=settlement.outcome,
                        profit=settlement.profit,
                        market=prediction.market_code,
                        line=odds.line,
                    ),
                )
            )
        return tuple(output)

    async def mark_alert_sent(
        self,
        session: AsyncSession,
        *,
        signal_id: int,
        message_id: int,
        sent_at: datetime,
    ) -> None:
        signal = await session.get(Signal, signal_id)
        if signal is None:
            raise ValueError(f"Unknown signal: {signal_id}")
        signal.telegram_message_id = str(message_id)
        signal.sent_at = sent_at

    async def mark_settlement_sent(
        self,
        session: AsyncSession,
        *,
        settlement_id: int,
        sent_at: datetime,
    ) -> None:
        settlement = await session.get(Settlement, settlement_id)
        if settlement is None:
            raise ValueError(f"Unknown settlement: {settlement_id}")
        settlement.telegram_notified_at = sent_at

    async def stats(
        self,
        session: AsyncSession,
        *,
        since: datetime | None,
        label: str,
        model_name: str | None = None,
    ) -> DailyStatsView:
        statement = (
            select(Signal, ModelPrediction, OddsSnapshot.odds, Settlement)
            .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
            .join(OddsSnapshot, OddsSnapshot.id == ModelPrediction.odds_snapshot_id)
            .outerjoin(Settlement, Settlement.signal_id == Signal.id)
            .where(Signal.decision == "alert", Signal.sent_at.is_not(None))
            .order_by(Signal.created_at)
        )
        if since is not None:
            statement = statement.where(Signal.created_at >= since)
        if model_name is not None:
            statement = statement.where(ModelPrediction.model_name == model_name)
        rows = (await session.execute(statement)).all()
        wins = losses = returns = 0
        total_stake = Decimal(0)
        profit = Decimal(0)
        odds_values: list[Decimal] = []
        grouped: dict[str, list[tuple[Signal, Settlement | None]]] = {}
        for signal, prediction, odds, settlement in rows:
            odds_values.append(odds)
            grouped.setdefault(prediction.model_name, []).append((signal, settlement))
            if settlement is None:
                continue
            total_stake += settlement.stake
            profit += settlement.profit
            if settlement.outcome == "win":
                wins += 1
            elif settlement.outcome == "loss":
                losses += 1
            else:
                returns += 1
        models = tuple(
            self._model_stats(name, model_rows)
            for name, model_rows in sorted(grouped.items())
        )
        settled = wins + losses + returns
        return DailyStatsView(
            label=label,
            signals=len(rows),
            wins=wins,
            losses=losses,
            returns=returns,
            pending=len(rows) - settled,
            total_stake=total_stake,
            profit=profit,
            roi_percent=(profit / total_stake * 100) if total_stake else None,
            average_odds=(sum(odds_values, Decimal(0)) / len(odds_values))
            if odds_values
            else None,
            models=models,
        )

    async def recent_signals(
        self,
        session: AsyncSession,
        *,
        limit: int = 10,
    ) -> tuple[SignalSummary, ...]:
        rows = (
            await session.execute(
                select(
                    Signal,
                    ModelPrediction,
                    OddsSnapshot.odds,
                    Event.external_id,
                    Event.started_at,
                )
                .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
                .join(OddsSnapshot, OddsSnapshot.id == ModelPrediction.odds_snapshot_id)
                .join(Event, Event.id == ModelPrediction.event_id)
                .where(
                    Signal.decision == "alert",
                    Event.started_at > datetime.now(UTC),
                )
                .order_by(Event.started_at, Signal.id)
                .limit(limit)
            )
        ).all()
        return tuple(
            SignalSummary(
                created_at=self._aware(signal.created_at),
                started_at=self._aware(started_at),
                decision=signal.decision,
                strategy=signal.strategy,
                model=prediction.model_name,
                selection=prediction.selection,
                odds=odds,
                value_percent=prediction.value_percent,
                external_id=external_id,
            )
            for signal, prediction, odds, external_id, started_at in rows
        )

    async def recent_results(
        self,
        session: AsyncSession,
        *,
        limit: int = 10,
    ) -> tuple[ResultSummary, ...]:
        rows = (
            await session.execute(
                select(
                    Settlement,
                    Result,
                    Event.external_id,
                    EventMatch.components,
                    ModelPrediction.mapping_reversed_sides,
                )
                .join(Signal, Signal.id == Settlement.signal_id)
                .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
                .join(Event, Event.id == ModelPrediction.event_id)
                .join(Result, Result.id == Settlement.result_id)
                .outerjoin(EventMatch, EventMatch.id == ModelPrediction.event_match_id)
                .order_by(Settlement.settled_at.desc(), Settlement.id.desc())
                .limit(limit)
            )
        ).all()
        output: list[ResultSummary] = []
        for (
            settlement,
            result,
            external_id,
            match_components,
            frozen_reversed,
        ) in rows:
            score1, score2 = self._orient_score(
                result.score1,
                result.score2,
                match_components,
                frozen_reversed=frozen_reversed,
            )
            output.append(
                ResultSummary(
                    settled_at=self._aware(settlement.settled_at),
                    outcome=settlement.outcome,
                    score1=score1,
                    score2=score2,
                    profit=settlement.profit,
                    external_id=external_id,
                )
            )
        return tuple(output)

    async def parser_health(
        self,
        session: AsyncSession,
    ) -> tuple[ParserHealthView, ...]:
        rows = (
            await session.execute(
                select(ParserRun, Source.code)
                .join(Source, Source.id == ParserRun.source_id)
                .order_by(Source.code, ParserRun.started_at.desc(), ParserRun.id.desc())
            )
        ).all()
        latest: dict[str, ParserHealthView] = {}
        for run, source_code in rows:
            if source_code in latest:
                continue
            latest[source_code] = ParserHealthView(
                source=source_code,
                status=run.status,
                finished_at=self._aware(run.finished_at) if run.finished_at else None,
                latency_ms=run.latency_ms,
                events_parsed=run.events_parsed,
                events_rejected=run.events_rejected,
                last_error=run.last_error,
            )
        return tuple(latest[name] for name in sorted(latest))

    async def recent_errors(
        self,
        session: AsyncSession,
        *,
        limit: int = 10,
    ) -> tuple[ParserHealthView, ...]:
        rows = (
            await session.execute(
                select(ParserRun, Source.code)
                .join(Source, Source.id == ParserRun.source_id)
                .where(
                    or_(ParserRun.status == "error", ParserRun.last_error.is_not(None))
                )
                .order_by(ParserRun.started_at.desc(), ParserRun.id.desc())
                .limit(limit)
            )
        ).all()
        return tuple(
            ParserHealthView(
                source=source_code,
                status=run.status,
                finished_at=self._aware(run.finished_at) if run.finished_at else None,
                latency_ms=run.latency_ms,
                events_parsed=run.events_parsed,
                events_rejected=run.events_rejected,
                last_error=run.last_error,
            )
            for run, source_code in rows
        )

    async def system_status(self, session: AsyncSession) -> SystemStatus:
        predictions = await session.scalar(select(func.count(ModelPrediction.id))) or 0
        pending_alerts = (
            await session.scalar(
                select(func.count(Signal.id)).where(
                    Signal.decision == "alert", Signal.sent_at.is_(None)
                )
            )
            or 0
        )
        sent_alerts = (
            await session.scalar(
                select(func.count(Signal.id)).where(
                    Signal.decision == "alert", Signal.sent_at.is_not(None)
                )
            )
            or 0
        )
        last_alert_created_at = await session.scalar(
            select(func.max(Signal.created_at)).where(Signal.decision == "alert")
        )
        last_alert_sent_at = await session.scalar(
            select(func.max(Signal.sent_at)).where(Signal.sent_at.is_not(None))
        )
        pending_settlements = (
            await session.scalar(
                select(func.count(Settlement.id))
                .join(Signal, Signal.id == Settlement.signal_id)
                .where(
                    Settlement.telegram_notified_at.is_(None),
                    Signal.sent_at.is_not(None),
                )
            )
            or 0
        )
        parsers_with_errors = (
            await session.scalar(
                select(func.count(ParserRun.id)).where(ParserRun.status == "error")
            )
            or 0
        )
        return SystemStatus(
            predictions=int(predictions),
            pending_alerts=int(pending_alerts),
            sent_alerts=int(sent_alerts),
            last_alert_created_at=(
                self._aware(last_alert_created_at) if last_alert_created_at else None
            ),
            last_alert_sent_at=(
                self._aware(last_alert_sent_at) if last_alert_sent_at else None
            ),
            pending_settlements=int(pending_settlements),
            parsers_with_errors=int(parsers_with_errors),
        )

    @staticmethod
    def _model_stats(
        name: str,
        rows: list[tuple[Signal, Settlement | None]],
    ) -> ModelStatsView:
        settlements = [settlement for _, settlement in rows if settlement is not None]
        stake = sum((item.stake for item in settlements), Decimal(0))
        profit = sum((item.profit for item in settlements), Decimal(0))
        return ModelStatsView(
            model=name,
            signals=len(rows),
            settled=len(settlements),
            profit=profit,
            roi_percent=(profit / stake * 100) if stake else None,
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _orient_score(
        score1: int,
        score2: int,
        match_components: dict[str, object] | None,
        *,
        frozen_reversed: bool | None = None,
    ) -> tuple[int, int]:
        reversed_sides = frozen_reversed
        if reversed_sides is None:
            reversed_sides = bool(
                match_components and match_components.get("reversed_sides")
            )
        if reversed_sides:
            return score2, score1
        return score1, score2
