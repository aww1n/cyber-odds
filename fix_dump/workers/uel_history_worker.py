from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    PersistedUELHistory,
    UELHistoryInput,
    UELHistoryRepository,
)
from app.parsers.uel import (
    UELTournamentHistory,
    UELTourRef,
    UELToursPage,
    parse_uel_tournament_history,
    parse_uel_tours_page,
)
from app.providers.base import ProviderPayload
from app.storage import ArchivedPayload, FilesystemRawArchive

LOGGER = logging.getLogger(__name__)


class UELHistoryProvider(Protocol):
    @property
    def sport(self) -> str: ...

    async def fetch_tours(
        self, *, page: int = 1, items_per_page: int = 10
    ) -> ProviderPayload: ...

    async def fetch_tour_data(self, tour_source_id: str) -> ProviderPayload: ...


@dataclass(frozen=True, slots=True)
class UELBackfillResult:
    page: int
    tours_page: UELToursPage
    histories: tuple[UELTournamentHistory, ...]
    archives: tuple[ArchivedPayload, ...]
    persisted: PersistedUELHistory | None
    latency_ms: int

    @property
    def events_parsed(self) -> int:
        return sum(len(history.matches) for history in self.histories)

    @property
    def results_parsed(self) -> int:
        return sum(
            1 for history in self.histories for match in history.matches if match.is_finished
        )


class UELHistoryCollector:
    def __init__(
        self,
        *,
        provider: UELHistoryProvider,
        archive: FilesystemRawArchive,
        source_timezone: str,
        repository: UELHistoryRepository | None = None,
    ) -> None:
        self._provider = provider
        self._archive = archive
        self._source_timezone = source_timezone
        self._repository = repository or UELHistoryRepository()

    async def collect_page(
        self,
        *,
        page: int = 1,
        items_per_page: int = 10,
        max_tournaments: int = 1,
        finished_only: bool = True,
        session: AsyncSession | None = None,
    ) -> UELBackfillResult:
        if max_tournaments <= 0:
            raise ValueError("max_tournaments must be greater than zero")
        started = perf_counter()
        discovery_payload = await self._provider.fetch_tours(
            page=page,
            items_per_page=items_per_page,
        )
        discovery_archive = self._archive.archive(discovery_payload)
        tours_page = parse_uel_tours_page(
            discovery_payload.data,
            items_per_page=items_per_page,
        )
        candidates = tours_page.tours
        if finished_only:
            candidates = tuple(item for item in candidates if item.state == "finished")
        selected = candidates[:max_tournaments]

        # Avoid bursting up to 100 HTTP requests at the UEL API at once when
        # HISTORY_MAX_TOURNAMENTS is high. Bounded concurrency still keeps the
        # backfill fast while reducing rate-limit/transient-failure pressure.
        semaphore = asyncio.Semaphore(10)

        async def fetch_data(tour: UELTourRef) -> ProviderPayload:
            async with semaphore:
                return await self._provider.fetch_tour_data(tour.external_id)

        data_payloads = await asyncio.gather(
            *(fetch_data(item) for item in selected),
            return_exceptions=True,
        )
        histories: list[UELTournamentHistory] = []
        archives: list[ArchivedPayload] = [discovery_archive]
        persistence_inputs: list[UELHistoryInput] = []
        for tour, data_payload in zip(selected, data_payloads, strict=True):
            if isinstance(data_payload, asyncio.CancelledError):
                raise data_payload
            if isinstance(data_payload, Exception):
                LOGGER.warning(
                    "UEL tour-data fetch failed external_id=%s name=%s: %s",
                    tour.external_id,
                    tour.name,
                    data_payload,
                )
                continue
            try:
                data_archive = self._archive.archive(data_payload)
                history = parse_uel_tournament_history(
                    tour,
                    data_payload.data,
                    source_timezone=self._source_timezone,
                    uel_sport=self._provider.sport,
                )
            except Exception:
                LOGGER.exception(
                    "UEL tour-data parse/archive failed external_id=%s name=%s",
                    tour.external_id,
                    tour.name,
                )
                continue
            histories.append(history)
            archives.append(data_archive)
            persistence_inputs.append(
                UELHistoryInput(
                    history=history,
                    data_payload=data_payload,
                    data_archive=data_archive,
                )
            )

        latency_ms = round((perf_counter() - started) * 1000)
        persisted = None
        if session is not None:
            persisted = await self._repository.persist(
                session,
                discovery_payload=discovery_payload,
                discovery_archive=discovery_archive,
                histories=persistence_inputs,
                latency_ms=latency_ms,
            )
        return UELBackfillResult(
            page=page,
            tours_page=tours_page,
            histories=tuple(histories),
            archives=tuple(archives),
            persisted=persisted,
            latency_ms=latency_ms,
        )
