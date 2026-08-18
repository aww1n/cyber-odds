from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import Event, Player, Result, Source
from app.features import DatabaseFeatureBuilder


@pytest.mark.anyio
async def test_database_feature_builder_enforces_event_and_availability_cutoffs(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 17, 12, tzinfo=UTC)
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'features.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)

    async with sessions.begin() as session:
        source = Source(code="esb", name="ESB", source_type="history")
        player_a = Player(display_name="A", normalized_name="a")
        player_b = Player(display_name="B", normalized_name="b")
        player_c = Player(display_name="C", normalized_name="c")
        session.add_all((source, player_a, player_b, player_c))
        await session.flush()

        def event(external_id: str, started_at: datetime) -> Event:
            return Event(
                external_id=external_id,
                source_id=source.id,
                sport="football",
                game="FC26",
                started_at=started_at,
                player1_id=player_a.id,
                player2_id=player_c.id,
                status="finished",
            )

        eligible = event("eligible", cutoff - timedelta(minutes=30))
        observed_at_cutoff = event("observed-at-cutoff", cutoff - timedelta(minutes=20))
        source_updated_eligible = event(
            "source-updated-eligible", cutoff - timedelta(minutes=25)
        )
        future_event = event("future", cutoff + timedelta(seconds=1))
        target = Event(
            external_id="target",
            source_id=source.id,
            sport="football",
            game="FC26",
            started_at=cutoff,
            player1_id=player_a.id,
            player2_id=player_b.id,
            status="scheduled",
        )
        session.add_all(
            (eligible, observed_at_cutoff, source_updated_eligible, future_event, target)
        )
        await session.flush()
        for item, observed_at in (
            (eligible, cutoff - timedelta(minutes=10)),
            (observed_at_cutoff, cutoff),
            (future_event, cutoff - timedelta(minutes=1)),
        ):
            session.add(
                Result(
                    event_id=item.id,
                    score1=2,
                    score2=1,
                    winner="P1",
                    is_draw=False,
                    total=3,
                    settled_at=None,
                    observed_at=observed_at,
                )
            )
        session.add(
            Result(
                event_id=source_updated_eligible.id,
                score1=1,
                score2=1,
                winner="X",
                is_draw=True,
                total=2,
                settled_at=None,
                source_updated_at=cutoff - timedelta(minutes=5),
                observed_at=cutoff + timedelta(days=1),
            )
        )
        target_id = target.id

    async with sessions() as session:
        features = await DatabaseFeatureBuilder().build(session, event_id=target_id)
        earlier_features = await DatabaseFeatureBuilder().build(
            session,
            event_id=target_id,
            cutoff_at=cutoff - timedelta(minutes=7),
        )

    assert features.eligible_match_ids == (source_updated_eligible.id, eligible.id)
    assert features.values["p1_global_all_matches"] == 2
    assert earlier_features.cutoff_at == cutoff - timedelta(minutes=7)
    assert earlier_features.eligible_match_ids == (eligible.id,)
    await engine.dispose()
