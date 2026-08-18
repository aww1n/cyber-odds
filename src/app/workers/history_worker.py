from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    ESBHistoryInput,
    ESBHistoryRepository,
    PersistedESBHistory,
)
from app.parsers.esportsbattle import (
    ESBTournamentHistory,
    ESBTournamentPage,
    parse_esb_tournament_history,
    parse_esb_tournament_page,
)
from app.providers.base import ProviderPayload
from app.storage import ArchivedPayload, FilesystemRawArchive

OBSERVED_FINISHED_TOURNAMENT_STATUS_ID = 4


class ESBProvider(Protocol):
    async def fetch_participant_tournaments(
        self, nickname: str, page: int = 1
    ) -> ProviderPayload: ...

    async def fetch_tournament(self, tournament_external_id: str) -> ProviderPayload: ...

    async def fetch_tournament_matches(
        self, tournament_external_id: str
    ) -> ProviderPayload: ...


@dataclass(frozen=True, slots=True)
class ESBBackfillResult:
    participant: str
    page: int
    tournament_page: ESBTournamentPage
    histories: tuple[ESBTournamentHistory, ...]
    archives: tuple[ArchivedPayload, ...]
    persisted: PersistedESBHistory | None
    latency_ms: int

    @property
    def events_parsed(self) -> int:
        return sum(len(history.matches) for history in self.histories)

    @property
    def results_parsed(self) -> int:
        return sum(
            1 for history in self.histories for match in history.matches if match.is_finished
        )


class ESBHistoryCollector:
    def __init__(
        self,
        *,
        provider: ESBProvider,
        archive: FilesystemRawArchive,
        repository: ESBHistoryRepository | None = None,
    ) -> None:
        self._provider = provider
        self._archive = archive
        self._repository = repository or ESBHistoryRepository()

    async def collect_participant_page(
        self,
        *,
        nickname: str,
        page: int = 1,
        max_tournaments: int = 1,
        finished_only: bool = True,
        session: AsyncSession | None = None,
    ) -> ESBBackfillResult:
        if max_tournaments <= 0:
            raise ValueError("max_tournaments must be greater than zero")
        started = perf_counter()
        discovery_payload = await self._provider.fetch_participant_tournaments(nickname, page)
        discovery_archive = self._archive.archive(discovery_payload)
        tournament_page = parse_esb_tournament_page(discovery_payload.data)
        candidates = tournament_page.tournaments
        if finished_only:
            candidates = tuple(
                item
                for item in candidates
                if item.status_id == OBSERVED_FINISHED_TOURNAMENT_STATUS_ID
            )
        selected = candidates[:max_tournaments]

        histories: list[ESBTournamentHistory] = []
        archives: list[ArchivedPayload] = [discovery_archive]
        persistence_inputs: list[ESBHistoryInput] = []
        for tournament in selected:
            tournament_payload, matches_payload = await asyncio.gather(
                self._provider.fetch_tournament(tournament.external_id),
                self._provider.fetch_tournament_matches(tournament.external_id),
            )
            tournament_archive = self._archive.archive(tournament_payload)
            matches_archive = self._archive.archive(matches_payload)
            history = parse_esb_tournament_history(
                tournament_payload.data,
                matches_payload.data,
            )
            histories.append(history)
            archives.extend((tournament_archive, matches_archive))
            persistence_inputs.append(
                ESBHistoryInput(
                    history=history,
                    tournament_payload=tournament_payload,
                    tournament_archive=tournament_archive,
                    matches_payload=matches_payload,
                    matches_archive=matches_archive,
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
        return ESBBackfillResult(
            participant=nickname,
            page=page,
            tournament_page=tournament_page,
            histories=tuple(histories),
            archives=tuple(archives),
            persisted=persisted,
            latency_ms=latency_ms,
        )
