from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.providers.base import JsonObject

TARGET_MARKERS = ("esportsbattle", "h2h", "united esports leagues")
PARTICIPANT_PATTERN = re.compile(r"^\s*(?P<team>.+?)\s*\((?P<player>[^()]*)\)\s*$")
GAME_PATTERN = re.compile(r"\b(?P<name>FC|NHL)\s*(?P<version>\d{2})\b", re.IGNORECASE)
FORMAT_PATTERN = re.compile(
    r"(?P<periods>\d+)\s*[x\u0445\u00d7]\s*(?P<minutes>\d+)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class FonbetTournament:
    external_id: str
    name: str
    sport: str
    game: str | None
    format: str | None
    source_family: str
    country_code: str | None


@dataclass(frozen=True, slots=True)
class FonbetParticipant:
    raw_name: str
    team_name: str | None
    player_name: str | None


@dataclass(frozen=True, slots=True)
class FonbetEvent:
    external_id: str
    internal_id: str
    tournament_external_id: str
    sport: str
    game: str | None
    format: str | None
    started_at: datetime
    status: str
    participant1: FonbetParticipant
    participant2: FonbetParticipant


@dataclass(frozen=True, slots=True)
class FonbetQuote:
    event_external_id: str
    factor_id: str
    market_code: str
    market_name: str
    selection: str
    odds: Decimal
    line: Decimal | None


@dataclass(frozen=True, slots=True)
class FonbetCatalog:
    tournaments: tuple[FonbetTournament, ...]
    events: tuple[FonbetEvent, ...]
    quotes: tuple[FonbetQuote, ...]
    received_event_count: int
    rejected_event_count: int
    rejection_reasons: tuple[str, ...]
    packet_version: str | None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _source_family(name: str) -> str | None:
    folded = name.casefold()
    if "esportsbattle" in folded:
        return "esb"
    if "united esports leagues" in folded:
        return "uel"
    if "h2h gg league" in folded:
        return "h2h_ggl"
    if "h2h" in folded:
        return "h2h"
    return None


def _country_code(name: str) -> str | None:
    folded = name.casefold()
    if "чехия" in folded or "czech" in folded:
        return "CZ"
    return None


def _game_and_format(name: str) -> tuple[str | None, str | None]:
    game_match = GAME_PATTERN.search(name)
    game = (
        f"{game_match.group('name').upper()}{game_match.group('version')}"
        if game_match
        else None
    )
    format_match = FORMAT_PATTERN.search(name)
    event_format = (
        f"{format_match.group('periods')}x{format_match.group('minutes')}"
        if format_match
        else None
    )
    return game, event_format


def _participant(raw_name: str) -> FonbetParticipant:
    match = PARTICIPANT_PATTERN.match(raw_name)
    if match is None:
        return FonbetParticipant(raw_name=raw_name, team_name=None, player_name=None)
    return FonbetParticipant(
        raw_name=raw_name,
        team_name=match.group("team").strip(),
        player_name=match.group("player").strip(),
    )


def _line(factor: JsonObject) -> Decimal | None:
    line_text = factor.get("pt")
    if isinstance(line_text, str):
        try:
            return Decimal(line_text)
        except InvalidOperation:
            return None
    parameter = factor.get("p")
    if isinstance(parameter, int) and not isinstance(parameter, bool):
        return Decimal(parameter) / Decimal(100)
    return None


def _factor_identity(
    factor_id: int,
    line: Decimal | None,
) -> tuple[str, str, str]:
    fixed = {
        921: ("1x2", "Full time result", "P1"),
        922: ("1x2", "Full time result", "X"),
        923: ("1x2", "Full time result", "P2"),
    }
    if factor_id in fixed:
        return fixed[factor_id]
    if line is not None:
        rendered_line = format(line, "f")
        parameterized = {
            927: ("handicap", "Full time handicap", f"F1({rendered_line})"),
            928: ("handicap", "Full time handicap", f"F2({rendered_line})"),
            930: ("total", "Full time total", f"TB({rendered_line})"),
            931: ("total", "Full time total", f"TM({rendered_line})"),
        }
        if factor_id in parameterized:
            return parameterized[factor_id]
    return (
        "unmapped_factor",
        f"Unmapped Fonbet factor {factor_id}",
        f"factor:{factor_id}",
    )


def parse_fonbet_catalog(payload: JsonObject) -> FonbetCatalog:
    sports_raw = payload.get("sports")
    events_raw = payload.get("events")
    factors_raw = payload.get("customFactors", [])
    if not isinstance(sports_raw, list) or not isinstance(events_raw, list):
        raise ValueError("Fonbet catalog requires sports and events arrays")
    if not isinstance(factors_raw, list):
        raise ValueError("Fonbet customFactors must be an array")

    sports = {
        node["id"]: node
        for node in sports_raw
        if isinstance(node, dict) and _optional_int(node.get("id")) is not None
    }
    tournament_by_id: dict[int, FonbetTournament] = {}
    prefix_by_id: dict[int, str] = {}
    for sport_id, node in sports.items():
        name = node.get("name")
        if not isinstance(name, str) or not any(
            marker in name.casefold() for marker in TARGET_MARKERS
        ):
            continue
        family = _source_family(name)
        parent_id = _optional_int(node.get("parentId"))
        root = sports.get(parent_id, {})
        root_alias = root.get("alias") if isinstance(root, dict) else None
        if root_alias == "football":
            sport, short_sport = "football", "ef"
        elif root_alias == "hockey":
            sport, short_sport = "hockey", "eh"
        else:
            continue
        if family is None:
            continue
        game, event_format = _game_and_format(name)
        tournament_by_id[sport_id] = FonbetTournament(
            external_id=str(sport_id),
            name=name,
            sport=sport,
            game=game,
            format=event_format,
            source_family=family,
            country_code=_country_code(name),
        )
        prefix_by_id[sport_id] = f"{family}_{short_sport}"

    parsed_events: list[FonbetEvent] = []
    rejected: list[str] = []
    event_ids: set[int] = set()
    for raw in events_raw:
        if not isinstance(raw, dict):
            continue
        sport_id = _optional_int(raw.get("sportId"))
        if sport_id not in tournament_by_id or raw.get("level") != 1:
            continue
        event_id = _optional_int(raw.get("id"))
        team1 = raw.get("team1")
        team2 = raw.get("team2")
        start_time = _optional_int(raw.get("startTime"))
        if event_id is None or not isinstance(team1, str) or not isinstance(team2, str):
            rejected.append(f"invalid core fields for event {raw.get('id', '<missing>')}")
            continue
        if start_time is None:
            rejected.append(f"missing startTime for event {event_id}")
            continue
        tournament = tournament_by_id[sport_id]
        place = raw.get("place")
        status = "live" if place == "live" else "scheduled" if place == "line" else "unknown"
        parsed_events.append(
            FonbetEvent(
                external_id=str(event_id),
                internal_id=f"{prefix_by_id[sport_id]}_{event_id}",
                tournament_external_id=str(sport_id),
                sport=tournament.sport,
                game=tournament.game,
                format=tournament.format,
                started_at=datetime.fromtimestamp(start_time, tz=UTC),
                status=status,
                participant1=_participant(team1),
                participant2=_participant(team2),
            )
        )
        event_ids.add(event_id)

    quotes: list[FonbetQuote] = []
    for event_factors in factors_raw:
        if not isinstance(event_factors, dict):
            continue
        event_id = _optional_int(event_factors.get("e"))
        factor_list = event_factors.get("factors")
        if event_id not in event_ids or not isinstance(factor_list, list):
            continue
        for raw_factor in factor_list:
            if not isinstance(raw_factor, dict):
                continue
            factor_id = _optional_int(raw_factor.get("f"))
            value = raw_factor.get("v")
            if factor_id is None or not isinstance(value, int | float) or isinstance(value, bool):
                continue
            odds = Decimal(str(value))
            if odds <= 1:
                continue
            line = _line(raw_factor)
            market_code, market_name, selection = _factor_identity(factor_id, line)
            quotes.append(
                FonbetQuote(
                    event_external_id=str(event_id),
                    factor_id=str(factor_id),
                    market_code=market_code,
                    market_name=market_name,
                    selection=selection,
                    odds=odds,
                    line=line,
                )
            )

    packet_version = payload.get("packetVersion")
    return FonbetCatalog(
        tournaments=tuple(tournament_by_id.values()),
        events=tuple(parsed_events),
        quotes=tuple(quotes),
        received_event_count=len(events_raw),
        rejected_event_count=len(rejected),
        rejection_reasons=tuple(rejected),
        packet_version=str(packet_version) if isinstance(packet_version, int | str) else None,
    )
