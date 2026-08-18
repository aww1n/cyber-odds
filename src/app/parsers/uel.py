from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.providers.base import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class UELTourRef:
    route_id: str
    external_id: str
    name: str
    state: str
    country: str | None


@dataclass(frozen=True, slots=True)
class UELToursPage:
    tours: tuple[UELTourRef, ...]
    total_items: int
    total_pages: int
    received_count: int
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UELTeam:
    external_id: str
    name: str
    alternate_name: str | None


@dataclass(frozen=True, slots=True)
class UELParticipant:
    external_player_key: str
    nickname: str
    team: UELTeam

    @property
    def raw_name(self) -> str:
        return f"{self.team.name} ({self.nickname})"


@dataclass(frozen=True, slots=True)
class UELMatch:
    external_id: str
    started_at: datetime
    status: str
    participant1: UELParticipant
    participant2: UELParticipant
    score1: int | None
    score2: int | None
    source_updated_at: datetime | None

    @property
    def is_finished(self) -> bool:
        return self.status == "finished" and self.score1 is not None and self.score2 is not None


@dataclass(frozen=True, slots=True)
class UELTournamentHistory:
    external_id: str
    name: str
    matches: tuple[UELMatch, ...]
    received_match_count: int
    rejection_reasons: tuple[str, ...]
    sport: str
    game: str
    country_code: str | None


def _string(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _name(raw: JsonObject, prefix: str = "title") -> str | None:
    english = raw.get(f"{prefix}_en")
    russian = raw.get(f"{prefix}_ru")
    return _string(english) or _string(russian)


def parse_uel_tours_page(payload: JsonValue, *, items_per_page: int) -> UELToursPage:
    if not isinstance(payload, dict):
        raise ValueError("UEL tours page must be an object")
    items = payload.get("items")
    total_items_value = payload.get("total_items")
    total_items_text = _string(total_items_value)
    if not isinstance(items, list) or total_items_text is None or items_per_page <= 0:
        raise ValueError("UEL tours page has an invalid contract")
    try:
        total_items = int(total_items_text)
    except ValueError as error:
        raise ValueError("UEL total_items must be an integer") from error

    tours: list[UELTourRef] = []
    rejected: list[str] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            rejected.append(f"tour[{index}] is not an object")
            continue
        route_id = _string(raw.get("id"))
        external_id = _string(raw.get("id_sl"))
        name = _name(raw)
        state = _string(raw.get("state"))
        if route_id is None or external_id is None or name is None or state is None:
            rejected.append(f"tour[{index}] has invalid core fields")
            continue
        tours.append(
            UELTourRef(
                route_id=route_id,
                external_id=external_id,
                name=name,
                state=state.casefold(),
                country=_string(raw.get("country")),
            )
        )
    return UELToursPage(
        tours=tuple(tours),
        total_items=total_items,
        total_pages=math.ceil(total_items / items_per_page),
        received_count=len(items),
        rejection_reasons=tuple(rejected),
    )


def _parse_local_datetime(value: Any, source_timezone: str) -> datetime | None:
    text = _string(value)
    if text is None:
        return None
    try:
        timezone = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown UEL source timezone: {source_timezone}") from error
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone
        ).astimezone(UTC)
    except ValueError:
        return None


def _parse_utc_datetime(value: Any) -> datetime | None:
    text = _string(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _score(value: Any) -> int | None:
    text = _string(value)
    if text is None:
        return None
    try:
        score = int(text)
    except ValueError:
        return None
    return score if score >= 0 else None


def _participant(raw: JsonObject, side: int) -> UELParticipant | None:
    player_id = _string(raw.get(f"player_{side}_id_sl")) or _string(
        raw.get(f"player{side}_id")
    )
    player_name = _string(raw.get(f"player_{side}_title_en")) or _string(
        raw.get(f"player_{side}_title_ru")
    )
    team_id = _string(raw.get(f"team_{side}_id_sl")) or _string(
        raw.get(f"team{side}_id")
    )
    english_team_name = _string(raw.get(f"team_{side}_title_en"))
    russian_team_name = _string(raw.get(f"team_{side}_title_ru"))
    team_name = english_team_name or russian_team_name
    if player_id is None or player_name is None or team_id is None or team_name is None:
        return None
    return UELParticipant(
        external_player_key=player_id,
        nickname=player_name,
        team=UELTeam(
            team_id,
            team_name,
            russian_team_name if russian_team_name != team_name else None,
        ),
    )


def parse_uel_tournament_history(
    tour: UELTourRef,
    payload: JsonValue,
    *,
    source_timezone: str,
    uel_sport: str,
) -> UELTournamentHistory:
    if not isinstance(payload, dict):
        raise ValueError("UEL tour data must be an object")
    blocks = payload.get("tour_blocks")
    games = blocks.get("games") if isinstance(blocks, dict) else None
    if not isinstance(games, list):
        raise ValueError("UEL tour data has no games array")
    if uel_sport == "efootball":
        sport, game = "football", "efootball"
    elif uel_sport == "ehockey":
        sport, game = "hockey", "ehockey"
    else:
        raise ValueError("UEL sport must be efootball or ehockey")

    matches: list[UELMatch] = []
    rejected: list[str] = []
    for index, raw in enumerate(games):
        if not isinstance(raw, dict):
            rejected.append(f"game[{index}] is not an object")
            continue
        external_id = _string(raw.get("id_sl")) or _string(raw.get("id"))
        started_at = _parse_local_datetime(raw.get("date_time"), source_timezone)
        state = _string(raw.get("state"))
        participant1 = _participant(raw, 1)
        participant2 = _participant(raw, 2)
        if (
            external_id is None
            or started_at is None
            or state is None
            or participant1 is None
            or participant2 is None
        ):
            rejected.append(f"game[{index}] has invalid core fields")
            continue
        score1 = _score(raw.get("score_team1"))
        score2 = _score(raw.get("score_team2"))
        normalized_state = state.casefold()
        known_states = {"scheduled", "live", "finished", "cancelled"}
        status = normalized_state if normalized_state in known_states else "unknown"
        source_updated_at = _parse_utc_datetime(raw.get("updated_at"))
        if source_updated_at is not None and source_updated_at <= started_at:
            # Upcoming rows use their creation/update time, which is not result
            # availability. Finished rows observed so far update after kickoff.
            source_updated_at = None
        matches.append(
            UELMatch(
                external_id=external_id,
                started_at=started_at,
                status=status,
                participant1=participant1,
                participant2=participant2,
                score1=score1,
                score2=score2,
                source_updated_at=source_updated_at,
            )
        )
    return UELTournamentHistory(
        external_id=tour.external_id,
        name=tour.name,
        matches=tuple(matches),
        received_match_count=len(games),
        rejection_reasons=tuple(rejected),
        sport=sport,
        game=game,
        country_code=tour.country,
    )
