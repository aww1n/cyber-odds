from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import Event, EventParticipant, RawPayload, Result, Source, Tournament
from app.providers import SISH2HProvider
from app.storage import FilesystemRawArchive
from app.workers.h2h_history_worker import SISH2HHistoryCollector

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.anyio
async def test_sis_h2h_worker_persists_daily_history_under_explicit_source(
    tmp_path: Path,
) -> None:
    body = (FIXTURES / "sis_h2h_schedule.json").read_bytes()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/schedule/fifa"
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sis-h2h.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SISH2HProvider(base_url="https://h2h.example", client=client)
        collector = SISH2HHistoryCollector(
            provider=provider,
            archive=FilesystemRawArchive(tmp_path / "raw"),
        )
        async with sessions.begin() as session:
            backfill = await collector.collect_day(date(2026, 8, 17), session=session)

    assert backfill.events_parsed == 3
    assert backfill.results_parsed == 2
    async with sessions() as session:
        source = await session.scalar(
            select(Source).where(Source.code == "sis_h2h_esoccer")
        )
        assert source is not None
        assert await session.scalar(select(func.count()).select_from(Event)) == 3
        assert await session.scalar(select(func.count()).select_from(Result)) == 2
        assert await session.scalar(select(func.count()).select_from(RawPayload)) == 1
        tournament = await session.scalar(select(Tournament))
        assert tournament is not None
        assert tournament.family == "h2h_ggl"
        cosmos = await session.scalar(
            select(EventParticipant).where(
                EventParticipant.external_player_key == "COSMOS"
            )
        )
        assert cosmos is not None
        assert cosmos.raw_player_name == "COSMOS"
        assert cosmos.raw_team_name == "FRANCE"

    metadata = list((tmp_path / "raw" / "sis_h2h").rglob("*.meta.json"))
    assert len(metadata) == 1
    await engine.dispose()
