from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.backtest import BacktestConfig, BacktestEngine, BacktestOpportunity
from app.config.strategies import StrategySettings
from app.database import Base, build_async_engine, build_session_factory
from app.database.models import (
    Event,
    EventMatch,
    Market,
    ModelPrediction,
    OddsSnapshot,
    Player,
    RawPayload,
    Signal,
    Source,
)
from app.models.statistical import poisson_total_probabilities
from app.workers.prediction_worker import PredictionSignalWorker


def test_integer_total_probability_prices_push_conditionally() -> None:
    probabilities = poisson_total_probabilities(
        goals1_rate=2.0,
        goals2_rate=2.0,
        line=4.0,
        selection="over",
    )

    assert probabilities.push > 0
    assert probabilities.win + probabilities.push + probabilities.loss == pytest.approx(1)
    assert probabilities.conditional_win == pytest.approx(
        probabilities.win / (probabilities.win + probabilities.loss)
    )


def test_backtest_total_integer_line_is_return_not_loss() -> None:
    start = datetime(2026, 8, 17, 12, tzinfo=UTC)
    opportunity = BacktestOpportunity(
        event_id=1,
        event_started_at=start + timedelta(minutes=10),
        decision_at=start + timedelta(minutes=9),
        feature_cutoff_at=start + timedelta(minutes=8),
        odds_received_at=start + timedelta(minutes=8),
        odds_snapshot_id=1,
        probability=0.6,
        odds=2.0,
        market="total",
        selection="over",
        tournament="UEL",
        sample_size=50,
        h2h_samples=10,
        match_confidence=0.99,
        score1=2,
        score2=2,
        line=4.0,
    )

    run = BacktestEngine().run(
        [opportunity],
        BacktestConfig(base_stake=10, initial_bankroll=100),
    )

    assert run.bets[0].settlement is not None
    assert run.bets[0].settlement.outcome == "return"
    assert run.final_bankroll == Decimal("100")


@pytest.mark.anyio
async def test_live_worker_creates_independent_predictions_for_real_total_lines(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'totals.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    async with sessions.begin() as session:
        history = Source(code="uel_ef", name="UEL", source_type="history")
        bookmaker = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        player1 = Player(display_name="A", normalized_name="a")
        player2 = Player(display_name="B", normalized_name="b")
        session.add_all((history, bookmaker, player1, player2))
        await session.flush()
        raw = RawPayload(
            source_id=bookmaker.id,
            endpoint="https://verified.example/listBase",
            http_status=200,
            content_sha256="f" * 64,
            storage_path="totals.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        source_event = Event(
            external_id="source-total",
            source_id=history.id,
            sport="football",
            started_at=now + timedelta(minutes=8),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
        )
        bookmaker_event = Event(
            external_id="book-total",
            source_id=bookmaker.id,
            sport="football",
            started_at=now + timedelta(minutes=8),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
            raw_payload_id=raw.id,
        )
        session.add_all((source_event, bookmaker_event))
        await session.flush()
        market = Market(
            event_id=bookmaker_event.id,
            external_id="factor:931",
            code="total",
            name="Full time total",
        )
        session.add(market)
        await session.flush()
        session.add_all(
            OddsSnapshot(
                event_id=bookmaker_event.id,
                bookmaker_source_id=bookmaker.id,
                market_id=market.id,
                selection="under",
                line=line,
                odds=Decimal("2.00"),
                received_at=now - timedelta(seconds=20),
                raw_payload_id=raw.id,
            )
            for line in (Decimal("2.5"), Decimal("3.5"))
        )
        session.add(
            EventMatch(
                source_event_id=source_event.id,
                bookmaker_event_id=bookmaker_event.id,
                confidence=Decimal("0.99"),
                status="matched",
                components={"reversed_sides": False},
                matched_at=now,
            )
        )

    strategy = StrategySettings(
        prediction_enabled=True,
        alerts_enabled=True,
        source="uel_ef",
        model_name="poisson_goals",
        model_version="totals-test",
        safety_multiplier=1.0,
        min_samples=0,
        max_probability=1.0,
        min_match_confidence=0.9,
        stale_after_seconds=120,
        suspicious_edge_percent=200,
        allowed_markets=("total",),
    )
    async with sessions.begin() as session:
        batch = await PredictionSignalWorker().generate_once(
            session,
            strategy_name="totals",
            strategy=strategy,
            now=now,
        )
    async with sessions() as session:
        predictions = (
            await session.scalars(select(ModelPrediction).order_by(ModelPrediction.id))
        ).all()
        signals = (await session.scalars(select(Signal).order_by(Signal.id))).all()
        snapshots = {
            item.id: item
            for item in (await session.scalars(select(OddsSnapshot))).all()
        }

    assert batch.predictions_created == 2
    assert {snapshots[item.odds_snapshot_id].line for item in predictions} == {
        Decimal("2.500"),
        Decimal("3.500"),
    }
    assert {item.selection for item in predictions} == {"under"}
    assert sum(item.decision == "alert" for item in signals) == 1
    assert sum(item.decision == "skip" for item in signals) == 1
    assert "better_selection_available" in next(
        item.filter_reasons for item in signals if item.decision == "skip"
    )
    await engine.dispose()
