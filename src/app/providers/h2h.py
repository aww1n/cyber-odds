from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.providers.base import JsonValue, ProviderError, ProviderPayload

SISH2HSport = Literal["fifa", "nba", "nfl"]


class SISH2HProvider:
    """HTTP client for routes published by the official H2H GGL frontend."""

    provider_name = "sis_h2h"

    def __init__(
        self,
        *,
        base_url: str = "https://api-h2h.hudstats.com",
        sport: SISH2HSport = "fifa",
        source_timezone: str = "Europe/Moscow",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("SIS H2H base_url must be an absolute HTTP(S) URL")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("SIS H2H base_url must not contain a query or fragment")
        if sport not in {"fifa", "nba", "nfl"}:
            raise ValueError("SIS H2H sport must be fifa, nba or nfl")
        try:
            self._timezone = ZoneInfo(source_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"Unknown SIS H2H source timezone: {source_timezone}") from error
        self._base_url = base_url.rstrip("/")
        self._sport = sport
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "cyber-odds-research/0.1 (+HTTP data collector)",
            },
        )

    @property
    def sport(self) -> SISH2HSport:
        return self._sport

    @property
    def source_timezone(self) -> str:
        return self._timezone.key

    async def fetch_schedule(self, day: date) -> ProviderPayload:
        local_midnight = datetime.combine(day, time.min, tzinfo=self._timezone)
        return await self._get_json_array(
            kind=f"{self._sport}_schedule_day",
            path=f"/v1/schedule/{self._sport}",
            params={"date": local_midnight.isoformat()},
        )

    async def fetch_participant_names(self) -> ProviderPayload:
        return await self._get_json_array(
            kind=f"{self._sport}_participant_names",
            path=f"/v1/participant/{self._sport}/names",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get_json_array(
        self,
        *,
        kind: str,
        path: str,
        params: dict[str, str] | None = None,
    ) -> ProviderPayload:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderError(f"SIS H2H request failed for {path}: {error}") from error
        try:
            parsed: Any = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderError(f"SIS H2H returned non-JSON content for {path}") from error
        if not isinstance(parsed, list):
            raise ProviderError(f"SIS H2H returned a non-array payload for {path}")
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
        )
