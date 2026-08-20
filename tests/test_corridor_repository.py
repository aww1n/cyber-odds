from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.corridors.repository import CorridorRepository
from app.database import Base, build_async_engine, build_session_factory
from app.database.models import (
    CorridorObservation,
    Event,
    EventMatch,
    Market,
    OddsCorridor,
    OddsSnapshot,
    RawPayload,
    Result,
    Source,
)


@pytest.mark.anyio
async def test_corridors_use_latest_fresh_prematch_quote_and_separate_total_lines(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'corridors.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    start = datetime(2026, 8, 17, 12, tzinfo=UTC)
    as_of = start + timedelta(hours=2)
    async with sessions.begin() as session:
        history = Source(code="uel_ef", name="UEL", source_type="history")
        bookmaker = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        session.add_all((history, bookmaker))
        await session.flush()
        raw = RawPayload(
            source_id=bookmaker.id,
            endpoint="https://verified.example/listBase",
            http_status=200,
            content_sha256="a" * 64,
            storage_path="corridors.json",
            response_headers={},
            received_at=start,
        )
        session.add(raw)
        await session.flush()
        source_event = Event(
            external_id="source",
            source_id=history.id,
            sport="football",
            game="FC26",
            started_at=start,
            status="finished",
        )
        bookmaker_event = Event(
            external_id="bookmaker",
            source_id=bookmaker.id,
            sport="football",
            game="FC26",
            started_at=start,
            status="finished",
            raw_payload_id=raw.id,
        )
        session.add_all((source_event, bookmaker_event))
        await session.flush()
        event_match = EventMatch(
            source_event_id=source_event.id,
            bookmaker_event_id=bookmaker_event.id,
            confidence=Decimal("0.99"),
            status="matched",
            components={"reversed_sides": False},
            matched_at=start,
        )
        result = Result(
            event_id=source_event.id,
            score1=3,
            score2=1,
            winner="P1",
            is_draw=False,
            total=4,
            observed_at=start + timedelta(minutes=5),
        )
        market = Market(
            event_id=bookmaker_event.id,
            external_id="factor:930",
            code="total",
            name="Full time total",
        )
        session.add_all((event_match, result, market))
        await session.flush()

        def quote(line: str, odds: str, received_at: datetime) -> OddsSnapshot:
            return OddsSnapshot(
                event_id=bookmaker_event.id,
                bookmaker_source_id=bookmaker.id,
                market_id=market.id,
                selection="over",
                line=Decimal(line),
                odds=Decimal(odds),
                received_at=received_at,
                raw_payload_id=raw.id,
            )

        session.add_all(
            (
                quote("3.5", "1.80", start - timedelta(minutes=10)),
                quote("3.5", "1.90", start - timedelta(minutes=1)),
                quote("4.0", "2.00", start - timedelta(minutes=2)),
                quote("4.5", "2.10", start - timedelta(minutes=3)),
                quote("5.5", "2.20", start - timedelta(minutes=30)),
                quote("6.5", "2.30", start + timedelta(minutes=1)),
            )
        )
        invalid = quote("7.5", "2.40", start - timedelta(minutes=1))
        invalid.selection = "sideways"
        session.add(invalid)

    repository = CorridorRepository()
    async with sessions.begin() as session:
        first = await repository.rebuild(
            session,
            as_of=as_of,
            min_samples=1,
            max_odds_age=timedelta(minutes=15),
        )
    async with sessions.begin() as session:
        second = await repository.rebuild(
            session,
            as_of=as_of,
            min_samples=1,
            max_odds_age=timedelta(minutes=15),
        )
    async with sessions() as session:
        observations = (
            await session.scalars(
                select(CorridorObservation).order_by(CorridorObservation.line)
            )
        ).all()
        corridors = (
            await session.scalars(
                select(OddsCorridor)
                .where(OddsCorridor.is_active.is_(True))
                .order_by(OddsCorridor.line)
            )
        ).all()
        over_35 = await repository.lookup(
            session,
            bookmaker_source_id=bookmaker.id,
            sport="football",
            game="FC26",
            tournament_family=None,
            market_code="total",
            selection="over",
            line=Decimal("3.5"),
            odds=Decimal("1.90"),
            cutoff_at=as_of,
            min_samples=1,
        )

    assert first.observations_materialized == 3
    assert first.observations_created == 3
    assert first.observations_returned == 1
    assert second.observations_created == 0
    assert second.observations_updated == 3
    assert {item.line for item in observations} == {
        Decimal("3.500"),
        Decimal("4.000"),
        Decimal("4.500"),
    }
    assert observations[0].odds == Decimal("1.90000")
    assert {item.line for item in corridors} == {
        Decimal("3.500"),
        Decimal("4.000"),
        Decimal("4.500"),
    }
    assert over_35 is not None
    assert over_35.wins == 1
    assert over_35.losses == 0
    assert over_35.returns == 0
    await engine.dispose()
