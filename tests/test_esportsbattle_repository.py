from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import (
    Event,
    EventParticipant,
    ParserRun,
    RawPayload,
    Result,
    Tournament,
)
from app.providers import ESportsBattleProvider
from app.storage import FilesystemRawArchive
from app.workers.history_worker import ESBHistoryCollector

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.anyio
async def test_history_worker_upserts_domain_rows_and_retains_every_raw_response(
    tmp_path: Path,
) -> None:
    responses = {
        "/api/participants/Artrom/tournaments": (
            FIXTURES / "esb_participant_tournaments.json"
        ).read_bytes(),
        "/api/tournaments/252708": (FIXTURES / "esb_tournament.json").read_bytes(),
        "/api/tournaments/252708/matches": (FIXTURES / "esb_matches.json").read_bytes(),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        body = responses[request.url.path]
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ESportsBattleProvider(base_url="https://football.example", client=client)
        collector = ESBHistoryCollector(
            provider=provider,
            archive=FilesystemRawArchive(tmp_path / "raw"),
        )
        for _ in range(2):
            async with sessions.begin() as session:
                result = await collector.collect_participant_page(
                    nickname="Artrom",
                    session=session,
                )
                assert result.events_parsed == 2
                assert result.results_parsed == 2

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tournament)) == 1
        assert await session.scalar(select(func.count()).select_from(Event)) == 2
        assert await session.scalar(select(func.count()).select_from(Result)) == 2
        assert await session.scalar(select(func.count()).select_from(EventParticipant)) == 4
        assert await session.scalar(select(func.count()).select_from(RawPayload)) == 6
        assert await session.scalar(select(func.count()).select_from(ParserRun)) == 2

        first_result = await session.scalar(select(Result).order_by(Result.event_id))
        assert first_result is not None
        assert first_result.settled_at is None
        assert first_result.observed_at is not None
        artrom = await session.scalar(
            select(EventParticipant).where(
                EventParticipant.external_player_key == "Artrom",
                EventParticipant.external_participant_id == "894728",
            )
        )
        assert artrom is not None
        assert artrom.external_team_id == "221"
        assert artrom.raw_team_name == "Fenerbahce"

        raw_headers = await session.scalar(select(RawPayload.response_headers).limit(1))
        assert isinstance(raw_headers, dict)
        assert "set-cookie" not in raw_headers

    metadata_files = list((tmp_path / "raw" / "esb").rglob("*.meta.json"))
    assert len(metadata_files) == 6
    assert json.loads(metadata_files[0].read_text())["provider"] == "esb"
    await engine.dispose()
