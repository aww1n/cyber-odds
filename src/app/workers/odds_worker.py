from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import FonbetCatalogRepository, PersistedCatalog
from app.parsers.fonbet import FonbetCatalog, parse_fonbet_catalog
from app.providers.base import OddsProvider
from app.storage import ArchivedPayload, FilesystemRawArchive


@dataclass(frozen=True, slots=True)
class CollectionResult:
    catalog: FonbetCatalog
    archive: ArchivedPayload
    persisted: PersistedCatalog | None
    latency_ms: int


class FonbetOddsCollector:
    def __init__(
        self,
        *,
        provider: OddsProvider,
        archive: FilesystemRawArchive,
        repository: FonbetCatalogRepository | None = None,
    ) -> None:
        self._provider = provider
        self._archive = archive
        self._repository = repository or FonbetCatalogRepository()

    async def collect_once(self, session: AsyncSession | None = None) -> CollectionResult:
        started = perf_counter()
        payload = await self._provider.fetch_events()
        archived = self._archive.archive(payload)
        if not isinstance(payload.data, dict):
            raise ValueError("Fonbet event catalog must be a JSON object")
        catalog = parse_fonbet_catalog(payload.data)
        latency_ms = round((perf_counter() - started) * 1000)

        persisted = None
        if session is not None:
            persisted = await self._repository.persist(
                session,
                payload=payload,
                archive=archived,
                catalog=catalog,
                latency_ms=latency_ms,
            )
        return CollectionResult(
            catalog=catalog,
            archive=archived,
            persisted=persisted,
            latency_ms=latency_ms,
        )
