from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Event, EventMatch, OddsSnapshot, Result, Source, Tournament
from app.normalization.events import (
    EventMatchDecision,
    MatchableEvent,
    choose_event_match,
)


@dataclass(frozen=True, slots=True)
class EventMatchingStats:
    source_events_seen: int
    candidates_seen: int
    automatic_matches: int
    ambiguous_matches: int
    rejected_without_candidate: int
    unresolved_source_events: int
    preserved_matches: int = 0


class EventMatchingRepository:
    """Create one stable historical-source → bookmaker mapping per event.

    A matched pair becomes immutable for automatic jobs. The live matcher only
    considers future source events, so a rolling candidate window cannot turn a
    real completed pair into ``rejected`` after kickoff. Completed historical
    rows are repaired explicitly through ``completed_only=True``.
    """

    async def match_sources(
        self,
        session: AsyncSession,
        *,
        historical_source_code: str,
        bookmaker_source_code: str,
        automatic_threshold: float = 0.90,
        exact_threshold: float = 0.985,
        ambiguity_margin: float = 0.03,
        candidate_window: timedelta = timedelta(minutes=30),
        live_only: bool = False,
        completed_only: bool = False,
        now: datetime | None = None,
    ) -> EventMatchingStats:
        if live_only and completed_only:
            raise ValueError("live_only and completed_only are mutually exclusive")

        sources = {
            item.code: item
            for item in (
                await session.scalars(
                    select(Source).where(
                        Source.code.in_((historical_source_code, bookmaker_source_code))
                    )
                )
            ).all()
        }
        if historical_source_code not in sources:
            raise ValueError(f"Unknown historical source: {historical_source_code}")
        if bookmaker_source_code not in sources:
            raise ValueError(f"Unknown bookmaker source: {bookmaker_source_code}")
        historical_source = sources[historical_source_code]
        bookmaker_source = sources[bookmaker_source_code]
        if historical_source.source_type != "history":
            raise ValueError(f"Source is not historical: {historical_source_code}")
        if bookmaker_source.source_type != "bookmaker":
            raise ValueError(f"Source is not a bookmaker: {bookmaker_source_code}")

        current = self._aware_utc(now or datetime.now(UTC))
        bookmaker_query = (
            select(Event, Tournament.name, Tournament.family)
            .outerjoin(Tournament, Tournament.id == Event.tournament_id)
            .where(Event.source_id == bookmaker_source.id)
        )
        if live_only:
            bookmaker_query = bookmaker_query.where(
                Event.status == "scheduled",
                Event.started_at > current,
            )
        elif completed_only:
            bookmaker_query = bookmaker_query.where(
                Event.started_at <= current,
                select(OddsSnapshot.id)
                .where(
                    OddsSnapshot.event_id == Event.id,
                    OddsSnapshot.received_at < Event.started_at,
                )
                .exists(),
            )
        bookmaker_rows = (
            await session.execute(bookmaker_query.order_by(Event.started_at, Event.id))
        ).all()
        if not bookmaker_rows:
            return EventMatchingStats(0, 0, 0, 0, 0, 0)

        earliest = min(row[0].started_at for row in bookmaker_rows) - candidate_window
        latest = max(row[0].started_at for row in bookmaker_rows) + candidate_window
        historical_query = (
            select(Event, Tournament.name, Tournament.family)
            .outerjoin(Tournament, Tournament.id == Event.tournament_id)
            .where(
                Event.source_id == historical_source.id,
                Event.started_at >= earliest,
                Event.started_at <= latest,
            )
        )
        if live_only:
            # Do not reconsider a source event once it can have started. A
            # rolling ±30-minute candidate window previously demoted valid
            # earlier matches when neighbouring events entered the window.
            historical_query = historical_query.where(Event.started_at > current)
        elif completed_only:
            historical_query = historical_query.join(Result, Result.event_id == Event.id).where(
                Event.started_at <= current
            )
        historical_rows = (
            await session.execute(historical_query.order_by(Event.started_at, Event.id))
        ).all()
        if not historical_rows:
            rejected = 0
            if not live_only and not completed_only:
                bookmaker_ids = [event.id for event, _, _ in bookmaker_rows]
                stale_matches = (
                    await session.scalars(
                        select(EventMatch).where(
                            EventMatch.bookmaker_event_id.in_(bookmaker_ids),
                            EventMatch.status == "matched",
                        )
                    )
                ).all()
                for stale_match in stale_matches:
                    stale_match.status = "rejected"
                rejected = len(stale_matches)
                await session.flush()
            return EventMatchingStats(
                0,
                len(bookmaker_rows),
                0,
                0,
                rejected,
                0,
            )

        bookmaker_matchables = [
            self._matchable(event, tournament_name or "", tournament_family)
            for event, tournament_name, tournament_family in bookmaker_rows
            if event.player1_id is not None and event.player2_id is not None
        ]
        source_event_ids = [event.id for event, _, _ in historical_rows]
        bookmaker_event_ids = [item.event_id for item in bookmaker_matchables]
        existing_conditions = [EventMatch.source_event_id.in_(source_event_ids)]
        if bookmaker_event_ids:
            existing_conditions.append(EventMatch.bookmaker_event_id.in_(bookmaker_event_ids))
        existing = list(
            (
                await session.scalars(select(EventMatch).where(or_(*existing_conditions)))
            ).all()
        )
        existing_by_pair = {
            (item.source_event_id, item.bookmaker_event_id): item for item in existing
        }
        existing_by_source: dict[int, list[EventMatch]] = {}
        matched_by_source: dict[int, EventMatch] = {}
        matched_by_bookmaker: dict[int, EventMatch] = {}
        for item in existing:
            existing_by_source.setdefault(item.source_event_id, []).append(item)
            if item.status == "matched":
                matched_by_source[item.source_event_id] = item
                matched_by_bookmaker[item.bookmaker_event_id] = item

        decisions: list[tuple[Event, EventMatchDecision]] = []
        unresolved = 0
        preserved = 0
        for event, tournament_name, tournament_family in historical_rows:
            if event.player1_id is None or event.player2_id is None:
                unresolved += 1
                continue
            source_matchable = self._matchable(
                event,
                tournament_name or "",
                tournament_family,
            )
            nearby = [
                candidate
                for candidate in bookmaker_matchables
                if abs(
                    (
                        self._aware_utc(candidate.started_at)
                        - self._aware_utc(source_matchable.started_at)
                    ).total_seconds()
                )
                <= candidate_window.total_seconds()
            ]
            decisions.append(
                (
                    event,
                    choose_event_match(
                        source_matchable,
                        nearby,
                        automatic_threshold=automatic_threshold,
                        exact_threshold=exact_threshold,
                        ambiguity_margin=ambiguity_margin,
                    ),
                )
            )

        automatic_candidate_counts: dict[int, int] = {}
        for _, decision in decisions:
            if decision.automatic and decision.best is not None:
                candidate_event_id = decision.best.candidate_event_id
                automatic_candidate_counts[candidate_event_id] = (
                    automatic_candidate_counts.get(candidate_event_id, 0) + 1
                )

        automatic = 0
        ambiguous = 0
        rejected = 0
        for source_event, decision in decisions:
            existing_for_source = existing_by_source.get(source_event.id, [])
            if decision.best is None:
                for old_match in existing_for_source:
                    old_match.status = "rejected"
                rejected += 1
                continue

            candidate_event_id = decision.best.candidate_event_id
            collision = automatic_candidate_counts.get(candidate_event_id, 0) > 1
            active_bookmaker_match = matched_by_bookmaker.get(candidate_event_id)
            candidate_already_matched = (
                active_bookmaker_match is not None
                and active_bookmaker_match.source_event_id != source_event.id
            )
            database_status = (
                "matched"
                if decision.automatic and not collision and not candidate_already_matched
                else "ambiguous"
            )
            if database_status == "matched":
                automatic += 1
            else:
                ambiguous += 1

            for old_match in existing_for_source:
                if old_match.bookmaker_event_id != candidate_event_id:
                    old_match.status = "rejected"

            pair = (source_event.id, candidate_event_id)
            event_match = existing_by_pair.get(pair)
            components: dict[str, Any] = dict(decision.best.components)
            components.update(
                {
                    "quality": decision.status,
                    "second_best_confidence": decision.second_best_confidence,
                    "bookmaker_collision": collision,
                    "bookmaker_already_matched": candidate_already_matched,
                }
            )
            if event_match is None:
                event_match = EventMatch(
                    source_event_id=source_event.id,
                    bookmaker_event_id=candidate_event_id,
                    confidence=Decimal(str(round(decision.best.confidence, 6))),
                    status=database_status,
                    components=components,
                    matched_at=current,
                )
                session.add(event_match)
                existing_by_pair[pair] = event_match
                existing_by_source.setdefault(source_event.id, []).append(event_match)
            else:
                event_match.confidence = Decimal(str(round(decision.best.confidence, 6)))
                event_match.status = database_status
                event_match.components = components
                event_match.matched_at = current
            if database_status == "matched":
                matched_by_source[source_event.id] = event_match
                matched_by_bookmaker[candidate_event_id] = event_match

        await session.flush()
        return EventMatchingStats(
            source_events_seen=len(historical_rows),
            candidates_seen=len(bookmaker_rows),
            automatic_matches=automatic,
            ambiguous_matches=ambiguous,
            rejected_without_candidate=rejected,
            unresolved_source_events=unresolved,
            preserved_matches=preserved,
        )

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _matchable(
        event: Event,
        tournament_name: str,
        tournament_family: str | None,
    ) -> MatchableEvent:
        return MatchableEvent(
            event_id=event.id,
            started_at=event.started_at,
            tournament_name=tournament_name,
            player1_key=str(event.player1_id) if event.player1_id is not None else None,
            player2_key=str(event.player2_id) if event.player2_id is not None else None,
            team1_key=str(event.team1_id) if event.team1_id is not None else None,
            team2_key=str(event.team2_id) if event.team2_id is not None else None,
            tournament_family=tournament_family,
        )
