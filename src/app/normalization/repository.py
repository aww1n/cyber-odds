from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Event,
    EventParticipant,
    NormalizationReview,
    Player,
    PlayerAlias,
    Source,
    Team,
    TeamAlias,
)
from app.normalization.common import Candidate, NormalizationDecision, decide_normalization
from app.normalization.players import normalize_player_name
from app.normalization.teams import normalize_team_name


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    participants_seen: int
    players_attached: int
    teams_attached: int
    players_created: int
    teams_created: int
    reviews_created: int


class SourceParticipantNormalizer:
    """Resolve source-scoped participant strings without silent ambiguous merges."""

    def __init__(
        self,
        *,
        auto_fuzzy_threshold: float = 0.985,
        review_threshold: float = 0.80,
        minimum_margin: float = 0.05,
    ) -> None:
        self._auto_fuzzy_threshold = auto_fuzzy_threshold
        self._review_threshold = review_threshold
        self._minimum_margin = minimum_margin

    async def normalize_source(
        self,
        session: AsyncSession,
        *,
        source_code: str,
    ) -> NormalizationStats:
        source = await session.scalar(select(Source).where(Source.code == source_code))
        if source is None:
            raise ValueError(f"Unknown source code: {source_code}")

        participants = list(
            (
                await session.scalars(
                    select(EventParticipant)
                    .join(Event, Event.id == EventParticipant.event_id)
                    .where(Event.source_id == source.id)
                    .order_by(EventParticipant.id)
                )
            ).all()
        )
        events = {
            event.id: event
            for event in (
                await session.scalars(
                    select(Event).where(Event.source_id == source.id)
                )
            ).all()
        }
        players = list((await session.scalars(select(Player).order_by(Player.id))).all())
        teams = list((await session.scalars(select(Team).order_by(Team.id))).all())
        player_candidates = [
            Candidate(item.id, item.display_name, item.normalized_name) for item in players
        ]
        team_candidates = [
            Candidate(item.id, item.display_name, item.normalized_name) for item in teams
        ]
        all_player_aliases = list((await session.scalars(select(PlayerAlias))).all())
        all_team_aliases = list((await session.scalars(select(TeamAlias))).all())
        player_aliases = {
            item.normalized_alias: item.player_id
            for item in all_player_aliases
            if item.source_id == source.id
        }
        team_aliases = {
            item.normalized_alias: item.team_id
            for item in all_team_aliases
            if item.source_id == source.id
        }
        global_player_aliases: dict[str, set[int]] = {}
        for player_alias in all_player_aliases:
            global_player_aliases.setdefault(player_alias.normalized_alias, set()).add(
                player_alias.player_id
            )
        global_team_aliases: dict[str, set[int]] = {}
        for team_alias in all_team_aliases:
            global_team_aliases.setdefault(team_alias.normalized_alias, set()).add(
                team_alias.team_id
            )
        existing_reviews = {
            (item.entity_type, item.normalized_value, item.candidate_id)
            for item in (
                await session.scalars(
                    select(NormalizationReview).where(
                        NormalizationReview.source_id == source.id,
                        NormalizationReview.status == "pending",
                    )
                )
            ).all()
        }

        players_created = 0
        teams_created = 0
        reviews_created = 0
        for participant in participants:
            player_value = participant.raw_player_name or participant.external_player_key
            if participant.player_id is None and player_value:
                normalized = normalize_player_name(player_value)
                if normalized in player_aliases:
                    participant.player_id = player_aliases[normalized]
                else:
                    decision = self._decide_with_global_aliases(
                        normalized,
                        player_candidates,
                        global_player_aliases,
                    )
                    player_id, created, review_created = await self._apply_player_decision(
                        session,
                        source=source,
                        raw_value=player_value,
                        normalized=normalized,
                        decision=decision,
                        candidates=player_candidates,
                        aliases=player_aliases,
                        existing_reviews=existing_reviews,
                    )
                    participant.player_id = player_id
                    if player_id is not None:
                        global_player_aliases.setdefault(normalized, set()).add(player_id)
                    players_created += created
                    reviews_created += review_created

            team_value = participant.raw_team_name
            if participant.team_id is None and team_value:
                normalized = normalize_team_name(team_value)
                if normalized in team_aliases:
                    participant.team_id = team_aliases[normalized]
                else:
                    decision = self._decide_with_global_aliases(
                        normalized,
                        team_candidates,
                        global_team_aliases,
                    )
                    team_id, created, review_created = await self._apply_team_decision(
                        session,
                        source=source,
                        raw_value=team_value,
                        normalized=normalized,
                        decision=decision,
                        candidates=team_candidates,
                        aliases=team_aliases,
                        existing_reviews=existing_reviews,
                    )
                    participant.team_id = team_id
                    if team_id is not None:
                        global_team_aliases.setdefault(normalized, set()).add(team_id)
                    teams_created += created
                    reviews_created += review_created

            alternate_team = participant.raw_team_name_alt
            if participant.team_id is not None and alternate_team:
                alternate_normalized = normalize_team_name(alternate_team)
                existing_team_id = team_aliases.get(alternate_normalized)
                global_ids = global_team_aliases.get(alternate_normalized, set())
                if existing_team_id is None and (
                    not global_ids or global_ids == {participant.team_id}
                ):
                    session.add(
                        TeamAlias(
                            team_id=participant.team_id,
                            source_id=source.id,
                            alias=alternate_team,
                            normalized_alias=alternate_normalized,
                            match_method="exact_alt",
                            confidence=Decimal("1"),
                            confirmed=True,
                        )
                    )
                    team_aliases[alternate_normalized] = participant.team_id
                    global_team_aliases.setdefault(alternate_normalized, set()).add(
                        participant.team_id
                    )
                    await session.flush()
                elif existing_team_id not in {None, participant.team_id} or (
                    global_ids and participant.team_id not in global_ids
                ):
                    conflict_id = existing_team_id or min(global_ids)
                    reviews_created += await self._review(
                        session,
                        source=source,
                        entity_type="team",
                        raw_value=alternate_team,
                        decision=NormalizationDecision(
                            "review",
                            alternate_normalized,
                            conflict_id,
                            1.0,
                            1.0,
                        ),
                        existing=existing_reviews,
                    )

            event = events[participant.event_id]
            if participant.side == 1:
                event.player1_id = participant.player_id
                event.team1_id = participant.team_id
            else:
                event.player2_id = participant.player_id
                event.team2_id = participant.team_id

        await session.flush()
        return NormalizationStats(
            participants_seen=len(participants),
            players_attached=sum(item.player_id is not None for item in participants),
            teams_attached=sum(item.team_id is not None for item in participants),
            players_created=players_created,
            teams_created=teams_created,
            reviews_created=reviews_created,
        )

    def _decide(
        self, normalized: str, candidates: list[Candidate]
    ) -> NormalizationDecision:
        return decide_normalization(
            normalized,
            candidates,
            auto_fuzzy_threshold=self._auto_fuzzy_threshold,
            review_threshold=self._review_threshold,
            minimum_margin=self._minimum_margin,
        )

    def _decide_with_global_aliases(
        self,
        normalized: str,
        candidates: list[Candidate],
        global_aliases: dict[str, set[int]],
    ) -> NormalizationDecision:
        alias_ids = global_aliases.get(normalized, set())
        if len(alias_ids) == 1:
            return NormalizationDecision(
                "exact", normalized, next(iter(alias_ids)), 1.0, 0.0
            )
        if len(alias_ids) > 1:
            return NormalizationDecision(
                "review", normalized, min(alias_ids), 1.0, 1.0
            )
        return self._decide(normalized, candidates)

    async def _apply_player_decision(
        self,
        session: AsyncSession,
        *,
        source: Source,
        raw_value: str,
        normalized: str,
        decision: NormalizationDecision,
        candidates: list[Candidate],
        aliases: dict[str, int],
        existing_reviews: set[tuple[str, str, int | None]],
    ) -> tuple[int | None, int, int]:
        if decision.action == "review":
            created = await self._review(
                session,
                source=source,
                entity_type="player",
                raw_value=raw_value,
                decision=decision,
                existing=existing_reviews,
            )
            return None, 0, created
        if decision.action == "new":
            entity = Player(display_name=raw_value, normalized_name=normalized, active=True)
            session.add(entity)
            await session.flush()
            candidates.append(Candidate(entity.id, entity.display_name, entity.normalized_name))
            entity_id, created = entity.id, 1
        else:
            assert decision.candidate_id is not None
            entity_id, created = decision.candidate_id, 0
        session.add(
            PlayerAlias(
                player_id=entity_id,
                source_id=source.id,
                alias=raw_value,
                normalized_alias=normalized,
                match_method=decision.action,
                confidence=Decimal(str(decision.confidence if decision.action != "new" else 1)),
                confirmed=True,
            )
        )
        aliases[normalized] = entity_id
        await session.flush()
        return entity_id, created, 0

    async def _apply_team_decision(
        self,
        session: AsyncSession,
        *,
        source: Source,
        raw_value: str,
        normalized: str,
        decision: NormalizationDecision,
        candidates: list[Candidate],
        aliases: dict[str, int],
        existing_reviews: set[tuple[str, str, int | None]],
    ) -> tuple[int | None, int, int]:
        if decision.action == "review":
            created = await self._review(
                session,
                source=source,
                entity_type="team",
                raw_value=raw_value,
                decision=decision,
                existing=existing_reviews,
            )
            return None, 0, created
        if decision.action == "new":
            entity = Team(display_name=raw_value, normalized_name=normalized, active=True)
            session.add(entity)
            await session.flush()
            candidates.append(Candidate(entity.id, entity.display_name, entity.normalized_name))
            entity_id, created = entity.id, 1
        else:
            assert decision.candidate_id is not None
            entity_id, created = decision.candidate_id, 0
        session.add(
            TeamAlias(
                team_id=entity_id,
                source_id=source.id,
                alias=raw_value,
                normalized_alias=normalized,
                match_method=decision.action,
                confidence=Decimal(str(decision.confidence if decision.action != "new" else 1)),
                confirmed=True,
            )
        )
        aliases[normalized] = entity_id
        await session.flush()
        return entity_id, created, 0

    @staticmethod
    async def _review(
        session: AsyncSession,
        *,
        source: Source,
        entity_type: Literal["player", "team"],
        raw_value: str,
        decision: NormalizationDecision,
        existing: set[tuple[str, str, int | None]],
    ) -> int:
        key = (entity_type, decision.normalized_value, decision.candidate_id)
        if key in existing:
            return 0
        session.add(
            NormalizationReview(
                source_id=source.id,
                entity_type=entity_type,
                raw_value=raw_value,
                normalized_value=decision.normalized_value,
                candidate_id=decision.candidate_id,
                confidence=Decimal(str(decision.confidence)),
                status="pending",
                reason=(
                    "fuzzy candidate requires review; "
                    f"best={decision.confidence:.4f}, "
                    f"second={decision.second_best_confidence:.4f}"
                ),
            )
        )
        existing.add(key)
        await session.flush()
        return 1
