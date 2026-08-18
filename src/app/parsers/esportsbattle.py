from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.providers.base import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class ESBTournamentRef:
    external_id: str
    name: str
    status_id: int


@dataclass(frozen=True, slots=True)
class ESBTournamentPage:
    tournaments: tuple[ESBTournamentRef, ...]
    total_pages: int
    received_count: int
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ESBTeam:
    external_id: str | None
    name: str


@dataclass(frozen=True, slots=True)
class ESBParticipant:
    external_player_key: str
    tournament_participant_external_id: str
    nickname: str
    team: ESBTeam | None

    @property
    def raw_name(self) -> str:
        if self.team is None:
            return self.nickname
        return f"{self.team.name} ({self.nickname})"


@dataclass(frozen=True, slots=True)
class ESBMatch:
    external_id: str
    started_at: datetime
    status: str
    source_status_id: int
    participant1: ESBParticipant
    participant2: ESBParticipant
    score1: int | None
    score2: int | None

    @property
    def is_finished(self) -> bool:
        return self.status == "finished" and self.score1 is not None and self.score2 is not None


@dataclass(frozen=True, slots=True)
class ESBTournamentHistory:
    external_id: str
    name: str
    source_status_id: int
    matches: tuple[ESBMatch, ...]
    received_match_count: int
    rejection_reasons: tuple[str, ...]
    sport: str = "football"
    game: str = "efootball"


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _required_name(raw: JsonObject) -> str | None:
    international = raw.get("token_international")
    localized = raw.get("token")
    if isinstance(international, str) and international.strip():
        return international.strip()
    if isinstance(localized, str) and localized.strip():
        return localized.strip()
    return None


def parse_esb_tournament_page(payload: JsonValue) -> ESBTournamentPage:
    if not isinstance(payload, dict):
        raise ValueError("ESportsBattle tournament page must be an object")
    tournaments_raw = payload.get("tournaments")
    total_pages = _int(payload.get("totalPages"))
    if not isinstance(tournaments_raw, list) or total_pages is None or total_pages < 1:
        raise ValueError("ESportsBattle tournament page has an invalid contract")

    tournaments: list[ESBTournamentRef] = []
    rejected: list[str] = []
    for index, raw in enumerate(tournaments_raw):
        if not isinstance(raw, dict):
            rejected.append(f"tournament[{index}] is not an object")
            continue
        external_id = _int(raw.get("id"))
        status_id = _int(raw.get("status_id"))
        name = _required_name(raw)
        if external_id is None or status_id is None or name is None:
            rejected.append(f"tournament[{index}] has invalid core fields")
            continue
        tournaments.append(
            ESBTournamentRef(
                external_id=str(external_id),
                name=name,
                status_id=status_id,
            )
        )
    return ESBTournamentPage(
        tournaments=tuple(tournaments),
        total_pages=total_pages,
        received_count=len(tournaments_raw),
        rejection_reasons=tuple(rejected),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _participant(raw: Any) -> ESBParticipant | None:
    if not isinstance(raw, dict):
        return None
    participant_id = _int(raw.get("id"))
    nickname = raw.get("nickname")
    if participant_id is None or not isinstance(nickname, str) or not nickname.strip():
        return None

    team_raw = raw.get("team")
    team: ESBTeam | None = None
    if isinstance(team_raw, dict):
        team_name = _required_name(team_raw)
        team_id = _int(team_raw.get("id"))
        if team_name is not None:
            team = ESBTeam(
                external_id=str(team_id) if team_id is not None else None,
                name=team_name,
            )
    cleaned_nickname = nickname.strip()
    return ESBParticipant(
        # The official participant card route is keyed by nickname. The numeric id
        # in a match is a tournament-participation id and is stored separately.
        external_player_key=cleaned_nickname,
        tournament_participant_external_id=str(participant_id),
        nickname=cleaned_nickname,
        team=team,
    )


def parse_esb_tournament_history(
    tournament_payload: JsonValue,
    matches_payload: JsonValue,
) -> ESBTournamentHistory:
    if not isinstance(tournament_payload, dict):
        raise ValueError("ESportsBattle tournament payload must be an object")
    if not isinstance(matches_payload, list):
        raise ValueError("ESportsBattle matches payload must be an array")

    tournament_id = _int(tournament_payload.get("id"))
    tournament_status_id = _int(tournament_payload.get("status_id"))
    tournament_name = _required_name(tournament_payload)
    if tournament_id is None or tournament_status_id is None or tournament_name is None:
        raise ValueError("ESportsBattle tournament has invalid core fields")

    matches: list[ESBMatch] = []
    rejected: list[str] = []
    for index, raw in enumerate(matches_payload):
        if not isinstance(raw, dict):
            rejected.append(f"match[{index}] is not an object")
            continue
        match_id = _int(raw.get("id"))
        status_id = _int(raw.get("status_id"))
        started_at = _parse_datetime(raw.get("date"))
        participant1 = _participant(raw.get("participant1"))
        participant2 = _participant(raw.get("participant2"))
        if (
            match_id is None
            or status_id is None
            or started_at is None
            or participant1 is None
            or participant2 is None
        ):
            rejected.append(f"match[{index}] has invalid core fields")
            continue

        participant1_raw = raw.get("participant1")
        participant2_raw = raw.get("participant2")
        assert isinstance(participant1_raw, dict)
        assert isinstance(participant2_raw, dict)
        score1 = _int(participant1_raw.get("score"))
        score2 = _int(participant2_raw.get("score"))
        # Status 3 was verified on completed matches from completed tournament
        # 252708. Other numeric statuses remain unknown until independently verified.
        has_final_score = status_id == 3 and score1 is not None and score2 is not None
        status = "finished" if has_final_score else "unknown"
        matches.append(
            ESBMatch(
                external_id=str(match_id),
                started_at=started_at,
                status=status,
                source_status_id=status_id,
                participant1=participant1,
                participant2=participant2,
                score1=score1,
                score2=score2,
            )
        )

    return ESBTournamentHistory(
        external_id=str(tournament_id),
        name=tournament_name,
        source_status_id=tournament_status_id,
        matches=tuple(matches),
        received_match_count=len(matches_payload),
        rejection_reasons=tuple(rejected),
    )
