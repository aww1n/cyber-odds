from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.database.models import (
    CorridorObservation,
    Event,
    EventMatch,
    EventParticipant,
    Market,
    ModelPrediction,
    OddsCorridor,
    OddsSnapshot,
    Player,
    Result,
    Settlement,
    Signal,
    Source,
    Team,
    Tournament,
)


@dataclass(frozen=True, slots=True)
class DatabaseAudit:
    counts: dict[str, int]
    problems: dict[str, int]

    @property
    def healthy(self) -> bool:
        return not any(self.problems.values())


async def audit_database(session: AsyncSession) -> DatabaseAudit:
    """Run read-only integrity checks not already guaranteed by constraints."""

    counts = {
        "sources": await _count(session, Source),
        "players": await _count(session, Player),
        "teams": await _count(session, Team),
        "tournaments": await _count(session, Tournament),
        "events": await _count(session, Event),
        "event_participants": await _count(session, EventParticipant),
        "results": await _count(session, Result),
        "markets": await _count(session, Market),
        "odds_snapshots": await _count(session, OddsSnapshot),
        "event_matches": await _count(session, EventMatch),
        "model_predictions": await _count(session, ModelPrediction),
        "signals": await _count(session, Signal),
        "settlements": await _count(session, Settlement),
        "corridor_observations": await _count(session, CorridorObservation),
        "odds_corridors": await _count(session, OddsCorridor),
    }
    source_event = aliased(Event)
    bookmaker_event = aliased(Event)
    historical_source = aliased(Source)
    bookmaker_source = aliased(Source)
    wrong_match_roles = int(
        await session.scalar(
            select(func.count(EventMatch.id))
            .join(source_event, source_event.id == EventMatch.source_event_id)
            .join(historical_source, historical_source.id == source_event.source_id)
            .join(bookmaker_event, bookmaker_event.id == EventMatch.bookmaker_event_id)
            .join(bookmaker_source, bookmaker_source.id == bookmaker_event.source_id)
            .where(
                (historical_source.source_type != "history")
                | (bookmaker_source.source_type != "bookmaker")
            )
        )
        or 0
    )
    result_event = aliased(Event)
    result_source = aliased(Source)
    results_on_non_history_source = int(
        await session.scalar(
            select(func.count(Result.id))
            .join(result_event, result_event.id == Result.event_id)
            .join(result_source, result_source.id == result_event.source_id)
            .where(result_source.source_type != "history")
        )
        or 0
    )
    problems = {
        "results_without_availability_timestamp": int(
            await session.scalar(
                select(func.count(Result.id)).where(
                    Result.settled_at.is_(None),
                    Result.source_updated_at.is_(None),
                    Result.observed_at.is_(None),
                )
            )
            or 0
        ),
        "matched_source_duplicates": await _duplicate_count(
            session,
            EventMatch.source_event_id,
            EventMatch.status == "matched",
        ),
        "matched_bookmaker_duplicates": await _duplicate_count(
            session,
            EventMatch.bookmaker_event_id,
            EventMatch.status == "matched",
        ),
        "duplicate_alert_keys": await _duplicate_count(
            session,
            Signal.alert_key,
            Signal.alert_key.is_not(None),
        ),
        "duplicate_events": await _duplicate_group_count(
            session,
            (Event.source_id, Event.external_id),
        ),
        "duplicate_event_match_pairs": await _duplicate_group_count(
            session,
            (EventMatch.source_event_id, EventMatch.bookmaker_event_id),
        ),
        "duplicate_predictions": await _duplicate_group_count(
            session,
            (
                ModelPrediction.odds_snapshot_id,
                ModelPrediction.model_name,
                ModelPrediction.model_version,
                ModelPrediction.selection,
            ),
        ),
        "duplicate_signals": await _duplicate_count(
            session,
            Signal.prediction_id,
            Signal.prediction_id.is_not(None),
        ),
        "duplicate_settlements": await _duplicate_count(
            session,
            Settlement.signal_id,
            Settlement.signal_id.is_not(None),
        ),
        "orphan_tournaments_source": await _orphan_count(
            session, Tournament, Tournament.source_id, Source, Source.id
        ),
        "orphan_events_source": await _orphan_count(
            session, Event, Event.source_id, Source, Source.id
        ),
        "orphan_events_tournament": await _orphan_count(
            session,
            Event,
            Event.tournament_id,
            Tournament,
            Tournament.id,
            nullable=True,
        ),
        "orphan_events_player1": await _orphan_count(
            session, Event, Event.player1_id, Player, Player.id, nullable=True
        ),
        "orphan_events_player2": await _orphan_count(
            session, Event, Event.player2_id, Player, Player.id, nullable=True
        ),
        "orphan_events_team1": await _orphan_count(
            session, Event, Event.team1_id, Team, Team.id, nullable=True
        ),
        "orphan_events_team2": await _orphan_count(
            session, Event, Event.team2_id, Team, Team.id, nullable=True
        ),
        "orphan_event_participants_event": await _orphan_count(
            session,
            EventParticipant,
            EventParticipant.event_id,
            Event,
            Event.id,
        ),
        "orphan_event_participants_player": await _orphan_count(
            session,
            EventParticipant,
            EventParticipant.player_id,
            Player,
            Player.id,
            nullable=True,
        ),
        "orphan_event_participants_team": await _orphan_count(
            session,
            EventParticipant,
            EventParticipant.team_id,
            Team,
            Team.id,
            nullable=True,
        ),
        "orphan_results_event": await _orphan_count(
            session, Result, Result.event_id, Event, Event.id
        ),
        "orphan_markets_event": await _orphan_count(
            session, Market, Market.event_id, Event, Event.id
        ),
        "orphan_odds_event": await _orphan_count(
            session, OddsSnapshot, OddsSnapshot.event_id, Event, Event.id
        ),
        "orphan_odds_market": await _orphan_count(
            session, OddsSnapshot, OddsSnapshot.market_id, Market, Market.id
        ),
        "orphan_odds_bookmaker_source": await _orphan_count(
            session,
            OddsSnapshot,
            OddsSnapshot.bookmaker_source_id,
            Source,
            Source.id,
        ),
        "orphan_event_matches_source": await _orphan_count(
            session,
            EventMatch,
            EventMatch.source_event_id,
            Event,
            Event.id,
        ),
        "orphan_event_matches_bookmaker": await _orphan_count(
            session,
            EventMatch,
            EventMatch.bookmaker_event_id,
            Event,
            Event.id,
        ),
        "orphan_predictions_event": await _orphan_count(
            session,
            ModelPrediction,
            ModelPrediction.event_id,
            Event,
            Event.id,
        ),
        "orphan_predictions_snapshot": await _orphan_count(
            session,
            ModelPrediction,
            ModelPrediction.odds_snapshot_id,
            OddsSnapshot,
            OddsSnapshot.id,
        ),
        "orphan_predictions_event_match": await _orphan_count(
            session,
            ModelPrediction,
            ModelPrediction.event_match_id,
            EventMatch,
            EventMatch.id,
            nullable=True,
        ),
        "orphan_signals_prediction": await _orphan_count(
            session,
            Signal,
            Signal.prediction_id,
            ModelPrediction,
            ModelPrediction.id,
        ),
        "orphan_settlements_signal": await _orphan_count(
            session, Settlement, Settlement.signal_id, Signal, Signal.id
        ),
        "orphan_settlements_result": await _orphan_count(
            session, Settlement, Settlement.result_id, Result, Result.id
        ),
        "orphan_corridors_source": await _orphan_count(
            session,
            OddsCorridor,
            OddsCorridor.bookmaker_source_id,
            Source,
            Source.id,
        ),
        "orphan_corridor_observations_match": await _orphan_count(
            session,
            CorridorObservation,
            CorridorObservation.event_match_id,
            EventMatch,
            EventMatch.id,
        ),
        "orphan_corridor_observations_snapshot": await _orphan_count(
            session,
            CorridorObservation,
            CorridorObservation.odds_snapshot_id,
            OddsSnapshot,
            OddsSnapshot.id,
        ),
        "orphan_corridor_observations_market": await _orphan_count(
            session,
            CorridorObservation,
            CorridorObservation.market_id,
            Market,
            Market.id,
        ),
        "orphan_corridor_observations_result": await _orphan_count(
            session,
            CorridorObservation,
            CorridorObservation.result_id,
            Result,
            Result.id,
        ),
        "orphan_corridor_observations_source": await _orphan_count(
            session,
            CorridorObservation,
            CorridorObservation.bookmaker_source_id,
            Source,
            Source.id,
        ),
        "prediction_cutoff_after_creation": int(
            await session.scalar(
                select(func.count(ModelPrediction.id)).where(
                    ModelPrediction.feature_cutoff_at > ModelPrediction.created_at
                )
            )
            or 0
        ),
        "signal_sent_before_creation": int(
            await session.scalar(
                select(func.count(Signal.id)).where(
                    Signal.sent_at.is_not(None),
                    Signal.sent_at < Signal.created_at,
                )
            )
            or 0
        ),
        "result_score_contract_mismatch": int(
            await session.scalar(
                select(func.count(Result.id)).where(
                    or_(
                        Result.total != Result.score1 + Result.score2,
                        (Result.score1 > Result.score2) & (Result.winner != "P1"),
                        (Result.score1 < Result.score2) & (Result.winner != "P2"),
                        (Result.score1 == Result.score2) & (Result.winner != "X"),
                        (Result.score1 == Result.score2) & Result.is_draw.is_(False),
                        (Result.score1 != Result.score2) & Result.is_draw.is_(True),
                    )
                )
            )
            or 0
        ),
        "results_on_non_history_source": results_on_non_history_source,
        "odds_market_event_mismatch": int(
            await session.scalar(
                select(func.count(OddsSnapshot.id))
                .join(Market, Market.id == OddsSnapshot.market_id)
                .where(Market.event_id != OddsSnapshot.event_id)
            )
            or 0
        ),
        "invalid_odds_market_contracts": int(
            await session.scalar(
                select(func.count(OddsSnapshot.id))
                .join(Market, Market.id == OddsSnapshot.market_id)
                .where(
                    or_(
                        (Market.code == "total")
                        & (
                            OddsSnapshot.line.is_(None)
                            | ~OddsSnapshot.selection.in_(("over", "under"))
                        ),
                        (Market.code == "1x2")
                        & ~OddsSnapshot.selection.in_(("P1", "X", "P2")),
                    )
                )
            )
            or 0
        ),
        "prediction_snapshot_event_mismatch": int(
            await session.scalar(
                select(func.count(ModelPrediction.id))
                .join(
                    OddsSnapshot,
                    OddsSnapshot.id == ModelPrediction.odds_snapshot_id,
                )
                .where(OddsSnapshot.event_id != ModelPrediction.event_id)
            )
            or 0
        ),
        "wrong_event_match_roles": wrong_match_roles,
    }
    return DatabaseAudit(counts=counts, problems=problems)


async def _count(session: AsyncSession, model: type[Any]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _duplicate_count(
    session: AsyncSession,
    column: InstrumentedAttribute[Any],
    condition: ColumnElement[bool],
) -> int:
    grouped = (
        select(column)
        .where(condition)
        .group_by(column)
        .having(func.count() > 1)
        .subquery()
    )
    return int(await session.scalar(select(func.count()).select_from(grouped)) or 0)


async def _duplicate_group_count(
    session: AsyncSession,
    columns: tuple[InstrumentedAttribute[Any], ...],
    condition: ColumnElement[bool] | None = None,
) -> int:
    query = select(*columns)
    if condition is not None:
        query = query.where(condition)
    grouped = query.group_by(*columns).having(func.count() > 1).subquery()
    return int(await session.scalar(select(func.count()).select_from(grouped)) or 0)


async def _orphan_count(
    session: AsyncSession,
    child: type[Any],
    foreign_column: InstrumentedAttribute[Any],
    parent: type[Any],
    parent_key: InstrumentedAttribute[Any],
    *,
    nullable: bool = False,
) -> int:
    statement = (
        select(func.count())
        .select_from(child)
        .outerjoin(parent, foreign_column == parent_key)
        .where(parent_key.is_(None))
    )
    if nullable:
        statement = statement.where(foreign_column.is_not(None))
    return int(await session.scalar(statement) or 0)
