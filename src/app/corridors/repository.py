from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.backtest.settlement import normalize_market_selection, settle_market
from app.corridors.calculator import (
    CorridorMetrics,
    bucket_bounds,
    calculate_metrics_from_totals,
)
from app.database.models import (
    CorridorObservation,
    Event,
    EventMatch,
    Market,
    OddsCorridor,
    OddsSnapshot,
    Result,
    Source,
    Tournament,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CorridorBuildResult:
    bookmaker: str
    as_of: datetime
    bucket_width: Decimal
    observations: int
    observations_materialized: int
    observations_created: int
    observations_updated: int
    observations_invalidated: int
    observations_returned: int
    buckets_written: int
    global_buckets: int
    tournament_buckets: int


@dataclass(frozen=True, slots=True)
class CorridorSnapshot:
    scope_type: str
    scope_value: str
    odds_min: Decimal
    odds_max: Decimal
    sample_size: int
    wins: int
    losses: int
    returns: int
    win_rate: Decimal
    average_odds: Decimal
    roi_percent: Decimal
    edge_percent: Decimal
    confidence: Decimal
    as_of: datetime

    @property
    def verdict(self) -> str:
        if self.sample_size < 20:
            return "insufficient"
        if self.roi_percent > 0 and self.edge_percent > 0:
            return "confirm"
        if self.roi_percent < 0 and self.edge_percent < 0:
            return "conflict"
        return "neutral"


@dataclass(frozen=True, slots=True)
class _MaterializationResult:
    materialized: int
    created: int
    updated: int
    invalidated: int


@dataclass(slots=True)
class _Aggregate:
    sample_size: int = 0
    wins: int = 0
    returns: int = 0
    odds_total: Decimal = Decimal("0")
    implied_probability_total: Decimal = Decimal("0")
    profit_total: Decimal = Decimal("0")

    def add(self, *, odds: Decimal, outcome: str) -> None:
        if outcome not in {"win", "loss", "return"}:
            raise ValueError(f"Unsupported corridor outcome: {outcome}")
        self.sample_size += 1
        self.odds_total += odds
        self.implied_probability_total += Decimal("1") / odds
        if outcome == "win":
            self.wins += 1
            self.profit_total += odds - Decimal("1")
        elif outcome == "loss":
            self.profit_total -= Decimal("1")
        else:
            self.returns += 1

    def metrics(self) -> CorridorMetrics:
        return calculate_metrics_from_totals(
            sample_size=self.sample_size,
            wins=self.wins,
            odds_total=self.odds_total,
            implied_probability_total=self.implied_probability_total,
            profit_total=self.profit_total,
            returns=self.returns,
        )


class CorridorRepository:
    """Materialize closing-line observations and aggregate leakage-safe corridors.

    An observation is exactly one latest pre-kickoff quote for a matched source
    result, market and selection. Rebuilds are idempotent: they update that
    closing quote in place, retire invalidated derived rows, and never delete
    raw odds, matches, results, predictions or historical aggregates.
    """

    _LOCK_ID = 6_381_919_042

    async def rebuild(
        self,
        session: AsyncSession,
        *,
        bookmaker_code: str = "fonbet",
        as_of: datetime | None = None,
        bucket_width: Decimal = Decimal("0.25"),
        min_samples: int = 20,
        max_odds_age: timedelta = timedelta(minutes=15),
    ) -> CorridorBuildResult:
        if bucket_width <= 0:
            raise ValueError("bucket_width must be positive")
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        if max_odds_age <= timedelta(0):
            raise ValueError("max_odds_age must be positive")

        await self._acquire_rebuild_lock(session)
        current = self._aware(as_of or datetime.now(UTC))
        bookmaker_source_id = await session.scalar(
            select(Source.id).where(
                Source.code == bookmaker_code,
                Source.source_type == "bookmaker",
            )
        )
        if bookmaker_source_id is None:
            raise ValueError(f"Unknown bookmaker source: {bookmaker_code}")

        latest_as_of = await session.scalar(
            select(func.max(OddsCorridor.as_of)).where(
                OddsCorridor.bookmaker_source_id == bookmaker_source_id,
                OddsCorridor.is_active.is_(True),
            )
        )
        if (
            as_of is not None
            and latest_as_of is not None
            and current < self._aware(latest_as_of)
        ):
            raise ValueError(
                "as_of cannot move a persisted corridor backwards; use a separate database "
                "for historical experiments"
            )

        materialization = await self._materialize_observations(
            session,
            bookmaker_source_id=bookmaker_source_id,
            as_of=current,
            max_odds_age=max_odds_age,
        )
        (
            observations,
            returned,
            buckets_written,
            global_buckets,
            tournament_buckets,
        ) = await self._rebuild_aggregates(
            session,
            bookmaker_source_id=bookmaker_source_id,
            as_of=current,
            bucket_width=bucket_width,
            min_samples=min_samples,
        )
        return CorridorBuildResult(
            bookmaker=bookmaker_code,
            as_of=current,
            bucket_width=bucket_width,
            observations=observations,
            observations_materialized=materialization.materialized,
            observations_created=materialization.created,
            observations_updated=materialization.updated,
            observations_invalidated=materialization.invalidated,
            observations_returned=returned,
            buckets_written=buckets_written,
            global_buckets=global_buckets,
            tournament_buckets=tournament_buckets,
        )

    async def lookup(
        self,
        session: AsyncSession,
        *,
        bookmaker_source_id: int,
        sport: str,
        game: str | None,
        tournament_family: str | None,
        market_code: str,
        selection: str,
        line: Decimal | None,
        odds: Decimal,
        cutoff_at: datetime,
        min_samples: int = 20,
    ) -> CorridorSnapshot | None:
        if odds <= 1:
            raise ValueError("odds must be greater than 1")
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        cutoff = self._aware(cutoff_at)
        line_clause = (
            OddsCorridor.line.is_(None)
            if line is None
            else OddsCorridor.line == line
        )
        rows = (
            await session.scalars(
                select(OddsCorridor)
                .where(
                    OddsCorridor.is_active.is_(True),
                    OddsCorridor.bookmaker_source_id == bookmaker_source_id,
                    OddsCorridor.sport == sport,
                    OddsCorridor.game_key == (game or ""),
                    OddsCorridor.market_code == market_code,
                    OddsCorridor.selection == selection,
                    line_clause,
                    OddsCorridor.odds_min <= odds,
                    OddsCorridor.odds_max > odds,
                    OddsCorridor.as_of <= cutoff,
                    OddsCorridor.sample_size >= min_samples,
                    (
                        (OddsCorridor.scope_type == "global")
                        |
                        (
                            (OddsCorridor.scope_type == "tournament_family")
                            & (OddsCorridor.scope_value == (tournament_family or ""))
                        )
                    ),
                )
                .order_by(
                    (OddsCorridor.scope_type == "tournament_family").desc(),
                    OddsCorridor.sample_size.desc(),
                )
            )
        ).all()
        if not rows:
            return None
        row = rows[0]
        return CorridorSnapshot(
            scope_type=row.scope_type,
            scope_value=row.scope_value,
            odds_min=row.odds_min,
            odds_max=row.odds_max,
            sample_size=row.sample_size,
            wins=row.wins,
            losses=row.losses,
            returns=row.returns,
            win_rate=row.win_rate,
            average_odds=row.average_odds,
            roi_percent=row.roi_percent,
            edge_percent=row.edge_percent,
            confidence=row.confidence,
            as_of=self._aware(row.as_of),
        )

    async def top_corridors(
        self,
        session: AsyncSession,
        *,
        bookmaker_code: str = "fonbet",
        min_samples: int = 20,
        limit: int = 30,
    ) -> tuple[OddsCorridor, ...]:
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = (
            await session.scalars(
                select(OddsCorridor)
                .join(Source, Source.id == OddsCorridor.bookmaker_source_id)
                .where(
                    Source.code == bookmaker_code,
                    OddsCorridor.is_active.is_(True),
                    OddsCorridor.sample_size >= min_samples,
                )
                .order_by(
                    OddsCorridor.roi_percent.desc(),
                    OddsCorridor.sample_size.desc(),
                    OddsCorridor.id,
                )
                .limit(limit)
            )
        ).all()
        return tuple(rows)

    async def _materialize_observations(
        self,
        session: AsyncSession,
        *,
        bookmaker_source_id: int,
        as_of: datetime,
        max_odds_age: timedelta,
    ) -> _MaterializationResult:
        active_match_ids = select(EventMatch.id).where(EventMatch.status == "matched")
        invalidated = await session.execute(
            update(CorridorObservation)
            .where(
                CorridorObservation.bookmaker_source_id == bookmaker_source_id,
                CorridorObservation.is_active.is_(True),
                ~CorridorObservation.event_match_id.in_(active_match_ids),
            )
            .values(is_active=False, invalidated_at=as_of, updated_at=as_of)
        )

        source_event = aliased(Event)
        bookmaker_event = aliased(Event)
        historical_source = aliased(Source)
        bookmaker_source = aliased(Source)
        bookmaker_tournament = aliased(Tournament)
        result_available_at = func.coalesce(
            Result.observed_at,
            Result.source_updated_at,
            Result.settled_at,
        )
        snapshot_rank = func.row_number().over(
            partition_by=(
                EventMatch.id,
                Market.id,
                OddsSnapshot.selection,
                OddsSnapshot.line,
            ),
            order_by=(OddsSnapshot.received_at.desc(), OddsSnapshot.id.desc()),
        ).label("snapshot_rank")
        ranked = (
            select(
                EventMatch.id.label("event_match_id"),
                OddsSnapshot.id.label("odds_snapshot_id"),
                Market.id.label("market_id"),
                Result.id.label("result_id"),
                OddsSnapshot.bookmaker_source_id.label("bookmaker_source_id"),
                bookmaker_event.sport.label("sport"),
                bookmaker_event.game.label("game_key"),
                bookmaker_tournament.family.label("tournament_family"),
                Market.code.label("market_code"),
                OddsSnapshot.selection.label("selection"),
                OddsSnapshot.line.label("line"),
                OddsSnapshot.odds.label("odds"),
                OddsSnapshot.received_at.label("odds_received_at"),
                bookmaker_event.started_at.label("bookmaker_started_at"),
                Result.score1.label("score1"),
                Result.score2.label("score2"),
                EventMatch.components.label("components"),
                result_available_at.label("result_available_at"),
                snapshot_rank,
            )
            .select_from(EventMatch)
            .join(source_event, source_event.id == EventMatch.source_event_id)
            .join(bookmaker_event, bookmaker_event.id == EventMatch.bookmaker_event_id)
            .join(historical_source, historical_source.id == source_event.source_id)
            .join(Result, Result.event_id == source_event.id)
            .join(OddsSnapshot, OddsSnapshot.event_id == bookmaker_event.id)
            .join(Market, Market.id == OddsSnapshot.market_id)
            .join(bookmaker_source, bookmaker_source.id == OddsSnapshot.bookmaker_source_id)
            .outerjoin(
                bookmaker_tournament,
                bookmaker_tournament.id == bookmaker_event.tournament_id,
            )
            .where(
                EventMatch.status == "matched",
                historical_source.source_type == "history",
                bookmaker_source.source_type == "bookmaker",
                bookmaker_event.source_id == bookmaker_source.id,
                OddsSnapshot.bookmaker_source_id == bookmaker_source_id,
                OddsSnapshot.received_at < bookmaker_event.started_at,
                bookmaker_event.started_at <= as_of,
                Market.code.in_(("1x2", "total")),
                result_available_at.is_not(None),
                result_available_at <= as_of,
            )
            .subquery()
        )
        ranked_candidates = (
            await session.execute(select(ranked).where(ranked.c.snapshot_rank == 1))
        ).mappings().all()
        candidates = [
            row
            for row in ranked_candidates
            if self._aware(row["bookmaker_started_at"])
            - self._aware(row["odds_received_at"])
            <= max_odds_age
        ]
        candidates.sort(key=lambda row: self._aware(row["odds_received_at"]))
        if not candidates:
            return _MaterializationResult(
                materialized=0,
                created=0,
                updated=0,
                invalidated=max(0, getattr(invalidated, "rowcount", 0) or 0),
            )

        event_match_ids = {int(row["event_match_id"]) for row in candidates}
        existing = {
            (item.event_match_id, item.market_id, item.selection, item.line): item
            for item in (
                await session.scalars(
                    select(CorridorObservation).where(
                        CorridorObservation.event_match_id.in_(event_match_ids)
                    )
                )
            ).all()
        }
        created = updated = processed = 0
        for row in candidates:
            components: Any = row["components"] or {}
            reversed_sides = bool(components.get("reversed_sides"))
            score1 = int(row["score1"])
            score2 = int(row["score2"])
            if reversed_sides:
                score1, score2 = score2, score1
            market_code = str(row["market_code"])
            line_value = self._decimal_or_none(row["line"])
            try:
                selection = normalize_market_selection(
                    str(row["selection"]),
                    market=market_code,
                    line=line_value,
                )
                outcome = settle_market(
                    selection,
                    score1=score1,
                    score2=score2,
                    market=market_code,
                    line=line_value,
                )
            except ValueError as error:
                LOGGER.warning(
                    "Skipping invalid corridor market contract snapshot_id=%s: %s",
                    row["odds_snapshot_id"],
                    error,
                )
                continue
            processed += 1
            odds = Decimal(str(row["odds"]))
            key = (
                int(row["event_match_id"]),
                int(row["market_id"]),
                selection,
                line_value,
            )
            values: dict[str, object] = {
                "odds_snapshot_id": int(row["odds_snapshot_id"]),
                "result_id": int(row["result_id"]),
                "bookmaker_source_id": int(row["bookmaker_source_id"]),
                "sport": str(row["sport"]),
                "game_key": str(row["game_key"] or ""),
                "tournament_family": str(row["tournament_family"] or ""),
                "market_code": str(row["market_code"]),
                "selection": selection,
                "line": line_value,
                "odds": odds,
                "implied_probability": Decimal("1") / odds,
                "outcome": outcome,
                "mapping_reversed_sides": reversed_sides,
                "result_available_at": self._aware(row["result_available_at"]),
                "is_active": True,
                "invalidated_at": None,
                "updated_at": as_of,
            }
            observation = existing.get(key)
            if observation is None:
                observation = CorridorObservation(
                    event_match_id=key[0],
                    market_id=key[1],
                    created_at=as_of,
                    **values,
                )
                session.add(observation)
                existing[key] = observation
                created += 1
            else:
                for field, value in values.items():
                    setattr(observation, field, value)
                updated += 1

        await session.flush()
        return _MaterializationResult(
            materialized=processed,
            created=created,
            updated=updated,
            invalidated=max(0, getattr(invalidated, "rowcount", 0) or 0),
        )

    async def _rebuild_aggregates(
        self,
        session: AsyncSession,
        *,
        bookmaker_source_id: int,
        as_of: datetime,
        bucket_width: Decimal,
        min_samples: int,
    ) -> tuple[int, int, int, int, int]:
        # Retire derived aggregate rows instead of deleting historical output.
        # Matching data or quote selection can change only through explicit,
        # auditable source records, so a retired aggregate must never be used by
        # live value lookup.
        await session.execute(
            update(OddsCorridor)
            .where(
                OddsCorridor.bookmaker_source_id == bookmaker_source_id,
                OddsCorridor.is_active.is_(True),
            )
            .values(is_active=False, retired_at=as_of, updated_at=as_of)
        )

        groups: dict[
            tuple[
                int,
                str,
                str,
                str,
                str,
                str,
                str,
                Decimal | None,
                Decimal,
                Decimal,
            ],
            _Aggregate,
        ] = defaultdict(_Aggregate)
        observations = returned = 0
        stream = await session.stream_scalars(
            select(CorridorObservation).where(
                CorridorObservation.bookmaker_source_id == bookmaker_source_id,
                CorridorObservation.is_active.is_(True),
                CorridorObservation.result_available_at <= as_of,
            )
        )
        async for observation in stream:
            low, high = bucket_bounds(observation.odds, bucket_width)
            keys = [
                (
                    observation.bookmaker_source_id,
                    observation.sport,
                    observation.game_key,
                    "global",
                    "",
                    observation.market_code,
                    observation.selection,
                    observation.line,
                    low,
                    high,
                )
            ]
            if observation.tournament_family:
                keys.append(
                    (
                        observation.bookmaker_source_id,
                        observation.sport,
                        observation.game_key,
                        "tournament_family",
                        observation.tournament_family,
                        observation.market_code,
                        observation.selection,
                        observation.line,
                        low,
                        high,
                    )
                )
            for key in keys:
                groups[key].add(odds=observation.odds, outcome=observation.outcome)
            observations += 1
            if observation.outcome == "return":
                returned += 1

        existing = {
            (
                item.bookmaker_source_id,
                item.sport,
                item.game_key,
                item.scope_type,
                item.scope_value,
                item.market_code,
                item.selection,
                item.line,
                item.odds_min,
                item.odds_max,
            ): item
            for item in (
                await session.scalars(
                    select(OddsCorridor).where(
                        OddsCorridor.bookmaker_source_id == bookmaker_source_id
                    )
                )
            ).all()
        }
        global_buckets = tournament_buckets = 0
        for key, aggregate in groups.items():
            if aggregate.sample_size < min_samples:
                continue
            metrics = aggregate.metrics()
            (
                source_id,
                sport,
                game_key,
                scope_type,
                scope_value,
                market_code,
                selection,
                line,
                odds_min,
                odds_max,
            ) = key
            values: dict[str, object] = {
                "bucket_width": bucket_width,
                "sample_size": metrics.sample_size,
                "wins": metrics.wins,
                "losses": metrics.losses,
                "returns": metrics.returns,
                "win_rate": metrics.win_rate,
                "average_odds": metrics.average_odds,
                "average_implied_probability": metrics.average_implied_probability,
                "roi_percent": metrics.roi_percent,
                "edge_percent": metrics.edge_percent,
                "confidence": metrics.confidence,
                "as_of": as_of,
                "is_active": True,
                "retired_at": None,
                "updated_at": as_of,
            }
            corridor = existing.get(key)
            if corridor is None:
                session.add(
                    OddsCorridor(
                        bookmaker_source_id=source_id,
                        sport=sport,
                        game_key=game_key,
                        scope_type=scope_type,
                        scope_value=scope_value,
                        market_code=market_code,
                        selection=selection,
                        line=line,
                        odds_min=odds_min,
                        odds_max=odds_max,
                        created_at=as_of,
                        **values,
                    )
                )
            else:
                for field, value in values.items():
                    setattr(corridor, field, value)
            if scope_type == "global":
                global_buckets += 1
            else:
                tournament_buckets += 1

        await session.flush()
        return (
            observations,
            returned,
            global_buckets + tournament_buckets,
            global_buckets,
            tournament_buckets,
        )

    async def _acquire_rebuild_lock(self, session: AsyncSession) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            await session.execute(select(func.pg_advisory_xact_lock(self._LOCK_ID)))

    @staticmethod
    def _decimal_or_none(value: object) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
