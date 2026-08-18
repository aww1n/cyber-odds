from __future__ import annotations

import json

import httpx
import pytest

from app.providers import FonbetProvider, ProviderError


@pytest.mark.anyio
async def test_provider_fetches_events_and_keeps_raw_body() -> None:
    body = json.dumps({"events": [], "sports": [], "customFactors": []}).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/listBase"
        assert request.url.params["scopeMarket"] == "1600"
        assert request.url.params["lang"] == "ru"
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = FonbetProvider(base_url="https://line.example", client=client)
        payload = await provider.fetch_events()

    assert payload.body == body
    assert isinstance(payload.data, dict)
    assert payload.data["events"] == []
    assert payload.kind == "events_list_base"


@pytest.mark.anyio
async def test_provider_separates_single_event_odds_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/event"
        assert request.url.params["eventId"] == "67277033"
        return httpx.Response(200, json={"events": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = FonbetProvider(base_url="https://line.example", client=client)
        payload = await provider.fetch_odds("67277033")

    assert payload.kind == "event_odds"


@pytest.mark.anyio
async def test_provider_rejects_non_json_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = FonbetProvider(base_url="https://line.example", client=client)
        with pytest.raises(ProviderError, match="non-JSON"):
            await provider.fetch_events()
