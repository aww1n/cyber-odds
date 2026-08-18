from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

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
from app.models.baseline import OutcomeProbabilities
from app.workers.prediction_worker import (
    PredictionSignalWorker,
    probabilities_by_bookmaker_selection,
)


def test_probabilities_are_oriented_to_bookmaker_participants() -> None:
    source_probabilities = OutcomeProbabilities(p1=0.55, draw=0.25, p2=0.20)

    direct = probabilities_by_bookmaker_selection(
        source_probabilities,
        reversed_sides=False,
    )
    reversed_mapping = probabilities_by_bookmaker_selection(
        source_probabilities,
        reversed_sides=True,
    )

    assert direct == {"P1": 0.55, "X": 0.25, "P2": 0.20}
    assert reversed_mapping == {"P1": 0.20, "X": 0.25, "P2": 0.55}


@pytest.mark.anyio
async def test_worker_persists_prediction_even_when_alerts_are_disabled(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prediction.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
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
            content_sha256="a" * 64,
            storage_path="raw.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        source_event = Event(
            external_id="source-event",
            source_id=history.id,
            sport="football",
            started_at=now + timedelta(minutes=20),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
        )
        bookmaker_event = Event(
            external_id="book-event",
            source_id=bookmaker.id,
            sport="football",
            started_at=now + timedelta(minutes=10),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
            raw_payload_id=raw.id,
        )
        session.add_all((source_event, bookmaker_event))
        await session.flush()
        market = Market(
            event_id=bookmaker_event.id,
            external_id="factor:921",
            code="1x2",
            name="Full time result",
        )
        session.add(market)
        await session.flush()
        odds = OddsSnapshot(
            event_id=bookmaker_event.id,
            bookmaker_source_id=bookmaker.id,
            market_id=market.id,
            selection="P1",
            odds=Decimal("4.00"),
            received_at=now - timedelta(minutes=1),
            raw_payload_id=raw.id,
        )
        session.add(odds)
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
        alerts_enabled=False,
        source="uel_ef",
        model_name="individual_h2h_fixed",
        model_version="test-v1",
        safety_multiplier=1.0,
        min_match_confidence=0.9,
        suspicious_edge_percent=100,
        stale_after_seconds=120,
        allowed_markets=("1x2",),
    )
    worker = PredictionSignalWorker()
    async with sessions.begin() as session:
        first = await worker.generate_once(
            session,
            strategy_name="test",
            strategy=strategy,
            now=now,
        )
    async with sessions.begin() as session:
        second = await worker.generate_once(
            session,
            strategy_name="test",
            strategy=strategy,
            now=now,
        )
    async with sessions() as session:
        prediction = await session.scalar(select(ModelPrediction))
        signal = await session.scalar(select(Signal))

    assert first.predictions_created == 1
    assert first.alerts_created == 0
    assert first.skips_created == 1
    assert second.predictions_created == 0
    assert prediction is not None
    assert prediction.event_match_id is not None
    assert prediction.mapping_reversed_sides is False
    assert prediction.odds_snapshot_id == odds.id
    assert float(prediction.probability) == pytest.approx(1 / 3)
    assert prediction.feature_cutoff_at.replace(tzinfo=UTC) == now - timedelta(minutes=1)
    assert signal is not None
    assert signal.decision == "skip"
    assert signal.expires_at is not None
    assert signal.filter_reasons == ["alerts_disabled"]
    await engine.dispose()


@pytest.mark.anyio
async def test_worker_keeps_predictions_but_deduplicates_live_alerts(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prediction-dedupe.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
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
            content_sha256="c" * 64,
            storage_path="raw-dedupe.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        source_event = Event(
            external_id="source-dedupe", source_id=history.id, sport="football",
            started_at=now + timedelta(minutes=20), player1_id=player1.id,
            player2_id=player2.id, status="scheduled",
        )
        bookmaker_event = Event(
            external_id="book-dedupe", source_id=bookmaker.id, sport="football",
            started_at=now + timedelta(minutes=10), player1_id=player1.id,
            player2_id=player2.id, status="scheduled", raw_payload_id=raw.id,
        )
        session.add_all((source_event, bookmaker_event))
        await session.flush()
        market = Market(
            event_id=bookmaker_event.id, external_id="factor:dedupe",
            code="1x2", name="Full time result",
        )
        session.add(market)
        await session.flush()
        first_odds = OddsSnapshot(
            event_id=bookmaker_event.id, bookmaker_source_id=bookmaker.id,
            market_id=market.id, selection="P1", odds=Decimal("4.00"),
            received_at=now - timedelta(minutes=1), raw_payload_id=raw.id,
        )
        session.add(first_odds)
        session.add(EventMatch(
            source_event_id=source_event.id, bookmaker_event_id=bookmaker_event.id,
            confidence=Decimal("0.99"), status="matched",
            components={"reversed_sides": False}, matched_at=now,
        ))

    strategy = StrategySettings(
        prediction_enabled=True,
        alerts_enabled=True,
        source="uel_ef",
        model_name="individual_h2h_fixed",
        model_version="test-dedupe",
        safety_multiplier=1.0,
        min_samples=0,
        min_match_confidence=0.9,
        suspicious_edge_percent=100,
        stale_after_seconds=120,
        allowed_markets=("1x2",),
    )
    worker = PredictionSignalWorker()
    async with sessions.begin() as session:
        first = await worker.generate_once(
            session, strategy_name="test", strategy=strategy, now=now
        )
    async with sessions.begin() as session:
        market_id = await session.scalar(
            select(Market.id).where(Market.external_id == "factor:dedupe")
        )
        bookmaker_id = await session.scalar(select(Source.id).where(Source.code == "fonbet"))
        event_id = await session.scalar(select(Event.id).where(Event.external_id == "book-dedupe"))
        raw_id = await session.scalar(
            select(RawPayload.id).where(RawPayload.storage_path == "raw-dedupe.json")
        )
        assert market_id and bookmaker_id and event_id and raw_id
        session.add(OddsSnapshot(
            event_id=event_id, bookmaker_source_id=bookmaker_id,
            market_id=market_id, selection="P1", odds=Decimal("4.10"),
            received_at=now - timedelta(seconds=20), raw_payload_id=raw_id,
        ))
    async with sessions.begin() as session:
        second = await worker.generate_once(
            session, strategy_name="test", strategy=strategy, now=now
        )
    async with sessions() as session:
        predictions = (
            await session.scalars(select(ModelPrediction).order_by(ModelPrediction.id))
        ).all()
        signals = (await session.scalars(select(Signal).order_by(Signal.id))).all()

    assert first.predictions_created == 1
    assert first.alerts_created == 1
    assert second.predictions_created == 1
    assert second.alerts_created == 0
    assert second.skips_created == 1
    assert len(predictions) == 2
    assert len(signals) == 2
    assert [signal.decision for signal in signals] == ["alert", "skip"]
    assert signals[0].alert_key is not None
    assert signals[1].alert_key is None
    assert signals[1].filter_reasons == ["duplicate_alert"]
    await engine.dispose()

@pytest.mark.anyio
async def test_worker_emits_only_highest_value_1x2_alert_per_event(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prediction-best.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
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
            content_sha256="d" * 64,
            storage_path="raw-best.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        source_event = Event(
            external_id="source-best",
            source_id=history.id,
            sport="football",
            started_at=now + timedelta(minutes=20),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
        )
        bookmaker_event = Event(
            external_id="book-best",
            source_id=bookmaker.id,
            sport="football",
            started_at=now + timedelta(minutes=10),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
            raw_payload_id=raw.id,
        )
        session.add_all((source_event, bookmaker_event))
        await session.flush()
        market = Market(
            event_id=bookmaker_event.id,
            external_id="factor:best",
            code="1x2",
            name="Full time result",
        )
        session.add(market)
        await session.flush()
        session.add_all(
            (
                OddsSnapshot(
                    event_id=bookmaker_event.id,
                    bookmaker_source_id=bookmaker.id,
                    market_id=market.id,
                    selection="P1",
                    odds=Decimal("4.00"),
                    received_at=now - timedelta(seconds=20),
                    raw_payload_id=raw.id,
                ),
                OddsSnapshot(
                    event_id=bookmaker_event.id,
                    bookmaker_source_id=bookmaker.id,
                    market_id=market.id,
                    selection="X",
                    odds=Decimal("5.00"),
                    received_at=now - timedelta(seconds=20),
                    raw_payload_id=raw.id,
                ),
            )
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
        model_name="individual_h2h_fixed",
        model_version="test-best",
        safety_multiplier=1.0,
        min_samples=0,
        min_match_confidence=0.9,
        suspicious_edge_percent=100,
        stale_after_seconds=120,
        allowed_markets=("1x2",),
    )
    worker = PredictionSignalWorker()
    async with sessions.begin() as session:
        batch = await worker.generate_once(
            session,
            strategy_name="test",
            strategy=strategy,
            now=now,
        )
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Signal, ModelPrediction)
                .join(ModelPrediction, ModelPrediction.id == Signal.prediction_id)
                .order_by(Signal.id)
            )
        ).all()

    alerts = [(signal, prediction) for signal, prediction in rows if signal.decision == "alert"]
    assert batch.predictions_created == 2
    assert batch.alerts_created == 1
    assert batch.skips_created == 1
    assert len(alerts) == 1
    assert alerts[0][1].selection == "X"
    skipped = [(signal, prediction) for signal, prediction in rows if signal.decision == "skip"]
    assert skipped[0][1].selection == "P1"
    assert "better_selection_available" in skipped[0][0].filter_reasons
    await engine.dispose()

@pytest.mark.anyio
async def test_worker_skips_alert_when_quote_will_expire_before_delivery_window(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prediction-expiry-window.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
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
            content_sha256="e" * 64,
            storage_path="raw-expiry-window.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        source_event = Event(
            external_id="source-expiry-window",
            source_id=history.id,
            sport="football",
            started_at=now + timedelta(minutes=20),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
        )
        bookmaker_event = Event(
            external_id="book-expiry-window",
            source_id=bookmaker.id,
            sport="football",
            started_at=now + timedelta(minutes=10),
            player1_id=player1.id,
            player2_id=player2.id,
            status="scheduled",
            raw_payload_id=raw.id,
        )
        session.add_all((source_event, bookmaker_event))
        await session.flush()
        market = Market(
            event_id=bookmaker_event.id,
            external_id="factor:expiry-window",
            code="1x2",
            name="Full time result",
        )
        session.add(market)
        await session.flush()
        session.add(
            OddsSnapshot(
                event_id=bookmaker_event.id,
                bookmaker_source_id=bookmaker.id,
                market_id=market.id,
                selection="P1",
                odds=Decimal("4.00"),
                # Still within stale_after=120, but only ten seconds remain.
                received_at=now - timedelta(seconds=110),
                raw_payload_id=raw.id,
            )
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
        model_name="individual_h2h_fixed",
        model_version="test-expiry-window",
        safety_multiplier=1.0,
        min_samples=0,
        min_match_confidence=0.9,
        suspicious_edge_percent=100,
        stale_after_seconds=120,
        min_alert_lead_seconds=15,
        allowed_markets=("1x2",),
    )
    worker = PredictionSignalWorker()
    async with sessions.begin() as session:
        batch = await worker.generate_once(
            session,
            strategy_name="test",
            strategy=strategy,
            now=now,
        )
    async with sessions() as session:
        signal = await session.scalar(select(Signal))

    assert batch.predictions_created == 1
    assert batch.alerts_created == 0
    assert batch.skips_created == 1
    assert signal is not None
    assert signal.decision == "skip"
    assert signal.filter_reasons == ["insufficient_delivery_window"]
    await engine.dispose()
