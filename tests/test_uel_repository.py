from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import Event, EventParticipant, RawPayload, Result, Source
from app.providers import UELProvider
from app.storage import FilesystemRawArchive
from app.workers.uel_history_worker import UELHistoryCollector

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.anyio
async def test_uel_worker_persists_history_and_keeps_post_raw(tmp_path: Path) -> None:
    responses = {
        "/api/efootball/load/tours/list": (FIXTURES / "uel_tours_page.json").read_bytes(),
        "/api/efootball/load/tour/data/226610": (
            FIXTURES / "uel_tour_data.json"
        ).read_bytes(),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=responses[request.url.path],
            headers={"content-type": "application/json"},
        )

    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'uel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = UELProvider(base_url="https://uel.example", client=client)
        collector = UELHistoryCollector(
            provider=provider,
            archive=FilesystemRawArchive(tmp_path / "raw"),
            source_timezone="Europe/Moscow",
        )
        async with sessions.begin() as session:
            backfill = await collector.collect_page(session=session)

    assert backfill.events_parsed == 2
    assert backfill.results_parsed == 2
    async with sessions() as session:
        source = await session.scalar(select(Source).where(Source.code == "uel_ef"))
        assert source is not None
        assert await session.scalar(select(func.count()).select_from(Event)) == 2
        assert await session.scalar(select(func.count()).select_from(Result)) == 2
        assert await session.scalar(select(func.count()).select_from(RawPayload)) == 2
        paul = await session.scalar(
            select(EventParticipant).where(
                EventParticipant.external_player_key == "183192"
            )
        )
        assert paul is not None
        assert paul.raw_player_name == "Paulblack17"
        assert paul.external_team_id == "80066"
        result = await session.scalar(select(Result).order_by(Result.event_id))
        assert result is not None
        assert result.source_updated_at is not None

    metadata = list((tmp_path / "raw" / "uel").rglob("*.meta.json"))
    assert len(metadata) == 2
    await engine.dispose()
