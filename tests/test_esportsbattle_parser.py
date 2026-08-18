from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.parsers.esportsbattle import (
    parse_esb_tournament_history,
    parse_esb_tournament_page,
)
from app.providers.base import JsonValue

FIXTURES = Path(__file__).parent / "fixtures"


def _json(name: str) -> JsonValue:
    value: Any = json.loads((FIXTURES / name).read_bytes())
    assert isinstance(value, dict | list)
    return value


def test_tournament_page_preserves_observed_statuses() -> None:
    page = parse_esb_tournament_page(_json("esb_participant_tournaments.json"))

    assert page.total_pages == 8
    assert [item.external_id for item in page.tournaments] == ["252799", "252708"]
    assert [item.status_id for item in page.tournaments] == [3, 4]


def test_history_parser_preserves_source_identity_and_results() -> None:
    history = parse_esb_tournament_history(
        _json("esb_tournament.json"),
        _json("esb_matches.json"),
    )

    assert history.external_id == "252708"
    assert history.name == "Europa League 2026-08-16"
    assert len(history.matches) == 2
    first = history.matches[0]
    assert first.external_id == "2226225"
    assert first.status == "finished"
    assert first.score1 == 5
    assert first.score2 == 4
    assert first.participant1.external_player_key == "Artrom"
    assert first.participant1.tournament_participant_external_id == "894728"
    assert first.participant1.team is not None
    assert first.participant1.team.external_id == "221"
    assert first.participant1.raw_name == "Fenerbahce (Artrom)"


def test_unknown_status_is_not_declared_finished() -> None:
    matches = _json("esb_matches.json")
    assert isinstance(matches, list)
    assert isinstance(matches[0], dict)
    matches[0]["status_id"] = 99

    history = parse_esb_tournament_history(_json("esb_tournament.json"), matches)

    assert history.matches[0].status == "unknown"
    assert not history.matches[0].is_finished
