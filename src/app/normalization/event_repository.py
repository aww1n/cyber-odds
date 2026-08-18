from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Event, EventMatch, Source, Tournament
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


class EventMatchingRepository:
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
        now: datetime | None = None,
    ) -> EventMatchingStats:
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

        current = self._aware_utc(now or datetime.now(UTC))
        bookmaker_query = (
            select(Event, Tournament.name, Tournament.family)
            .outerjoin(Tournament, Tournament.id == Event.tournament_id)
            .where(Event.source_id == sources[bookmaker_source_code].id)
        )
        if live_only:
            bookmaker_query = bookmaker_query.where(
                Event.status == "scheduled",
                Event.started_at > current,
            )
        bookmaker_rows = (
            await session.execute(bookmaker_query.order_by(Event.started_at, Event.id))
        ).all()
        if not bookmaker_rows:
            return EventMatchingStats(0, 0, 0, 0, 0, 0)

        earliest = min(row[0].started_at for row in bookmaker_rows) - candidate_window
        latest = max(row[0].started_at for row in bookmaker_rows) + candidate_window
        historical_rows = (
            await session.execute(
                select(Event, Tournament.name, Tournament.family)
                .outerjoin(Tournament, Tournament.id == Event.tournament_id)
                .where(
                    Event.source_id == sources[historical_source_code].id,
                    Event.started_at >= earliest,
                    Event.started_at <= latest,
                )
                .order_by(Event.started_at, Event.id)
            )
        ).all()
        if not historical_rows:
            return EventMatchingStats(0, len(bookmaker_rows), 0, 0, 0, 0)

        bookmaker_matchables = [
            self._matchable(event, tournament_name or "", tournament_family)
            for event, tournament_name, tournament_family in bookmaker_rows
            if event.player1_id is not None and event.player2_id is not None
        ]
        decisions: list[tuple[Event, EventMatchDecision]] = []
        unresolved = 0
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
            decision = choose_event_match(
                source_matchable,
                nearby,
                automatic_threshold=automatic_threshold,
                exact_threshold=exact_threshold,
                ambiguity_margin=ambiguity_margin,
            )
            decisions.append((event, decision))

        automatic_candidate_counts: dict[int, int] = {}
        for _, decision in decisions:
            if decision.automatic and decision.best is not None:
                event_id = decision.best.candidate_event_id
                automatic_candidate_counts[event_id] = (
                    automatic_candidate_counts.get(event_id, 0) + 1
                )

        source_event_ids = [event.id for event, _ in decisions]
        existing = list(
            (
                await session.scalars(
                    select(EventMatch).where(EventMatch.source_event_id.in_(source_event_ids))
                )
            ).all()
        )
        existing_by_pair = {
            (item.source_event_id, item.bookmaker_event_id): item for item in existing
        }
        existing_by_source: dict[int, list[EventMatch]] = {}
        for item in existing:
            existing_by_source.setdefault(item.source_event_id, []).append(item)

        automatic = 0
        ambiguous = 0
        rejected = 0
        now = datetime.now(UTC)
        for source_event, decision in decisions:
            if decision.best is None:
                for old_match in existing_by_source.get(source_event.id, []):
                    old_match.status = "rejected"
                rejected += 1
                continue
            collision = automatic_candidate_counts.get(decision.best.candidate_event_id, 0) > 1
            database_status = "matched" if decision.automatic and not collision else "ambiguous"
            if database_status == "matched":
                automatic += 1
            else:
                ambiguous += 1

            for old_match in existing_by_source.get(source_event.id, []):
                if old_match.bookmaker_event_id != decision.best.candidate_event_id:
                    old_match.status = "rejected"

            pair = (source_event.id, decision.best.candidate_event_id)
            event_match = existing_by_pair.get(pair)
            components: dict[str, Any] = dict(decision.best.components)
            components.update(
                {
                    "quality": decision.status,
                    "second_best_confidence": decision.second_best_confidence,
                    "bookmaker_collision": collision,
                }
            )
            if event_match is None:
                event_match = EventMatch(
                    source_event_id=source_event.id,
                    bookmaker_event_id=decision.best.candidate_event_id,
                    confidence=Decimal(str(round(decision.best.confidence, 6))),
                    status=database_status,
                    components=components,
                    matched_at=now,
                )
                session.add(event_match)
            else:
                event_match.confidence = Decimal(
                    str(round(decision.best.confidence, 6))
                )
                event_match.status = database_status
                event_match.components = components
                event_match.matched_at = now
        await session.flush()
        return EventMatchingStats(
            source_events_seen=len(historical_rows),
            candidates_seen=len(bookmaker_rows),
            automatic_matches=automatic,
            ambiguous_matches=ambiguous,
            rejected_without_candidate=rejected,
            unresolved_source_events=unresolved,
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
