from __future__ import annotations

import httpx
import pytest

from app.providers import ProviderError, UELProvider


@pytest.mark.anyio
async def test_uel_tours_uses_confirmed_post_contract_and_preserves_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/efootball/load/tours/list"
        assert request.content == b'{"current_page":2,"items_per_page":5}'
        return httpx.Response(200, json={"items": [], "total_items": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = UELProvider(base_url="https://uel.example", client=client)
        payload = await provider.fetch_tours(page=2, items_per_page=5)

    assert payload.request_method == "POST"
    assert payload.request_body == b'{"current_page":2,"items_per_page":5}'
    assert isinstance(payload.data, dict)


@pytest.mark.anyio
async def test_uel_rejects_wrong_response_shape() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = UELProvider(base_url="https://uel.example", client=client)
        with pytest.raises(ProviderError, match="non-object"):
            await provider.fetch_tours()
