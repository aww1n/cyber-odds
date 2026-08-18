from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.providers.uel import UELProvider
from app.storage import FilesystemRawArchive
from app.workers.uel_history_worker import UELHistoryCollector

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.anyio
async def test_uel_collector_keeps_good_tours_when_one_tour_request_fails(
    tmp_path: Path,
) -> None:
    responses = {
        "/api/efootball/load/tours/list": (FIXTURES / "uel_tours_page.json").read_bytes(),
        "/api/efootball/load/tour/data/226610": (
            FIXTURES / "uel_tour_data.json"
        ).read_bytes(),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in responses:
            return httpx.Response(404, json={"error": "missing fixture"})
        return httpx.Response(
            200,
            content=responses[request.url.path],
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = UELProvider(client=client, sport="efootball")
        collector = UELHistoryCollector(
            provider=provider,
            archive=FilesystemRawArchive(tmp_path / "raw"),
            source_timezone="Europe/Moscow",
        )
        result = await collector.collect_page(
            page=1,
            items_per_page=10,
            max_tournaments=2,
            finished_only=False,
        )

    assert len(result.histories) == 1
    assert result.histories[0].external_id == "226610"
    assert result.events_parsed == 2
    assert result.results_parsed == 2
