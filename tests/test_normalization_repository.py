from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import (
    Event,
    EventParticipant,
    NormalizationReview,
    Player,
    PlayerAlias,
    Source,
    Team,
)
from app.normalization.repository import SourceParticipantNormalizer


@pytest.mark.anyio
async def test_normalizer_attaches_exact_creates_new_and_queues_fuzzy_review(
    tmp_path: Path,
) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'normalization.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)

    async with sessions.begin() as session:
        source = Source(
            code="esb",
            name="ESportsBattle",
            source_type="history",
            enabled=True,
        )
        known = Player(display_name="PRITISTREET", normalized_name="pritistreet")
        session.add_all((source, known))
        await session.flush()
        event = Event(
            external_id="match-1",
            source_id=source.id,
            sport="football",
            game="efootball",
            started_at=datetime(2026, 8, 16, 5, tzinfo=UTC),
            status="finished",
        )
        session.add(event)
        await session.flush()
        session.add_all(
            (
                EventParticipant(
                    event_id=event.id,
                    side=1,
                    raw_name="Spain (PRITISTREET)",
                    raw_player_name="Spain (PRITISTREET)",
                    external_player_key="Spain (PRITISTREET)",
                    raw_team_name="Spain",
                    raw_team_name_alt="Испания",
                ),
                EventParticipant(
                    event_id=event.id,
                    side=2,
                    raw_name="Italy (pritistret)",
                    raw_player_name="pritistret",
                    external_player_key="pritistret",
                    raw_team_name="Italy",
                ),
            )
        )

    normalizer = SourceParticipantNormalizer()
    async with sessions.begin() as session:
        stats = await normalizer.normalize_source(session, source_code="esb")

    assert stats.participants_seen == 2
    assert stats.players_attached == 1
    assert stats.players_created == 0
    assert stats.teams_attached == 2
    assert stats.teams_created == 2
    assert stats.reviews_created == 1

    async with sessions.begin() as session:
        repeated = await normalizer.normalize_source(session, source_code="esb")
    assert repeated.reviews_created == 0

    async with sessions.begin() as session:
        fonbet = Source(code="fonbet", name="Fonbet", source_type="bookmaker")
        session.add(fonbet)
        await session.flush()
        bookmaker_event = Event(
            external_id="bookmaker-match",
            source_id=fonbet.id,
            sport="football",
            game="FC26",
            started_at=datetime(2026, 8, 16, 6, tzinfo=UTC),
            status="scheduled",
        )
        session.add(bookmaker_event)
        await session.flush()
        session.add(
            EventParticipant(
                event_id=bookmaker_event.id,
                side=1,
                raw_name="Испания (PRITISTREET)",
                raw_player_name="PRITISTREET",
                raw_team_name="Испания",
            )
        )

    async with sessions.begin() as session:
        cross_source = await normalizer.normalize_source(session, source_code="fonbet")
    assert cross_source.players_created == 0
    assert cross_source.teams_created == 0
    assert cross_source.players_attached == 1
    assert cross_source.teams_attached == 1

    async with sessions() as session:
        stored_event = await session.scalar(
            select(Event).where(Event.external_id == "match-1")
        )
        assert stored_event is not None
        assert stored_event.player1_id is not None
        assert stored_event.player2_id is None
        assert stored_event.team1_id is not None
        assert stored_event.team2_id is not None
        assert await session.scalar(select(func.count()).select_from(PlayerAlias)) == 2
        assert await session.scalar(select(func.count()).select_from(Team)) == 2
        assert await session.scalar(select(func.count()).select_from(NormalizationReview)) == 1

    await engine.dispose()
