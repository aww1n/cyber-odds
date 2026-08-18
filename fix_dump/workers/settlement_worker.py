from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.settlement import settle_market
from app.database.models import (
    EventMatch,
    ModelPrediction,
    OddsSnapshot,
    Result,
    Settlement,
    Signal,
)


@dataclass(frozen=True, slots=True)
class SettlementBatch:
    candidates: int
    settled: int
    skipped_without_stake: int
    skipped_unsupported_market: int


class SettlementWorker:
    async def settle_once(
        self,
        session: AsyncSession,
        *,
        settled_at: datetime | None = None,
    ) -> SettlementBatch:
        rows = (
            await session.execute(
                select(Signal, ModelPrediction, OddsSnapshot, Result, EventMatch)
                .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
                .join(OddsSnapshot, OddsSnapshot.id == ModelPrediction.odds_snapshot_id)
                .join(
                    EventMatch,
                    EventMatch.id == ModelPrediction.event_match_id,
                )
                .join(Result, Result.event_id == EventMatch.source_event_id)
                .outerjoin(Settlement, Settlement.signal_id == Signal.id)
                .where(
                    Signal.decision == "alert",
                    # Analytics represents calls that were actually delivered.
                    # An alert decision that never reached Telegram is not a bet.
                    Signal.sent_at.is_not(None),
                    Settlement.id.is_(None),
                )
                .order_by(Signal.id)
            )
        ).all()
        settled = 0
        skipped_without_stake = 0
        skipped_unsupported_market = 0
        timestamp = settled_at or datetime.now(UTC)
        for signal, prediction, odds, result, event_match in rows:
            stake = signal.suggested_stake
            if stake is None or stake <= 0:
                skipped_without_stake += 1
                continue
            try:
                score1, score2 = result.score1, result.score2
                reversed_sides = prediction.mapping_reversed_sides
                if reversed_sides is None:
                    # Legacy fallback for predictions created before the mapping
                    # orientation was frozen on ModelPrediction.
                    reversed_sides = bool(event_match.components.get("reversed_sides"))
                if reversed_sides:
                    score1, score2 = score2, score1
                outcome = settle_market(
                    prediction.selection,
                    score1=score1,
                    score2=score2,
                )
            except ValueError:
                skipped_unsupported_market += 1
                continue
            if outcome == "win":
                payout = stake * odds.odds
            elif outcome == "return":
                payout = stake
            else:
                payout = Decimal(0)
            settlement = Settlement(
                signal_id=signal.id,
                result_id=result.id,
                outcome=outcome,
                stake=stake,
                payout=payout,
                profit=payout - stake,
                settled_at=timestamp,
            )
            # The scheduler and an operator may run settlement at the same time.
            # The unique signal_id index is the source of truth; isolate a race
            # in a SAVEPOINT so one duplicate cannot roll back the whole batch.
            try:
                async with session.begin_nested():
                    session.add(settlement)
                    await session.flush()
            except IntegrityError as exc:
                if not self._is_duplicate_settlement(exc):
                    raise
                continue
            settled += 1
        return SettlementBatch(
            candidates=len(rows),
            settled=settled,
            skipped_without_stake=skipped_without_stake,
            skipped_unsupported_market=skipped_unsupported_market,
        )

    @staticmethod
    def _is_duplicate_settlement(exc: IntegrityError) -> bool:
        message = str(exc).casefold()
        return (
            "ix_settlements_signal_id" in message
            or "settlements.signal_id" in message
        )
