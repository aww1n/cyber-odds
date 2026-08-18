from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import Event, EventMatch, Player, Source, Team, Tournament
from app.normalization.event_repository import EventMatchingRepository


@pytest.mark.anyio
async def test_repository_persists_only_unambiguous_automatic_match(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'matching.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)

    async with sessions.begin() as session:
        history_source = Source(code="esb", name="ESB", source_type="history")
        bookmaker_source = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        player1 = Player(display_name="A", normalized_name="a")
        player2 = Player(display_name="B", normalized_name="b")
        team1 = Team(display_name="Spain", normalized_name="spain")
        team2 = Team(display_name="Italy", normalized_name="italy")
        session.add_all((history_source, bookmaker_source, player1, player2, team1, team2))
        await session.flush()
        history_tournament = Tournament(
            source_id=history_source.id,
            external_id="h-tournament",
            name="FC 26 H2H Liga-1",
            normalized_name="fc 26 h2h liga-1",
            sport="football",
        )
        bookmaker_tournament = Tournament(
            source_id=bookmaker_source.id,
            external_id="b-tournament",
            name="FC 26 H2H Liga-1",
            normalized_name="fc 26 h2h liga-1",
            sport="football",
        )
        session.add_all((history_tournament, bookmaker_tournament))
        await session.flush()
        common = {
            "sport": "football",
            "game": "FC26",
            "started_at": datetime(2026, 8, 17, 12, tzinfo=UTC),
            "player1_id": player1.id,
            "player2_id": player2.id,
            "team1_id": team1.id,
            "team2_id": team2.id,
            "status": "scheduled",
        }
        historical_event = Event(
            external_id="history-event",
            source_id=history_source.id,
            tournament_id=history_tournament.id,
            **common,
        )
        bookmaker_event = Event(
            external_id="bookmaker-event",
            source_id=bookmaker_source.id,
            tournament_id=bookmaker_tournament.id,
            **common,
        )
        session.add_all((historical_event, bookmaker_event))
        await session.flush()
        bookmaker_event_id = bookmaker_event.id

    repository = EventMatchingRepository()
    async with sessions.begin() as session:
        stats = await repository.match_sources(
            session,
            historical_source_code="esb",
            bookmaker_source_code="fonbet",
        )

    assert stats.automatic_matches == 1
    assert stats.ambiguous_matches == 0
    async with sessions() as session:
        stored = await session.scalar(select(EventMatch))
        assert stored is not None
        assert stored.status == "matched"
        assert float(stored.confidence) == 1.0
        assert stored.components["quality"] == "exact"

    async with sessions.begin() as session:
        stored_event = await session.get(Event, bookmaker_event_id)
        assert stored_event is not None
        stored_event.started_at += timedelta(hours=2)

    async with sessions.begin() as session:
        stale_stats = await repository.match_sources(
            session,
            historical_source_code="esb",
            bookmaker_source_code="fonbet",
        )
    async with sessions() as session:
        stored = await session.scalar(select(EventMatch))

    assert stale_stats.rejected_without_candidate == 1
    assert stored is not None
    assert stored.status == "rejected"

    await engine.dispose()
