from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Event, Result, Source, Tournament
from app.features.builder import FeatureBuilder, FeatureSet, MatchRecord, TargetContext


class DatabaseFeatureBuilder:
    """Load only results known available before the target cutoff."""

    async def build(
        self,
        session: AsyncSession,
        *,
        event_id: int,
        cutoff_at: datetime | None = None,
    ) -> FeatureSet:
        target_row = (
            await session.execute(
                select(Event, Source.code, Tournament.id)
                .join(Source, Source.id == Event.source_id)
                .outerjoin(Tournament, Tournament.id == Event.tournament_id)
                .where(Event.id == event_id)
            )
        ).one_or_none()
        if target_row is None:
            raise ValueError(f"Unknown event id: {event_id}")
        target_event, target_source, target_tournament_id = target_row
        if target_event.player1_id is None or target_event.player2_id is None:
            raise ValueError("Target event players must be normalized before features")
        cutoff = cutoff_at or target_event.started_at

        rows = (
            await session.execute(
                select(Event, Result, Source.code, Tournament.id)
                .join(Result, Result.event_id == Event.id)
                .join(Source, Source.id == Event.source_id)
                .outerjoin(Tournament, Tournament.id == Event.tournament_id)
                .where(
                    Event.started_at < cutoff,
                    Event.player1_id.is_not(None),
                    Event.player2_id.is_not(None),
                    or_(
                        Result.settled_at < cutoff,
                        Result.source_updated_at < cutoff,
                        Result.observed_at < cutoff,
                    ),
                    or_(
                        Event.player1_id.in_(
                            (target_event.player1_id, target_event.player2_id)
                        ),
                        Event.player2_id.in_(
                            (target_event.player1_id, target_event.player2_id)
                        ),
                    ),
                )
                .order_by(Event.started_at.desc(), Event.id.desc())
            )
        ).all()
        history: list[MatchRecord] = []
        for event, result, source_code, tournament_id in rows:
            assert event.player1_id is not None
            assert event.player2_id is not None
            available_values = [
                self._aware(value)
                for value in (
                    result.settled_at,
                    result.source_updated_at,
                    result.observed_at,
                )
                if value is not None
            ]
            if not available_values:
                continue
            history.append(
                MatchRecord(
                    event_id=event.id,
                    started_at=self._aware(event.started_at),
                    available_at=min(available_values),
                    source=source_code,
                    sport=event.sport,
                    game=event.game,
                    tournament_key=str(tournament_id) if tournament_id is not None else None,
                    format=event.format,
                    player1_key=str(event.player1_id),
                    player2_key=str(event.player2_id),
                    team1_key=str(event.team1_id) if event.team1_id is not None else None,
                    team2_key=str(event.team2_id) if event.team2_id is not None else None,
                    score1=result.score1,
                    score2=result.score2,
                )
            )
        target = TargetContext(
            event_id=target_event.id,
            cutoff_at=self._aware(cutoff),
            source=target_source,
            sport=target_event.sport,
            game=target_event.game,
            tournament_key=(
                str(target_tournament_id) if target_tournament_id is not None else None
            ),
            format=target_event.format,
            player1_key=str(target_event.player1_id),
            player2_key=str(target_event.player2_id),
            team1_key=(str(target_event.team1_id) if target_event.team1_id is not None else None),
            team2_key=(str(target_event.team2_id) if target_event.team2_id is not None else None),
        )
        return FeatureBuilder(history).build(target)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
