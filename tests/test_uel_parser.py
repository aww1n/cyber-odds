from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.parsers.uel import parse_uel_tournament_history, parse_uel_tours_page
from app.providers.base import JsonValue

FIXTURES = Path(__file__).parent / "fixtures"


def _json(name: str) -> JsonValue:
    value: Any = json.loads((FIXTURES / name).read_bytes())
    assert isinstance(value, dict | list)
    return value


def test_uel_page_contract_and_pagination() -> None:
    page = parse_uel_tours_page(_json("uel_tours_page.json"), items_per_page=10)

    assert page.total_items == 8003
    assert page.total_pages == 801
    assert page.tours[0].route_id == "7981"
    assert page.tours[0].external_id == "226610"


def test_uel_history_uses_configured_source_timezone_and_stable_ids() -> None:
    page = parse_uel_tours_page(_json("uel_tours_page.json"), items_per_page=10)
    history = parse_uel_tournament_history(
        page.tours[0],
        _json("uel_tour_data.json"),
        source_timezone="Europe/Moscow",
        uel_sport="efootball",
    )

    assert history.external_id == "226610"
    assert len(history.matches) == 2
    first = history.matches[0]
    assert first.external_id == "4538930"
    assert first.started_at == datetime(2026, 8, 15, 5, 30, tzinfo=UTC)
    assert first.participant1.external_player_key == "183192"
    assert first.participant1.nickname == "Paulblack17"
    assert first.participant1.team.external_id == "80066"
    assert first.participant1.team.alternate_name == "Аргентина"
    assert first.score1 == 2
    assert first.score2 == 0
    assert first.source_updated_at == datetime(2026, 8, 15, 6, 18, 35, tzinfo=UTC)
    assert first.is_finished
