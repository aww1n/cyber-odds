from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.providers.base import OddsProvider, ProviderError, ProviderPayload


class FonbetProvider(OddsProvider):
    """HTTP-only provider for a configured and independently verified line host."""

    provider_name = "fonbet"

    def __init__(
        self,
        *,
        base_url: str,
        scope_market: int = 1600,
        language: str = "ru",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Fonbet base_url must be an absolute HTTP(S) URL")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("Fonbet base_url must not contain query parameters or a fragment")
        if scope_market <= 0:
            raise ValueError("scope_market must be greater than zero")

        self._base_url = base_url.rstrip("/")
        self._scope_market = scope_market
        self._language = language
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "cyber-odds-research/0.1 (+HTTP data collector)",
            },
        )

    async def __aenter__(self) -> FonbetProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def fetch_events(self) -> ProviderPayload:
        return await self._get_json(
            kind="events_list_base",
            path="/events/listBase",
            params={"scopeMarket": self._scope_market, "lang": self._language},
        )

    async def fetch_odds(self, event_external_id: str) -> ProviderPayload:
        try:
            event_id = int(event_external_id)
        except ValueError as error:
            raise ValueError("Fonbet event_external_id must be an integer string") from error
        if event_id <= 0:
            raise ValueError("Fonbet event_external_id must be greater than zero")
        return await self._get_json(
            kind="event_odds",
            path="/events/event",
            params={
                "eventId": event_id,
                "scopeMarket": self._scope_market,
                "lang": self._language,
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get_json(
        self, *, kind: str, path: str, params: dict[str, int | str]
    ) -> ProviderPayload:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderError(f"Fonbet request failed for {path}: {error}") from error

        content_type = response.headers.get("content-type")
        try:
            parsed: Any = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderError(f"Fonbet returned non-JSON content for {path}") from error
        if not isinstance(parsed, dict):
            raise ProviderError(f"Fonbet returned a non-object JSON payload for {path}")
        if not isinstance(parsed.get("events"), list):
            raise ProviderError(f"Fonbet payload for {path} has no events array")

        received_at = datetime.now(UTC)

        return ProviderPayload(
            provider=self.provider_name,
            kind=kind,
            url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            headers={key: value for key, value in response.headers.items()},
            received_at=received_at,
            body=response.content,
            data=parsed,
        )
