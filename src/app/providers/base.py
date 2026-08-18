from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

JsonObject = dict[str, Any]
JsonArray = list[Any]
JsonValue = JsonObject | JsonArray


class ProviderError(RuntimeError):
    """External provider failed or returned an invalid contract."""


@dataclass(frozen=True, slots=True)
class ProviderPayload:
    provider: str
    kind: str
    url: str
    status_code: int
    content_type: str | None
    headers: dict[str, str]
    received_at: datetime
    body: bytes
    data: JsonValue
    request_method: str = "GET"
    request_body: bytes | None = None


class OddsProvider(ABC):
    @abstractmethod
    async def fetch_events(self) -> ProviderPayload:
        """Fetch the provider's event catalog."""

    @abstractmethod
    async def fetch_odds(self, event_external_id: str) -> ProviderPayload:
        """Fetch current odds for one provider event."""

    @abstractmethod
    async def close(self) -> None:
        """Close owned network resources."""
