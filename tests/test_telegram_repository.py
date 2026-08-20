from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import (
    Event,
    EventMatch,
    EventParticipant,
    Market,
    ModelPrediction,
    OddsSnapshot,
    RawPayload,
    Result,
    Settlement,
    Signal,
    Source,
    Tournament,
)
from app.telegram.repository import TelegramRepository


@pytest.mark.anyio
async def test_data_totals_are_zero_for_empty_database(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    async with sessions() as session:
        totals = await TelegramRepository().data_totals(session)
    await engine.dispose()

    assert totals.events == 0
    assert totals.results == 0
    assert totals.odds_snapshots == 0
    assert totals.players == 0
    assert totals.tournaments == 0
    assert totals.predictions == 0


@pytest.mark.anyio
async def test_repository_delivers_each_alert_and_settlement_from_persisted_rows(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'telegram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    async with sessions.begin() as session:
        source = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        history = Source(code="uel_ef", name="UEL", source_type="history")
        session.add_all((source, history))
        await session.flush()
        raw = RawPayload(
            source_id=source.id,
            endpoint="https://verified.example/events/listBase",
            http_status=200,
            content_type="application/json",
            content_sha256="a" * 64,
            storage_path="data/raw/test.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()
        tournament = Tournament(
            source_id=source.id,
            external_id="t-1",
            name="FC 26. H2H Liga-1",
            normalized_name="fc 26 h2h liga 1",
            sport="football",
        )
        session.add(tournament)
        await session.flush()
        event = Event(
            external_id="event-1",
            source_id=source.id,
            sport="football",
            tournament_id=tournament.id,
            started_at=now + timedelta(minutes=30),
            format="2x4",
            status="scheduled",
            raw_payload_id=raw.id,
        )
        session.add(event)
        await session.flush()
        history_event = Event(
            external_id="history-event-1",
            source_id=history.id,
            sport="football",
            started_at=event.started_at,
            status="finished",
            raw_payload_id=raw.id,
        )
        session.add(history_event)
        await session.flush()
        event_match = EventMatch(
            source_event_id=history_event.id,
            bookmaker_event_id=event.id,
            confidence=Decimal("0.99"),
            status="matched",
            components={"reversed_sides": True},
            matched_at=now,
        )
        session.add(event_match)
        await session.flush()
        session.add_all(
            (
                EventParticipant(event_id=event.id, side=1, raw_name="Italy (A)"),
                EventParticipant(event_id=event.id, side=2, raw_name="Spain (B)"),
            )
        )
        market = Market(
            event_id=event.id,
            external_id="factor-927",
            code="handicap",
            name="handicap",
        )
        session.add(market)
        await session.flush()
        odds = OddsSnapshot(
            event_id=event.id,
            bookmaker_source_id=source.id,
            market_id=market.id,
            selection="F2",
            line=Decimal("0"),
            odds=Decimal("2.15"),
            received_at=now,
            raw_payload_id=raw.id,
        )
        session.add(odds)
        await session.flush()
        prediction = ModelPrediction(
            event_id=event.id,
            event_match_id=event_match.id,
            mapping_reversed_sides=True,
            odds_snapshot_id=odds.id,
            model_name="baseline",
            model_version="1",
            market_code="handicap",
            selection="F2",
            probability=Decimal("0.5744"),
            fair_odds=Decimal("1.7400"),
            value_ratio=Decimal("1.2350"),
            value_percent=Decimal("23.50"),
            display_odds=Decimal("2.18"),
            sample_size=50,
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
            strategy="test",
            minimum_odds=Decimal("1.95"),
            safety_multiplier=Decimal("1.12"),
            stake_mode="fixed",
            suggested_stake=Decimal("100"),
            bet_multiplier=Decimal("1"),
            filter_reasons=[],
            expires_at=now + timedelta(minutes=2),
            created_at=now,
        )
        session.add(signal)
        result = Result(
            event_id=history_event.id,
            score1=2,
            score2=1,
            winner="P1",
            is_draw=False,
            total=3,
            settled_at=now + timedelta(hours=1),
        )
        session.add(result)
        await session.flush()
        session.add(
            Settlement(
                signal_id=signal.id,
                result_id=result.id,
                outcome="win",
                stake=Decimal("100"),
                payout=Decimal("215"),
                profit=Decimal("115"),
                settled_at=now + timedelta(hours=1),
            )
        )

    repository = TelegramRepository()
    async with sessions() as session:
        alerts = await repository.pending_alerts(session, now=now)
        too_early = await repository.pending_alerts(
            session,
            now=now,
            alert_window_minutes=10,
        )
        settlements_before_alert = await repository.pending_settlements(session)
    assert len(alerts) == 1
    assert too_early == ()
    assert alerts[0].view.calculation_odds == Decimal("2.15000")
    assert alerts[0].view.display_odds == Decimal("2.18000")
    assert alerts[0].view.signal_id == alerts[0].signal_id
    assert settlements_before_alert == ()

    async with sessions.begin() as session:
        await repository.mark_alert_sent(
            session,
            signal_id=alerts[0].signal_id,
            message_id=42,
            sent_at=now,
        )
    async with sessions() as session:
        assert await repository.pending_alerts(session, now=now) == ()
        settlements = await repository.pending_settlements(session)
        stats = await repository.stats(session, since=None, label="All")
    assert len(settlements) == 1
    assert settlements[0].reply_to_message_id == 42
    assert settlements[0].view.outcome == "win"
    assert (settlements[0].view.score1, settlements[0].view.score2) == (1, 2)
    assert stats.signals == 1
    assert stats.profit == Decimal("115.00")
    assert stats.roi_percent == Decimal("115")

    async with sessions.begin() as session:
        await repository.mark_settlement_sent(
            session,
            settlement_id=settlements[0].settlement_id,
            sent_at=now + timedelta(hours=1),
        )
    async with sessions() as session:
        assert await repository.pending_settlements(session) == ()
    await engine.dispose()


@pytest.mark.anyio
async def test_repository_expires_unsent_alerts_before_event_or_after_quote_expiry(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'telegram-expiry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    async with sessions.begin() as session:
        source = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        session.add(source)
        await session.flush()
        raw = RawPayload(
            source_id=source.id, endpoint="https://verified.example/listBase",
            http_status=200, content_sha256="e" * 64, storage_path="raw-expiry.json",
            response_headers={}, received_at=now,
        )
        session.add(raw)
        await session.flush()
        event = Event(
            external_id="event-expiry", source_id=source.id, sport="football",
            started_at=now + timedelta(minutes=10), status="scheduled", raw_payload_id=raw.id,
        )
        session.add(event)
        await session.flush()
        market = Market(event_id=event.id, external_id="factor-expiry", code="1x2", name="1X2")
        session.add(market)
        await session.flush()
        odds = OddsSnapshot(
            event_id=event.id, bookmaker_source_id=source.id, market_id=market.id,
            selection="P1", odds=Decimal("2.00"), received_at=now - timedelta(minutes=3),
            raw_payload_id=raw.id,
        )
        session.add(odds)
        await session.flush()
        prediction = ModelPrediction(
            event_id=event.id, event_match_id=None, mapping_reversed_sides=None,
            odds_snapshot_id=odds.id, model_name="baseline", model_version="1",
            market_code="1x2", selection="P1", probability=Decimal("0.6"),
            fair_odds=Decimal("1.66667"), value_ratio=Decimal("1.2"),
            value_percent=Decimal("20"), display_odds=Decimal("2"), sample_size=12,
            features={}, anomaly_flags=[], feature_cutoff_at=odds.received_at, created_at=now,
        )
        session.add(prediction)
        await session.flush()
        signal = Signal(
            prediction_id=prediction.id, decision="alert", strategy="baseline",
            alert_key=f"baseline:{event.id}:P1", minimum_odds=Decimal("1.80"),
            safety_multiplier=Decimal("1.08"), suggested_stake=Decimal("1"),
            filter_reasons=[], expires_at=now - timedelta(seconds=1), created_at=now,
        )
        session.add(signal)

    repository = TelegramRepository()
    async with sessions.begin() as session:
        expired = await repository.expire_pending_alerts(session, now=now)
    async with sessions() as session:
        stored = await session.get(Signal, signal.id)
        pending = await repository.pending_alerts(session, now=now)
    assert expired == 1
    assert stored is not None
    assert stored.decision == "skip"
    assert stored.alert_key is None
    assert "delivery_expired" in stored.filter_reasons
    assert pending == ()
    await engine.dispose()


@pytest.mark.anyio
async def test_pending_alert_window_rejects_started_and_sorts_nearest_first(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'telegram-window.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)

    async with sessions.begin() as session:
        source = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        session.add(source)
        await session.flush()
        raw = RawPayload(
            source_id=source.id,
            endpoint="https://verified.example/listBase",
            http_status=200,
            content_sha256="f" * 64,
            storage_path="telegram-window.json",
            response_headers={},
            received_at=now,
        )
        session.add(raw)
        await session.flush()

        for label, minutes in (("later", 8), ("early", 11), ("started", -1), ("near", 5)):
            event = Event(
                external_id=f"event-{label}",
                source_id=source.id,
                sport="football",
                started_at=now + timedelta(minutes=minutes),
                status="scheduled" if minutes > 0 else "live",
                raw_payload_id=raw.id,
            )
            session.add(event)
            await session.flush()
            market = Market(
                event_id=event.id,
                external_id=f"market-{label}",
                code="1x2",
                name="1X2",
            )
            session.add(market)
            await session.flush()
            odds = OddsSnapshot(
                event_id=event.id,
                bookmaker_source_id=source.id,
                market_id=market.id,
                selection="P1",
                odds=Decimal("2"),
                received_at=now - timedelta(seconds=30),
                raw_payload_id=raw.id,
            )
            session.add(odds)
            await session.flush()
            prediction = ModelPrediction(
                event_id=event.id,
                event_match_id=None,
                mapping_reversed_sides=None,
                odds_snapshot_id=odds.id,
                model_name="baseline",
                model_version="window-v1",
                market_code="1x2",
                selection="P1",
                probability=Decimal("0.6"),
                fair_odds=Decimal("1.66667"),
                value_ratio=Decimal("1.2"),
                value_percent=Decimal("20"),
                display_odds=Decimal("2"),
                sample_size=20,
                features={},
                anomaly_flags=[],
                feature_cutoff_at=odds.received_at,
                created_at=now,
            )
            session.add(prediction)
            await session.flush()
            session.add(
                Signal(
                    prediction_id=prediction.id,
                    decision="alert",
                    strategy="window",
                    alert_key=f"window:{event.external_id}:1x2:P1:-",
                    minimum_odds=Decimal("1.8"),
                    safety_multiplier=Decimal("1.08"),
                    filter_reasons=[],
                    expires_at=event.started_at,
                    created_at=now,
                )
            )

    repository = TelegramRepository()
    async with sessions.begin() as session:
        expired = await repository.expire_pending_alerts(session, now=now)
        pending = await repository.pending_alerts(
            session,
            now=now,
            alert_window_minutes=10,
        )

    assert expired == 1
    assert [item.view.external_id for item in pending] == ["event-near", "event-later"]
    await engine.dispose()
