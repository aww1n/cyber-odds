from __future__ import annotations

import json

import httpx
import pytest

from app.providers import ESportsBattleProvider, ProviderError


@pytest.mark.anyio
async def test_provider_fetches_participant_tournaments() -> None:
    body = json.dumps({"totalPages": 1, "tournaments": []}).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/participants/A B/tournaments"
        assert request.url.params["page"] == "2"
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ESportsBattleProvider(base_url="https://football.example", client=client)
        payload = await provider.fetch_participant_tournaments("A B", page=2)

    assert payload.kind == "participant_tournaments_page"
    assert payload.body == body
    assert isinstance(payload.data, dict)
    assert payload.data["totalPages"] == 1


@pytest.mark.anyio
async def test_provider_accepts_array_contract_for_matches() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tournaments/252708/matches"
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ESportsBattleProvider(base_url="https://football.example", client=client)
        payload = await provider.fetch_tournament_matches("252708")

    assert payload.kind == "tournament_matches"
    assert payload.data == []


@pytest.mark.anyio
async def test_provider_rejects_wrong_json_shape() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ESportsBattleProvider(base_url="https://football.example", client=client)
        with pytest.raises(ProviderError, match="non-array"):
            await provider.fetch_tournament_matches("252708")
