from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.database import Base, build_async_engine, build_session_factory
from app.database.audit import audit_database
from app.database.models import Event, Result, Source


@pytest.mark.anyio
async def test_database_audit_covers_all_pipeline_tables(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)

    async with sessions() as session:
        audit = await audit_database(session)

    assert audit.healthy
    assert {
        "sources",
        "players",
        "teams",
        "tournaments",
        "events",
        "event_participants",
        "results",
        "odds_snapshots",
        "event_matches",
        "model_predictions",
        "signals",
        "settlements",
        "corridor_observations",
        "odds_corridors",
    } <= audit.counts.keys()
    assert all(value == 0 for value in audit.problems.values())
    await engine.dispose()


@pytest.mark.anyio
async def test_database_audit_detects_invalid_result_contract(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit-result.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)

    async with sessions.begin() as session:
        source = Source(code="uel_ef", name="UEL", source_type="history")
        session.add(source)
        await session.flush()
        event = Event(
            source_id=source.id,
            external_id="uel-invalid-result",
            sport="football",
            started_at=now,
            status="finished",
        )
        session.add(event)
        await session.flush()
        session.add(
            Result(
                event_id=event.id,
                score1=2,
                score2=1,
                winner="P2",
                is_draw=False,
                total=99,
                observed_at=now,
            )
        )

    async with sessions() as session:
        audit = await audit_database(session)

    assert not audit.healthy
    assert audit.problems["result_score_contract_mismatch"] == 1
    assert audit.problems["results_on_non_history_source"] == 0
    await engine.dispose()
