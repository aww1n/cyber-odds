from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
            # The alert freezes its actual bankroll-derived amount.  Legacy
            # signals fall back to the former strategy-unit stake.
            stake = signal.stake_amount or signal.suggested_stake
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
                outcome: str = settle_market(
                    prediction.selection,
                    score1=score1,
                    score2=score2,
                    market=prediction.market_code,
                    line=odds.line,
                )
            except ValueError:
                # A delivered historical call must not remain pending forever
                # after a parser/market contract changes.  Void returns stake.
                skipped_unsupported_market += 1
                outcome = "void"
            settlement_odds = prediction.display_odds or odds.odds
            if outcome == "win":
                payout = stake * settlement_odds
            elif outcome in {"return", "void"}:
                payout = stake
            else:
                payout = Decimal(0)
            inserted = await self._insert_settlement_atomic(
                session,
                values={
                    "signal_id": signal.id,
                    "result_id": result.id,
                    "outcome": outcome,
                    "stake": stake,
                    "payout": payout,
                    "profit": payout - stake,
                    "settled_at": timestamp,
                },
            )
            if not inserted:
                continue
            settled += 1
        return SettlementBatch(
            candidates=len(rows),
            settled=settled,
            skipped_without_stake=skipped_without_stake,
            skipped_unsupported_market=skipped_unsupported_market,
        )

    @staticmethod
    async def _insert_settlement_atomic(
        session: AsyncSession,
        *,
        values: dict[str, object],
    ) -> bool:
        if session.bind is None:
            raise RuntimeError("session is not bound to a database engine")
        statement: Any
        if session.bind.dialect.name == "postgresql":
            statement = postgresql_insert(Settlement).values(**values)
        elif session.bind.dialect.name == "sqlite":
            statement = sqlite_insert(Settlement).values(**values)
        else:  # pragma: no cover - supported deployments use PostgreSQL/SQLite
            raise RuntimeError(
                f"unsupported database dialect: {session.bind.dialect.name}"
            )
        statement = statement.on_conflict_do_nothing(
            index_elements=[Settlement.signal_id]
        ).returning(Settlement.id)
        return (await session.scalar(statement)) is not None
