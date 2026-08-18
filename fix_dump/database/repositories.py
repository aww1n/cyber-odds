from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Event,
    EventParticipant,
    Market,
    OddsSnapshot,
    ParserRun,
    RawPayload,
    Result,
    Source,
    Tournament,
)
from app.parsers.esportsbattle import ESBTournamentHistory
from app.parsers.fonbet import FonbetCatalog
from app.parsers.h2h import SISH2HDailyHistory, tournament_external_id
from app.parsers.uel import UELTournamentHistory
from app.providers.base import ProviderPayload
from app.storage import ArchivedPayload


@dataclass(frozen=True, slots=True)
class PersistedCatalog:
    source_id: int
    raw_payload_id: int
    tournaments_upserted: int
    events_upserted: int
    odds_snapshots_inserted: int


@dataclass(frozen=True, slots=True)
class ESBHistoryInput:
    history: ESBTournamentHistory
    tournament_payload: ProviderPayload
    tournament_archive: ArchivedPayload
    matches_payload: ProviderPayload
    matches_archive: ArchivedPayload


@dataclass(frozen=True, slots=True)
class PersistedESBHistory:
    source_id: int
    raw_payloads_inserted: int
    tournaments_upserted: int
    events_upserted: int
    results_upserted: int


@dataclass(frozen=True, slots=True)
class UELHistoryInput:
    history: UELTournamentHistory
    data_payload: ProviderPayload
    data_archive: ArchivedPayload


@dataclass(frozen=True, slots=True)
class PersistedUELHistory:
    source_id: int
    raw_payloads_inserted: int
    tournaments_upserted: int
    events_upserted: int
    results_upserted: int


@dataclass(frozen=True, slots=True)
class PersistedSISH2HHistory:
    source_id: int
    raw_payload_id: int
    tournaments_upserted: int
    events_upserted: int
    results_upserted: int


def _source_normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = {"set-cookie", "cookie", "authorization", "proxy-authorization"}
    return {key: value for key, value in headers.items() if key.casefold() not in sensitive}


async def _persist_raw_payload(
    session: AsyncSession,
    *,
    source_id: int,
    payload: ProviderPayload,
    archive: ArchivedPayload,
) -> RawPayload:
    raw_payload = RawPayload(
        source_id=source_id,
        endpoint=payload.url,
        http_status=payload.status_code,
        content_type=payload.content_type,
        content_sha256=archive.content_sha256,
        storage_path=str(Path(archive.path)),
        response_headers=_safe_headers(payload.headers),
        received_at=payload.received_at,
    )
    session.add(raw_payload)
    await session.flush()
    return raw_payload


class FonbetCatalogRepository:
    """Persists one catalog snapshot; odds rows are intentionally insert-only."""

    async def persist(
        self,
        session: AsyncSession,
        *,
        payload: ProviderPayload,
        archive: ArchivedPayload,
        catalog: FonbetCatalog,
        latency_ms: int,
    ) -> PersistedCatalog:
        source = await session.scalar(select(Source).where(Source.code == "fonbet"))
        base_url = payload.url.split("/events/", maxsplit=1)[0]
        if source is None:
            source = Source(
                code="fonbet",
                name="Fonbet",
                source_type="bookmaker",
                base_url=base_url,
                enabled=True,
            )
            session.add(source)
            await session.flush()
        else:
            source.base_url = base_url

        raw_payload = await _persist_raw_payload(
            session,
            source_id=source.id,
            payload=payload,
            archive=archive,
        )

        tournament_ids = [item.external_id for item in catalog.tournaments]
        existing_tournaments = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Tournament).where(
                        Tournament.source_id == source.id,
                        Tournament.external_id.in_(tournament_ids),
                    )
                )
            ).all()
        }
        tournaments: dict[str, Tournament] = {}
        for tournament_dto in catalog.tournaments:
            tournament = existing_tournaments.get(tournament_dto.external_id)
            if tournament is None:
                tournament = Tournament(
                    source_id=source.id,
                    external_id=tournament_dto.external_id,
                    name=tournament_dto.name,
                    normalized_name=_source_normalized(tournament_dto.name),
                    sport=tournament_dto.sport,
                    game=tournament_dto.game,
                    format=tournament_dto.format,
                    family=tournament_dto.source_family,
                    country_code=tournament_dto.country_code,
                )
                session.add(tournament)
            else:
                tournament.name = tournament_dto.name
                tournament.normalized_name = _source_normalized(tournament_dto.name)
                tournament.sport = tournament_dto.sport
                tournament.game = tournament_dto.game
                tournament.format = tournament_dto.format
                tournament.family = tournament_dto.source_family
                tournament.country_code = tournament_dto.country_code
            tournaments[tournament_dto.external_id] = tournament
        await session.flush()

        event_external_ids = [item.external_id for item in catalog.events]
        existing_events = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Event).where(
                        Event.source_id == source.id,
                        Event.external_id.in_(event_external_ids),
                    )
                )
            ).all()
        }
        events: dict[str, Event] = {}
        for event_dto in catalog.events:
            tournament = tournaments[event_dto.tournament_external_id]
            event = existing_events.get(event_dto.external_id)
            if event is None:
                event = Event(
                    external_id=event_dto.external_id,
                    source_id=source.id,
                    sport=event_dto.sport,
                    game=event_dto.game,
                    tournament_id=tournament.id,
                    started_at=event_dto.started_at,
                    format=event_dto.format,
                    status=event_dto.status,
                    raw_payload_id=raw_payload.id,
                )
                session.add(event)
            else:
                event.sport = event_dto.sport
                event.game = event_dto.game
                event.tournament_id = tournament.id
                event.started_at = event_dto.started_at
                event.format = event_dto.format
                event.status = event_dto.status
                event.raw_payload_id = raw_payload.id
            events[event_dto.external_id] = event
        await session.flush()

        database_event_ids = [event.id for event in events.values()]
        participants = {
            (item.event_id, item.side): item
            for item in (
                await session.scalars(
                    select(EventParticipant).where(
                        EventParticipant.event_id.in_(database_event_ids)
                    )
                )
            ).all()
        }
        event_dtos = {item.external_id: item for item in catalog.events}
        for external_id, event in events.items():
            dto = event_dtos[external_id]
            for side, participant in ((1, dto.participant1), (2, dto.participant2)):
                database_participant = participants.get((event.id, side))
                if database_participant is None:
                    session.add(
                        EventParticipant(
                            event_id=event.id,
                            side=side,
                            raw_name=participant.raw_name,
                            raw_player_name=participant.player_name,
                            raw_team_name=participant.team_name,
                            external_player_key=participant.player_name,
                        )
                    )
                else:
                    database_participant.raw_name = participant.raw_name
                    database_participant.raw_player_name = participant.player_name
                    database_participant.raw_team_name = participant.team_name
                    database_participant.external_player_key = participant.player_name
        await session.flush()

        existing_markets = {
            (item.event_id, item.external_id): item
            for item in (
                await session.scalars(select(Market).where(Market.event_id.in_(database_event_ids)))
            ).all()
        }
        markets: dict[tuple[int, str], Market] = dict(existing_markets)
        for quote in catalog.quotes:
            event = events[quote.event_external_id]
            external_market_id = f"factor:{quote.factor_id}"
            market_key = (event.id, external_market_id)
            if market_key not in markets:
                market = Market(
                    event_id=event.id,
                    external_id=external_market_id,
                    code=quote.market_code,
                    name=quote.market_name,
                )
                session.add(market)
                markets[market_key] = market
            else:
                markets[market_key].code = quote.market_code
                markets[market_key].name = quote.market_name
        await session.flush()

        for quote in catalog.quotes:
            event = events[quote.event_external_id]
            market = markets[(event.id, f"factor:{quote.factor_id}")]
            session.add(
                OddsSnapshot(
                    event_id=event.id,
                    bookmaker_source_id=source.id,
                    market_id=market.id,
                    selection=quote.selection,
                    line=quote.line,
                    odds=quote.odds,
                    received_at=payload.received_at,
                    source_timestamp=None,
                    raw_payload_id=raw_payload.id,
                )
            )

        session.add(
            ParserRun(
                source_id=source.id,
                parser_name="fonbet.list_base.v1",
                status="success" if not catalog.rejection_reasons else "partial",
                started_at=payload.received_at,
                finished_at=payload.received_at,
                last_success_at=payload.received_at,
                last_error="; ".join(catalog.rejection_reasons[:20]) or None,
                events_received=catalog.received_event_count,
                events_parsed=len(catalog.events),
                events_rejected=catalog.rejected_event_count,
                latency_ms=latency_ms,
                raw_payload_id=raw_payload.id,
            )
        )
        await session.flush()

        return PersistedCatalog(
            source_id=source.id,
            raw_payload_id=raw_payload.id,
            tournaments_upserted=len(catalog.tournaments),
            events_upserted=len(catalog.events),
            odds_snapshots_inserted=len(catalog.quotes),
        )


class ESBHistoryRepository:
    """Upserts historical matches/results while retaining every source response."""

    async def persist(
        self,
        session: AsyncSession,
        *,
        discovery_payload: ProviderPayload,
        discovery_archive: ArchivedPayload,
        histories: Sequence[ESBHistoryInput],
        latency_ms: int,
    ) -> PersistedESBHistory:
        source = await session.scalar(select(Source).where(Source.code == "esb"))
        base_url = discovery_payload.url.split("/api/", maxsplit=1)[0]
        if source is None:
            source = Source(
                code="esb",
                name="ESportsBattle eFootball",
                source_type="history",
                base_url=base_url,
                enabled=True,
            )
            session.add(source)
            await session.flush()
        else:
            source.base_url = base_url

        discovery_raw = await _persist_raw_payload(
            session,
            source_id=source.id,
            payload=discovery_payload,
            archive=discovery_archive,
        )
        raw_by_tournament: dict[str, tuple[RawPayload, RawPayload]] = {}
        for item in histories:
            tournament_raw = await _persist_raw_payload(
                session,
                source_id=source.id,
                payload=item.tournament_payload,
                archive=item.tournament_archive,
            )
            matches_raw = await _persist_raw_payload(
                session,
                source_id=source.id,
                payload=item.matches_payload,
                archive=item.matches_archive,
            )
            raw_by_tournament[item.history.external_id] = (tournament_raw, matches_raw)

        history_by_id = {item.history.external_id: item.history for item in histories}
        tournament_external_ids = list(history_by_id)
        existing_tournaments = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Tournament).where(
                        Tournament.source_id == source.id,
                        Tournament.external_id.in_(tournament_external_ids),
                    )
                )
            ).all()
        }
        tournaments: dict[str, Tournament] = {}
        for external_id, history in history_by_id.items():
            tournament = existing_tournaments.get(external_id)
            if tournament is None:
                tournament = Tournament(
                    source_id=source.id,
                    external_id=external_id,
                    name=history.name,
                    normalized_name=_source_normalized(history.name),
                    sport=history.sport,
                    game=history.game,
                    format=None,
                    family="esb",
                    country_code=None,
                )
                session.add(tournament)
            else:
                tournament.name = history.name
                tournament.normalized_name = _source_normalized(history.name)
                tournament.sport = history.sport
                tournament.game = history.game
                tournament.family = "esb"
            tournaments[external_id] = tournament
        await session.flush()

        match_entries = [
            (history, match)
            for history in history_by_id.values()
            for match in history.matches
        ]
        event_external_ids = list({match.external_id for _, match in match_entries})
        existing_events = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Event).where(
                        Event.source_id == source.id,
                        Event.external_id.in_(event_external_ids),
                    )
                )
            ).all()
        }
        events: dict[str, Event] = {}
        for history, match in match_entries:
            matches_raw = raw_by_tournament[history.external_id][1]
            event = existing_events.get(match.external_id)
            if event is None:
                event = Event(
                    external_id=match.external_id,
                    source_id=source.id,
                    sport=history.sport,
                    game=history.game,
                    tournament_id=tournaments[history.external_id].id,
                    started_at=match.started_at,
                    format=None,
                    status=match.status,
                    raw_payload_id=matches_raw.id,
                )
                session.add(event)
            else:
                event.sport = history.sport
                event.game = history.game
                event.tournament_id = tournaments[history.external_id].id
                event.started_at = match.started_at
                event.status = match.status
                event.raw_payload_id = matches_raw.id
            events[match.external_id] = event
        await session.flush()

        database_event_ids = [event.id for event in events.values()]
        existing_participants = {
            (item.event_id, item.side): item
            for item in (
                await session.scalars(
                    select(EventParticipant).where(
                        EventParticipant.event_id.in_(database_event_ids)
                    )
                )
            ).all()
        }
        for _, match in match_entries:
            event = events[match.external_id]
            for side, participant in ((1, match.participant1), (2, match.participant2)):
                database_participant = existing_participants.get((event.id, side))
                participant_values = {
                    "raw_name": participant.raw_name,
                    "raw_player_name": participant.nickname,
                    "raw_team_name": participant.team.name if participant.team else None,
                    "external_player_key": participant.external_player_key,
                    "external_participant_id": participant.tournament_participant_external_id,
                    "external_team_id": (
                        participant.team.external_id if participant.team else None
                    ),
                }
                if database_participant is None:
                    session.add(
                        EventParticipant(event_id=event.id, side=side, **participant_values)
                    )
                else:
                    for key, value in participant_values.items():
                        setattr(database_participant, key, value)
        await session.flush()

        existing_results = {
            item.event_id: item
            for item in (
                await session.scalars(select(Result).where(Result.event_id.in_(database_event_ids)))
            ).all()
        }
        results_upserted = 0
        for history, match in match_entries:
            if not match.is_finished:
                continue
            assert match.score1 is not None
            assert match.score2 is not None
            event = events[match.external_id]
            if match.score1 > match.score2:
                winner = "P1"
            elif match.score2 > match.score1:
                winner = "P2"
            else:
                winner = "X"
            result_values: dict[str, Any] = {
                "score1": match.score1,
                "score2": match.score2,
                "winner": winner,
                "is_draw": match.score1 == match.score2,
                "total": match.score1 + match.score2,
                # Historical ESB payloads expose a score, but no settlement time.
                "settled_at": None,
                "raw_payload_id": raw_by_tournament[history.external_id][1].id,
            }
            result = existing_results.get(event.id)
            if result is None:
                session.add(
                    Result(
                        event_id=event.id,
                        observed_at=raw_by_tournament[history.external_id][1].received_at,
                        **result_values,
                    )
                )
            else:
                for key, value in result_values.items():
                    setattr(result, key, value)
                observed_at = raw_by_tournament[history.external_id][1].received_at
                if result.observed_at is None or _aware_utc(observed_at) < _aware_utc(
                    result.observed_at
                ):
                    result.observed_at = observed_at
            results_upserted += 1

        rejection_reasons = [
            reason for history in history_by_id.values() for reason in history.rejection_reasons
        ]
        events_received = sum(
            history.received_match_count for history in history_by_id.values()
        )
        finished_at = max(
            (
                payload.received_at
                for item in histories
                for payload in (item.tournament_payload, item.matches_payload)
            ),
            default=discovery_payload.received_at,
        )
        session.add(
            ParserRun(
                source_id=source.id,
                parser_name="esb.participant_history.v1",
                status="partial" if rejection_reasons else "success",
                started_at=discovery_payload.received_at,
                finished_at=finished_at,
                last_success_at=finished_at,
                last_error="; ".join(rejection_reasons[:20]) or None,
                events_received=events_received,
                events_parsed=len(event_external_ids),
                events_rejected=len(rejection_reasons),
                latency_ms=latency_ms,
                raw_payload_id=discovery_raw.id,
            )
        )
        await session.flush()

        return PersistedESBHistory(
            source_id=source.id,
            raw_payloads_inserted=1 + 2 * len(histories),
            tournaments_upserted=len(tournaments),
            events_upserted=len(event_external_ids),
            results_upserted=results_upserted,
        )


class UELHistoryRepository:
    """Persist official UEL tour history without conflating its source identities."""

    async def persist(
        self,
        session: AsyncSession,
        *,
        discovery_payload: ProviderPayload,
        discovery_archive: ArchivedPayload,
        histories: Sequence[UELHistoryInput],
        latency_ms: int,
    ) -> PersistedUELHistory:
        games = {item.history.game for item in histories}
        if len(games) > 1:
            raise ValueError("One UEL persistence batch cannot mix sports")
        game = next(iter(games), "efootball")
        source_code = "uel_ef" if game == "efootball" else "uel_eh"
        source_name = (
            "United Esports Leagues eFootball"
            if game == "efootball"
            else "United Esports Leagues eHockey"
        )
        source = await session.scalar(select(Source).where(Source.code == source_code))
        base_url = discovery_payload.url.split("/api/", maxsplit=1)[0]
        if source is None:
            source = Source(
                code=source_code,
                name=source_name,
                source_type="history",
                base_url=base_url,
                enabled=True,
            )
            session.add(source)
            await session.flush()
        else:
            source.base_url = base_url

        discovery_raw = await _persist_raw_payload(
            session,
            source_id=source.id,
            payload=discovery_payload,
            archive=discovery_archive,
        )
        raw_by_tournament: dict[str, RawPayload] = {}
        for item in histories:
            raw_by_tournament[item.history.external_id] = await _persist_raw_payload(
                session,
                source_id=source.id,
                payload=item.data_payload,
                archive=item.data_archive,
            )

        history_by_id = {item.history.external_id: item.history for item in histories}
        tournament_external_ids = list(history_by_id)
        existing_tournaments = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Tournament).where(
                        Tournament.source_id == source.id,
                        Tournament.external_id.in_(tournament_external_ids),
                    )
                )
            ).all()
        }
        tournaments: dict[str, Tournament] = {}
        for external_id, history in history_by_id.items():
            tournament = existing_tournaments.get(external_id)
            if tournament is None:
                tournament = Tournament(
                    source_id=source.id,
                    external_id=external_id,
                    name=history.name,
                    normalized_name=_source_normalized(history.name),
                    sport=history.sport,
                    game=history.game,
                    format=None,
                    family="uel",
                    country_code=history.country_code,
                )
                session.add(tournament)
            else:
                tournament.name = history.name
                tournament.normalized_name = _source_normalized(history.name)
                tournament.sport = history.sport
                tournament.game = history.game
                tournament.family = "uel"
                tournament.country_code = history.country_code
            tournaments[external_id] = tournament
        await session.flush()

        match_entries = [
            (history, match)
            for history in history_by_id.values()
            for match in history.matches
        ]
        event_external_ids = list({match.external_id for _, match in match_entries})
        existing_events = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Event).where(
                        Event.source_id == source.id,
                        Event.external_id.in_(event_external_ids),
                    )
                )
            ).all()
        }
        events: dict[str, Event] = {}
        for history, match in match_entries:
            raw_payload = raw_by_tournament[history.external_id]
            event = existing_events.get(match.external_id)
            if event is None:
                event = Event(
                    external_id=match.external_id,
                    source_id=source.id,
                    sport=history.sport,
                    game=history.game,
                    tournament_id=tournaments[history.external_id].id,
                    started_at=match.started_at,
                    format=None,
                    status=match.status,
                    raw_payload_id=raw_payload.id,
                )
                session.add(event)
            else:
                event.tournament_id = tournaments[history.external_id].id
                event.started_at = match.started_at
                event.status = match.status
                event.raw_payload_id = raw_payload.id
            events[match.external_id] = event
        await session.flush()

        database_event_ids = [event.id for event in events.values()]
        existing_participants = {
            (item.event_id, item.side): item
            for item in (
                await session.scalars(
                    select(EventParticipant).where(
                        EventParticipant.event_id.in_(database_event_ids)
                    )
                )
            ).all()
        }
        for _, match in match_entries:
            event = events[match.external_id]
            for side, participant in ((1, match.participant1), (2, match.participant2)):
                participant_values = {
                    "raw_name": participant.raw_name,
                    "raw_player_name": participant.nickname,
                    "raw_team_name": participant.team.name,
                    "raw_team_name_alt": participant.team.alternate_name,
                    "external_player_key": participant.external_player_key,
                    "external_participant_id": None,
                    "external_team_id": participant.team.external_id,
                }
                stored = existing_participants.get((event.id, side))
                if stored is None:
                    session.add(
                        EventParticipant(
                            event_id=event.id,
                            side=side,
                            **participant_values,
                        )
                    )
                else:
                    for key, value in participant_values.items():
                        setattr(stored, key, value)
        await session.flush()

        existing_results = {
            item.event_id: item
            for item in (
                await session.scalars(select(Result).where(Result.event_id.in_(database_event_ids)))
            ).all()
        }
        results_upserted = 0
        for history, match in match_entries:
            if not match.is_finished:
                continue
            assert match.score1 is not None
            assert match.score2 is not None
            event = events[match.external_id]
            if match.score1 > match.score2:
                winner = "P1"
            elif match.score2 > match.score1:
                winner = "P2"
            else:
                winner = "X"
            raw_payload = raw_by_tournament[history.external_id]
            result_values: dict[str, Any] = {
                "score1": match.score1,
                "score2": match.score2,
                "winner": winner,
                "is_draw": match.score1 == match.score2,
                "total": match.score1 + match.score2,
                "settled_at": None,
                "source_updated_at": match.source_updated_at,
                "raw_payload_id": raw_payload.id,
            }
            result = existing_results.get(event.id)
            if result is None:
                session.add(
                    Result(
                        event_id=event.id,
                        observed_at=raw_payload.received_at,
                        **result_values,
                    )
                )
            else:
                for key, value in result_values.items():
                    setattr(result, key, value)
                first_observation = result.observed_at
                if first_observation is None or _aware_utc(
                    raw_payload.received_at
                ) < _aware_utc(first_observation):
                    result.observed_at = raw_payload.received_at
            results_upserted += 1

        rejection_reasons = [
            reason for history in history_by_id.values() for reason in history.rejection_reasons
        ]
        events_received = sum(
            history.received_match_count for history in history_by_id.values()
        )
        finished_at = max(
            (item.data_payload.received_at for item in histories),
            default=discovery_payload.received_at,
        )
        session.add(
            ParserRun(
                source_id=source.id,
                parser_name=f"uel.{game}.tour_history.v1",
                status="partial" if rejection_reasons else "success",
                started_at=discovery_payload.received_at,
                finished_at=finished_at,
                last_success_at=finished_at,
                last_error="; ".join(rejection_reasons[:20]) or None,
                events_received=events_received,
                events_parsed=len(event_external_ids),
                events_rejected=len(rejection_reasons),
                latency_ms=latency_ms,
                raw_payload_id=discovery_raw.id,
            )
        )
        await session.flush()
        return PersistedUELHistory(
            source_id=source.id,
            raw_payloads_inserted=1 + len(histories),
            tournaments_upserted=len(tournaments),
            events_upserted=len(event_external_ids),
            results_upserted=results_upserted,
        )


class SISH2HHistoryRepository:
    """Persist official SIS H2H GGL schedules under an explicit source code."""

    async def persist(
        self,
        session: AsyncSession,
        *,
        payload: ProviderPayload,
        archive: ArchivedPayload,
        history: SISH2HDailyHistory,
        latency_ms: int,
    ) -> PersistedSISH2HHistory:
        source_code = f"sis_h2h_{history.game}"
        source = await session.scalar(select(Source).where(Source.code == source_code))
        base_url = payload.url.split("/v1/", maxsplit=1)[0]
        if source is None:
            source = Source(
                code=source_code,
                name=f"SIS H2H Global Gaming League {history.game}",
                source_type="history",
                base_url=base_url,
                enabled=True,
            )
            session.add(source)
            await session.flush()
        else:
            source.base_url = base_url

        raw_payload = await _persist_raw_payload(
            session,
            source_id=source.id,
            payload=payload,
            archive=archive,
        )

        tournament_names = sorted({match.tournament_name for match in history.matches})
        external_tournament_ids = {
            name: tournament_external_id(history.api_sport, name)
            for name in tournament_names
        }
        existing_tournaments = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Tournament).where(
                        Tournament.source_id == source.id,
                        Tournament.external_id.in_(external_tournament_ids.values()),
                    )
                )
            ).all()
        }
        tournaments: dict[str, Tournament] = {}
        for name, external_id in external_tournament_ids.items():
            tournament = existing_tournaments.get(external_id)
            if tournament is None:
                tournament = Tournament(
                    source_id=source.id,
                    external_id=external_id,
                    name=name,
                    normalized_name=_source_normalized(name),
                    sport=history.sport,
                    game=history.game,
                    format=None,
                    family="h2h_ggl",
                    country_code=None,
                )
                session.add(tournament)
            else:
                tournament.name = name
                tournament.normalized_name = _source_normalized(name)
                tournament.sport = history.sport
                tournament.game = history.game
                tournament.family = "h2h_ggl"
            tournaments[name] = tournament
        await session.flush()

        external_event_ids = list({match.external_id for match in history.matches})
        existing_events = {
            item.external_id: item
            for item in (
                await session.scalars(
                    select(Event).where(
                        Event.source_id == source.id,
                        Event.external_id.in_(external_event_ids),
                    )
                )
            ).all()
        }
        events: dict[str, Event] = {}
        for match in history.matches:
            event = existing_events.get(match.external_id)
            if event is None:
                event = Event(
                    external_id=match.external_id,
                    source_id=source.id,
                    sport=history.sport,
                    game=history.game,
                    tournament_id=tournaments[match.tournament_name].id,
                    started_at=match.started_at,
                    format=None,
                    status=match.status,
                    raw_payload_id=raw_payload.id,
                )
                session.add(event)
            else:
                event.tournament_id = tournaments[match.tournament_name].id
                event.started_at = match.started_at
                event.status = match.status
                event.raw_payload_id = raw_payload.id
            events[match.external_id] = event
        await session.flush()

        database_event_ids = [event.id for event in events.values()]
        existing_participants = {
            (item.event_id, item.side): item
            for item in (
                await session.scalars(
                    select(EventParticipant).where(
                        EventParticipant.event_id.in_(database_event_ids)
                    )
                )
            ).all()
        }
        for match in history.matches:
            event = events[match.external_id]
            for side, participant in ((1, match.participant1), (2, match.participant2)):
                values = {
                    "raw_name": participant.raw_name,
                    "raw_player_name": participant.nickname,
                    "raw_team_name": participant.team_name,
                    "raw_team_name_alt": None,
                    "external_player_key": participant.external_player_key,
                    "external_participant_id": None,
                    "external_team_id": participant.team_name,
                }
                stored = existing_participants.get((event.id, side))
                if stored is None:
                    session.add(EventParticipant(event_id=event.id, side=side, **values))
                else:
                    for key, value in values.items():
                        setattr(stored, key, value)
        await session.flush()

        existing_results = {
            item.event_id: item
            for item in (
                await session.scalars(select(Result).where(Result.event_id.in_(database_event_ids)))
            ).all()
        }
        results_upserted = 0
        for match in history.matches:
            if not match.is_finished:
                continue
            assert match.score1 is not None
            assert match.score2 is not None
            event = events[match.external_id]
            if match.score1 > match.score2:
                winner = "P1"
            elif match.score2 > match.score1:
                winner = "P2"
            else:
                winner = "X"
            result_values: dict[str, Any] = {
                "score1": match.score1,
                "score2": match.score2,
                "winner": winner,
                "is_draw": match.score1 == match.score2,
                "total": match.score1 + match.score2,
                "settled_at": None,
                "raw_payload_id": raw_payload.id,
            }
            result = existing_results.get(event.id)
            if result is None:
                session.add(
                    Result(
                        event_id=event.id,
                        observed_at=payload.received_at,
                        **result_values,
                    )
                )
            else:
                for key, value in result_values.items():
                    setattr(result, key, value)
                if result.observed_at is None or _aware_utc(payload.received_at) < _aware_utc(
                    result.observed_at
                ):
                    result.observed_at = payload.received_at
            results_upserted += 1

        session.add(
            ParserRun(
                source_id=source.id,
                parser_name=f"sis_h2h.{history.api_sport}.schedule.v1",
                status="partial" if history.rejection_reasons else "success",
                started_at=payload.received_at,
                finished_at=payload.received_at,
                last_success_at=payload.received_at,
                last_error="; ".join(history.rejection_reasons[:20]) or None,
                events_received=history.received_match_count,
                events_parsed=len(events),
                events_rejected=len(history.rejection_reasons),
                latency_ms=latency_ms,
                raw_payload_id=raw_payload.id,
            )
        )
        await session.flush()
        return PersistedSISH2HHistory(
            source_id=source.id,
            raw_payload_id=raw_payload.id,
            tournaments_upserted=len(tournaments),
            events_upserted=len(events),
            results_upserted=results_upserted,
        )
