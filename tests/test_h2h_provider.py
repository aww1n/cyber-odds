from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.providers import ProviderError, SISH2HProvider


@pytest.mark.anyio
async def test_sis_h2h_schedule_uses_timezone_aware_official_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/schedule/fifa"
        assert request.url.params["date"] == "2026-08-17T00:00:00+03:00"
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SISH2HProvider(
            base_url="https://h2h.example",
            source_timezone="Europe/Moscow",
            client=client,
        )
        payload = await provider.fetch_schedule(date(2026, 8, 17))

    assert payload.provider == "sis_h2h"
    assert payload.kind == "fifa_schedule_day"
    assert payload.request_method == "GET"
    assert payload.request_body is None


@pytest.mark.anyio
async def test_sis_h2h_rejects_wrong_response_shape() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SISH2HProvider(base_url="https://h2h.example", client=client)
        with pytest.raises(ProviderError, match="non-array"):
            await provider.fetch_schedule(date(2026, 8, 17))
