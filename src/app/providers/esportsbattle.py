from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx

from app.providers.base import JsonValue, ProviderError, ProviderPayload


class ESportsBattleProvider:
    """HTTP client for routes confirmed in the official eFootball frontend."""

    provider_name = "esb"

    def __init__(
        self,
        *,
        base_url: str = "https://football.esportsbattle.com",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("ESportsBattle base_url must be an absolute HTTP(S) URL")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError(
                "ESportsBattle base_url must not contain query parameters or a fragment"
            )

        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "cyber-odds-research/0.1 (+HTTP data collector)",
            },
        )

    async def __aenter__(self) -> ESportsBattleProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def fetch_participants(self, page: int = 1) -> ProviderPayload:
        return await self._get_json(
            kind="participants_page",
            path="/api/participants",
            params={"page": self._positive_page(page)},
            expected="object",
        )

    async def fetch_participant(self, nickname: str) -> ProviderPayload:
        return await self._get_json(
            kind="participant",
            path=f"/api/participants/{self._nickname_path(nickname)}",
            params=None,
            expected="object",
        )

    async def fetch_participant_tournaments(
        self, nickname: str, page: int = 1
    ) -> ProviderPayload:
        return await self._get_json(
            kind="participant_tournaments_page",
            path=f"/api/participants/{self._nickname_path(nickname)}/tournaments",
            params={"page": self._positive_page(page)},
            expected="object",
        )

    async def fetch_tournament(self, tournament_external_id: str) -> ProviderPayload:
        tournament_id = self._positive_id(tournament_external_id, "tournament_external_id")
        return await self._get_json(
            kind="tournament",
            path=f"/api/tournaments/{tournament_id}",
            params=None,
            expected="object",
        )

    async def fetch_tournament_matches(
        self, tournament_external_id: str
    ) -> ProviderPayload:
        tournament_id = self._positive_id(tournament_external_id, "tournament_external_id")
        return await self._get_json(
            kind="tournament_matches",
            path=f"/api/tournaments/{tournament_id}/matches",
            params=None,
            expected="array",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _positive_page(page: int) -> int:
        if page <= 0:
            raise ValueError("page must be greater than zero")
        return page

    @staticmethod
    def _positive_id(value: str, field: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError(f"{field} must be an integer string") from error
        if parsed <= 0:
            raise ValueError(f"{field} must be greater than zero")
        return parsed

    @staticmethod
    def _nickname_path(nickname: str) -> str:
        cleaned = nickname.strip()
        if not cleaned:
            raise ValueError("nickname must not be empty")
        return quote(cleaned, safe="")

    async def _get_json(
        self,
        *,
        kind: str,
        path: str,
        params: dict[str, int] | None,
        expected: Literal["object", "array"],
    ) -> ProviderPayload:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderError(f"ESportsBattle request failed for {path}: {error}") from error

        try:
            parsed: Any = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderError(
                f"ESportsBattle returned non-JSON content for {path}"
            ) from error
        if expected == "object" and not isinstance(parsed, dict):
            raise ProviderError(f"ESportsBattle returned a non-object payload for {path}")
        if expected == "array" and not isinstance(parsed, list):
            raise ProviderError(f"ESportsBattle returned a non-array payload for {path}")

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
