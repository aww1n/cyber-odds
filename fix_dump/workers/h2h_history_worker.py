from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import PersistedSISH2HHistory, SISH2HHistoryRepository
from app.parsers.h2h import SISH2HDailyHistory, parse_sis_h2h_schedule
from app.providers.base import ProviderPayload
from app.storage import ArchivedPayload, FilesystemRawArchive


class SISH2HHistoryProvider(Protocol):
    @property
    def sport(self) -> str: ...

    async def fetch_schedule(self, day: date) -> ProviderPayload: ...


@dataclass(frozen=True, slots=True)
class SISH2HBackfillResult:
    history: SISH2HDailyHistory
    archive: ArchivedPayload
    persisted: PersistedSISH2HHistory | None
    latency_ms: int

    @property
    def events_parsed(self) -> int:
        return len(self.history.matches)

    @property
    def results_parsed(self) -> int:
        return sum(match.is_finished for match in self.history.matches)


class SISH2HHistoryCollector:
    def __init__(
        self,
        *,
        provider: SISH2HHistoryProvider,
        archive: FilesystemRawArchive,
        repository: SISH2HHistoryRepository | None = None,
    ) -> None:
        self._provider = provider
        self._archive = archive
        self._repository = repository or SISH2HHistoryRepository()

    async def collect_day(
        self,
        day: date,
        session: AsyncSession | None = None,
    ) -> SISH2HBackfillResult:
        started = perf_counter()
        payload = await self._provider.fetch_schedule(day)
        archive = self._archive.archive(payload)
        history = parse_sis_h2h_schedule(
            payload.data,
            day=day,
            api_sport=self._provider.sport,
        )
        latency_ms = round((perf_counter() - started) * 1000)
        persisted = None
        if session is not None:
            persisted = await self._repository.persist(
                session,
                payload=payload,
                archive=archive,
                history=history,
                latency_ms=latency_ms,
            )
        return SISH2HBackfillResult(history, archive, persisted, latency_ms)
