from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.database import Base, build_async_engine, build_session_factory
from app.database.models import Event, Market, OddsSnapshot, RawPayload
from app.database.repositories import FonbetCatalogRepository
from app.parsers.fonbet import parse_fonbet_catalog
from app.providers.base import ProviderPayload
from app.storage import FilesystemRawArchive


def _fixture() -> tuple[ProviderPayload, dict[str, Any]]:
    path = Path(__file__).parent / "fixtures" / "fonbet_list_base.json"
    body = path.read_bytes()
    data = json.loads(body)
    payload = ProviderPayload(
        provider="fonbet",
        kind="events_list_base",
        url="https://line.example/events/listBase?scopeMarket=1600&lang=ru",
        status_code=200,
        content_type="application/json",
        headers={"content-type": "application/json"},
        received_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        body=body,
        data=data,
    )
    return payload, data


@pytest.mark.anyio
async def test_repository_upserts_events_but_appends_every_odds_snapshot(tmp_path: Path) -> None:
    engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    payload, data = _fixture()
    catalog = parse_fonbet_catalog(data)
    archive = FilesystemRawArchive(tmp_path / "raw")
    repository = FonbetCatalogRepository()

    async with sessions.begin() as session:
        await repository.persist(
            session,
            payload=payload,
            archive=archive.archive(payload),
            catalog=catalog,
            latency_ms=10,
        )
    async with sessions.begin() as session:
        await repository.persist(
            session,
            payload=payload,
            archive=archive.archive(payload),
            catalog=catalog,
            latency_ms=11,
        )

    async with sessions() as session:
        event_count = await session.scalar(select(func.count()).select_from(Event))
        odds_count = await session.scalar(select(func.count()).select_from(OddsSnapshot))
        raw_count = await session.scalar(select(func.count()).select_from(RawPayload))
        mapped_total = await session.scalar(
            select(OddsSnapshot)
            .join(Market, Market.id == OddsSnapshot.market_id)
            .where(Market.code == "total")
            .limit(1)
        )

        assert event_count == 2
        assert odds_count == len(catalog.quotes) * 2
        assert raw_count == 2
        assert mapped_total is not None
        # Modern normalized format: selection="over" with line stored separately
        assert mapped_total.selection == "over"
        assert mapped_total.line == Decimal("2.5")
    await engine.dispose()
