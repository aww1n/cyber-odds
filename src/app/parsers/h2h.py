from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.normalization.common import normalize_text
from app.providers.base import JsonValue


@dataclass(frozen=True, slots=True)
class SISH2HParticipant:
    external_player_key: str
    nickname: str
    team_name: str

    @property
    def raw_name(self) -> str:
        return f"{self.team_name} ({self.nickname})"


@dataclass(frozen=True, slots=True)
class SISH2HMatch:
    external_id: str
    started_at: datetime
    status: str
    tournament_name: str
    stream_name: str
    participant1: SISH2HParticipant
    participant2: SISH2HParticipant
    score1: int | None
    score2: int | None

    @property
    def is_finished(self) -> bool:
        return self.status == "finished" and self.score1 is not None and self.score2 is not None


@dataclass(frozen=True, slots=True)
class SISH2HDailyHistory:
    day: date
    api_sport: str
    sport: str
    game: str
    matches: tuple[SISH2HMatch, ...]
    received_match_count: int
    rejection_reasons: tuple[str, ...]


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _score(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _datetime(value: Any) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _sport_mapping(api_sport: str) -> tuple[str, str]:
    if api_sport == "fifa":
        return "football", "esoccer"
    if api_sport == "nba":
        return "basketball", "nba2k"
    if api_sport == "nfl":
        return "american_football", "madden"
    raise ValueError("SIS H2H sport must be fifa, nba or nfl")


def tournament_external_id(api_sport: str, name: str) -> str:
    normalized = normalize_text(name)
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"{api_sport}:{digest}"


def parse_sis_h2h_schedule(
    payload: JsonValue,
    *,
    day: date,
    api_sport: str,
) -> SISH2HDailyHistory:
    if not isinstance(payload, list):
        raise ValueError("SIS H2H schedule must be an array")
    sport, game = _sport_mapping(api_sport)
    matches: list[SISH2HMatch] = []
    rejected: list[str] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            rejected.append(f"match[{index}] is not an object")
            continue
        external_id = _text(raw.get("externalId"))
        started_at = _datetime(raw.get("startDate"))
        team1 = _text(raw.get("teamAName"))
        team2 = _text(raw.get("teamBName"))
        player1 = _text(raw.get("participantAName"))
        player2 = _text(raw.get("participantBName"))
        tournament_name = _text(raw.get("tournamentName"))
        stream_name = _text(raw.get("streamName"))
        if None in {
            external_id,
            started_at,
            team1,
            team2,
            player1,
            player2,
            tournament_name,
            stream_name,
        }:
            rejected.append(f"match[{index}] has invalid core fields")
            continue
        assert external_id is not None
        assert started_at is not None
        assert team1 is not None
        assert team2 is not None
        assert player1 is not None
        assert player2 is not None
        assert tournament_name is not None
        assert stream_name is not None
        score1 = _score(raw.get("teamAScore"))
        score2 = _score(raw.get("teamBScore"))
        source_status = _text(raw.get("matchStatus"))
        if raw.get("isCancelled") is True:
            status = "cancelled"
        elif source_status == "MATCH_ENDED" and score1 is not None and score2 is not None:
            status = "finished"
        elif source_status is None and score1 is None and score2 is None:
            status = "scheduled"
        else:
            status = "unknown"
        matches.append(
            SISH2HMatch(
                external_id=external_id,
                started_at=started_at,
                status=status,
                tournament_name=tournament_name,
                stream_name=stream_name,
                participant1=SISH2HParticipant(player1, player1, team1),
                participant2=SISH2HParticipant(player2, player2, team2),
                score1=score1,
                score2=score2,
            )
        )
    return SISH2HDailyHistory(
        day=day,
        api_sport=api_sport,
        sport=sport,
        game=game,
        matches=tuple(matches),
        received_match_count=len(payload),
        rejection_reasons=tuple(rejected),
    )
