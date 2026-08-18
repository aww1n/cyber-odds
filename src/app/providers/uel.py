from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from app.providers.base import JsonValue, ProviderError, ProviderPayload

UELSport = Literal["efootball", "ehockey"]


class UELProvider:
    """HTTP client for POST routes extracted from the official UEL frontend."""

    provider_name = "uel"

    def __init__(
        self,
        *,
        base_url: str = "https://api.unitedleagues.gg",
        sport: UELSport = "efootball",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("UEL base_url must be an absolute HTTP(S) URL")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("UEL base_url must not contain query parameters or a fragment")
        if sport not in {"efootball", "ehockey"}:
            raise ValueError("UEL sport must be efootball or ehockey")
        self._base_url = base_url.rstrip("/")
        self._sport = sport
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "cyber-odds-research/0.1 (+HTTP data collector)",
            },
        )

    @property
    def sport(self) -> UELSport:
        return self._sport

    async def fetch_tours(self, *, page: int = 1, items_per_page: int = 10) -> ProviderPayload:
        if page <= 0 or items_per_page <= 0 or items_per_page > 100:
            raise ValueError("page and items_per_page must be within allowed positive ranges")
        return await self._post_json(
            kind=f"{self._sport}_tours_page",
            path=f"/api/{self._sport}/load/tours/list",
            request={"current_page": page, "items_per_page": items_per_page},
            expected="object",
        )

    async def fetch_tour(self, tour_id: str) -> ProviderPayload:
        return await self._post_json(
            kind=f"{self._sport}_tour",
            path=f"/api/{self._sport}/load/tour/{self._positive_id(tour_id, 'tour_id')}",
            request={},
            expected="object",
        )

    async def fetch_tour_data(self, tour_source_id: str) -> ProviderPayload:
        return await self._post_json(
            kind=f"{self._sport}_tour_data",
            path=(
                f"/api/{self._sport}/load/tour/data/"
                f"{self._positive_id(tour_source_id, 'tour_source_id')}"
            ),
            request={},
            expected="object",
        )

    async def fetch_players(self) -> ProviderPayload:
        return await self._post_json(
            kind=f"{self._sport}_players",
            path=f"/api/{self._sport}/load/players/list",
            request={},
            expected="array",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _positive_id(value: str, field: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError(f"{field} must be an integer string") from error
        if parsed <= 0:
            raise ValueError(f"{field} must be greater than zero")
        return parsed

    async def _post_json(
        self,
        *,
        kind: str,
        path: str,
        request: dict[str, int],
        expected: Literal["object", "array"],
    ) -> ProviderPayload:
        url = f"{self._base_url}{path}"
        request_body = json.dumps(request, separators=(",", ":")).encode()
        try:
            response = await self._client.post(url, content=request_body)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderError(f"UEL request failed for {path}: {error}") from error
        try:
            parsed: Any = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderError(f"UEL returned non-JSON content for {path}") from error
        if expected == "object" and not isinstance(parsed, dict):
            raise ProviderError(f"UEL returned a non-object payload for {path}")
        if expected == "array" and not isinstance(parsed, list):
            raise ProviderError(f"UEL returned a non-array payload for {path}")
        data: JsonValue = parsed
        return ProviderPayload(
            provider=self.provider_name,
            kind=kind,
            url=str(response.url),
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            headers={key: value for key, value in response.headers.items()},
            received_at=datetime.now(UTC),
            body=response.content,
            data=data,
            request_method="POST",
            request_body=request_body,
        )
