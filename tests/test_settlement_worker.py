from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import (
    Event,
    EventMatch,
    Market,
    ModelPrediction,
    OddsSnapshot,
    RawPayload,
    Result,
    Settlement,
    Signal,
    Source,
)
from app.workers.settlement_worker import SettlementWorker


@pytest.mark.anyio
async def test_worker_settles_matched_alert_at_the_exact_prediction_snapshot(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settlement.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    async with sessions.begin() as session:
        bookmaker = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        history = Source(code="uel_ef", name="UEL", source_type="history")
        session.add_all((bookmaker, history))
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
        bookmaker_event = Event(
            external_id="book-event",
            source_id=bookmaker.id,
            sport="football",
            started_at=now,
            status="finished",
            raw_payload_id=raw.id,
        )
        history_event = Event(
            external_id="history-event",
            source_id=history.id,
            sport="football",
            started_at=now,
            status="finished",
            raw_payload_id=raw.id,
        )
        session.add_all((bookmaker_event, history_event))
        await session.flush()
        result = Result(
            event_id=history_event.id,
            score1=2,
            score2=1,
            winner="P1",
            is_draw=False,
            total=3,
            observed_at=now,
            raw_payload_id=raw.id,
        )
        market = Market(
            event_id=bookmaker_event.id,
            external_id="factor-1",
            code="1x2",
            name="1X2",
        )
        session.add_all((result, market))
        await session.flush()
        odds = OddsSnapshot(
            event_id=bookmaker_event.id,
            bookmaker_source_id=bookmaker.id,
            market_id=market.id,
            selection="P2",
            odds=Decimal("2.15"),
            received_at=now,
            raw_payload_id=raw.id,
        )
        session.add(odds)
        await session.flush()
        event_match = EventMatch(
            source_event_id=history_event.id,
            bookmaker_event_id=bookmaker_event.id,
            confidence=Decimal("0.99"),
            status="matched",
            components={"reversed_sides": True},
            matched_at=now,
        )
        session.add(event_match)
        await session.flush()
        prediction = ModelPrediction(
            event_id=bookmaker_event.id,
            event_match_id=event_match.id,
            mapping_reversed_sides=True,
            odds_snapshot_id=odds.id,
            model_name="baseline",
            model_version="1",
            market_code="1x2",
            selection="P2",
            probability=Decimal("0.55"),
            fair_odds=Decimal("1.81818"),
            value_ratio=Decimal("1.1825"),
            value_percent=Decimal("18.25"),
            features={},
            anomaly_flags=[],
            feature_cutoff_at=now,
            created_at=now,
        )
        session.add(prediction)
        await session.flush()
        signal = Signal(
            prediction_id=prediction.id,
            decision="alert",
            strategy="baseline",
            minimum_odds=Decimal("2.00"),
            safety_multiplier=Decimal("1.10"),
            suggested_stake=Decimal("100"),
            filter_reasons=[],
            created_at=now,
            sent_at=now,
        )
        session.add(signal)

    async with sessions.begin() as session:
        batch = await SettlementWorker().settle_once(session, settled_at=now)
    async with sessions() as session:
        stored = await session.scalar(select(Settlement))

    assert batch.candidates == 1
    assert batch.settled == 1
    assert stored is not None
    assert stored.outcome == "win"
    assert stored.stake == Decimal("100.00")
    assert stored.payout == Decimal("215.00")
    assert stored.profit == Decimal("115.00")
    await engine.dispose()


@pytest.mark.anyio
async def test_worker_settles_alert_even_if_match_was_later_rejected(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settlement-rejected.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    async with sessions.begin() as session:
        bookmaker = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        history = Source(code="uel_ef", name="UEL", source_type="history")
        session.add_all((bookmaker, history))
        await session.flush()
        raw = RawPayload(
            source_id=bookmaker.id,
            endpoint="https://verified.example/listBase",
            http_status=200,
            content_sha256="b" * 64,
            storage_path="raw2.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        bookmaker_event = Event(
            external_id="book-event-2", source_id=bookmaker.id, sport="football",
            started_at=now, status="finished", raw_payload_id=raw.id,
        )
        history_event = Event(
            external_id="history-event-2", source_id=history.id, sport="football",
            started_at=now, status="finished", raw_payload_id=raw.id,
        )
        session.add_all((bookmaker_event, history_event))
        await session.flush()
        market = Market(event_id=bookmaker_event.id, external_id="factor-2", code="1x2", name="1X2")
        result = Result(
            event_id=history_event.id, score1=2, score2=0, winner="P1",
            is_draw=False, total=2, observed_at=now, raw_payload_id=raw.id,
        )
        session.add_all((market, result))
        await session.flush()
        odds = OddsSnapshot(
            event_id=bookmaker_event.id, bookmaker_source_id=bookmaker.id,
            market_id=market.id, selection="X", odds=Decimal("3.50"),
            received_at=now, raw_payload_id=raw.id,
        )
        session.add(odds)
        await session.flush()
        event_match = EventMatch(
            source_event_id=history_event.id, bookmaker_event_id=bookmaker_event.id,
            confidence=Decimal("0.92"), status="rejected",
            components={"reversed_sides": False}, matched_at=now,
        )
        session.add(event_match)
        await session.flush()
        prediction = ModelPrediction(
            event_id=bookmaker_event.id, event_match_id=event_match.id,
            mapping_reversed_sides=False,
            odds_snapshot_id=odds.id,
            model_name="baseline",
            model_version="1",
            market_code="1x2", selection="X", probability=Decimal("0.40"),
            fair_odds=Decimal("2.50"), value_ratio=Decimal("1.40"),
            value_percent=Decimal("40"), display_odds=Decimal("3.50"),
            features={}, anomaly_flags=[], feature_cutoff_at=now, created_at=now,
        )
        session.add(prediction)
        await session.flush()
        session.add(Signal(
            prediction_id=prediction.id, decision="alert", strategy="baseline",
            alert_key=f"baseline:{bookmaker_event.id}:X", minimum_odds=Decimal("2.80"),
            safety_multiplier=Decimal("1.10"), suggested_stake=Decimal("1"),
            filter_reasons=[], created_at=now, sent_at=now,
        ))

    async with sessions.begin() as session:
        batch = await SettlementWorker().settle_once(session, settled_at=now)
    async with sessions() as session:
        stored = await session.scalar(select(Settlement))
    assert batch.candidates == 1
    assert batch.settled == 1
    assert stored is not None
    assert stored.outcome == "loss"
    assert stored.profit == Decimal("-1.00")
    await engine.dispose()


@pytest.mark.anyio
async def test_worker_does_not_settle_alert_that_was_never_delivered(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settlement-unsent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    async with sessions.begin() as session:
        bookmaker = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        history = Source(code="uel_ef", name="UEL", source_type="history")
        session.add_all((bookmaker, history))
        await session.flush()
        raw = RawPayload(
            source_id=bookmaker.id, endpoint="https://verified.example/listBase",
            http_status=200, content_sha256="d" * 64, storage_path="raw-unsent.json",
            response_headers={}, received_at=now,
        )
        session.add(raw)
        await session.flush()
        bookmaker_event = Event(
            external_id="book-unsent", source_id=bookmaker.id, sport="football",
            started_at=now, status="finished", raw_payload_id=raw.id,
        )
        history_event = Event(
            external_id="history-unsent", source_id=history.id, sport="football",
            started_at=now, status="finished", raw_payload_id=raw.id,
        )
        session.add_all((bookmaker_event, history_event))
        await session.flush()
        market = Market(
            event_id=bookmaker_event.id, external_id="factor-unsent", code="1x2", name="1X2"
        )
        result = Result(
            event_id=history_event.id, score1=1, score2=0, winner="P1",
            is_draw=False, total=1, observed_at=now, raw_payload_id=raw.id,
        )
        session.add_all((market, result))
        await session.flush()
        odds = OddsSnapshot(
            event_id=bookmaker_event.id, bookmaker_source_id=bookmaker.id,
            market_id=market.id, selection="P1", odds=Decimal("2.00"),
            received_at=now, raw_payload_id=raw.id,
        )
        session.add(odds)
        await session.flush()
        event_match = EventMatch(
            source_event_id=history_event.id, bookmaker_event_id=bookmaker_event.id,
            confidence=Decimal("0.99"), status="matched",
            components={"reversed_sides": False}, matched_at=now,
        )
        session.add(event_match)
        await session.flush()
        prediction = ModelPrediction(
            event_id=bookmaker_event.id, event_match_id=event_match.id,
            mapping_reversed_sides=False, odds_snapshot_id=odds.id,
            model_name="baseline", model_version="1", market_code="1x2",
            selection="P1", probability=Decimal("0.6"), fair_odds=Decimal("1.66667"),
            value_ratio=Decimal("1.2"), value_percent=Decimal("20"), display_odds=Decimal("2"),
            features={}, anomaly_flags=[], feature_cutoff_at=now, created_at=now,
        )
        session.add(prediction)
        await session.flush()
        session.add(Signal(
            prediction_id=prediction.id, decision="alert", strategy="baseline",
            alert_key=f"baseline:{bookmaker_event.id}:P1", minimum_odds=Decimal("1.80"),
            safety_multiplier=Decimal("1.08"), suggested_stake=Decimal("1"),
            filter_reasons=[], created_at=now, sent_at=None,
        ))

    async with sessions.begin() as session:
        batch = await SettlementWorker().settle_once(session, settled_at=now)
    async with sessions() as session:
        stored = await session.scalar(select(Settlement))
    assert batch.candidates == 0
    assert batch.settled == 0
    assert stored is None
    await engine.dispose()
