from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.parsers.fonbet import parse_fonbet_catalog


def load_fixture() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "fonbet_list_base.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_parser_selects_target_tournaments_and_root_events() -> None:
    catalog = parse_fonbet_catalog(load_fixture())

    assert len(catalog.tournaments) == 2
    assert len(catalog.events) == 2
    football = next(event for event in catalog.events if event.external_id == "67277033")
    assert football.internal_id == "uel_ef_67277033"
    assert football.sport == "football"
    assert football.game == "FC26"
    assert football.format == "2x3"
    assert football.started_at == datetime.fromtimestamp(1786967280, tz=UTC)
    assert football.participant1.team_name == "Германия"
    assert football.participant1.player_name == "Djimbo88"


def test_parser_maps_only_verified_core_factor_semantics() -> None:
    """Test that parser uses normalized selection format for totals."""
    catalog = parse_fonbet_catalog(load_fixture())

    quote = next(
        item
        for item in catalog.quotes
        if item.event_external_id == "67277033" and item.factor_id == "930"
    )
    assert quote.odds == Decimal("2.4")
    assert quote.line == Decimal("2.5")
    assert quote.market_code == "total"
    assert quote.market_name == "Full time total"
    # Modern normalized format: selection="over" with separate line field
    assert quote.selection == "over"
    assert {quote.factor_id for quote in catalog.quotes} == {"921", "922", "923", "930"}
    identities = {
        item.factor_id: (item.market_code, item.selection) for item in catalog.quotes
    }
    assert identities == {
        "921": ("1x2", "P1"),
        "922": ("1x2", "X"),
        "923": ("1x2", "P2"),
        "930": ("total", "over"),  # normalized format
    }


def test_parser_keeps_unknown_or_incomplete_factors_unmapped() -> None:
    payload = load_fixture()
    factors = payload["customFactors"][0]["factors"]
    factors.extend(({"f": 999999, "v": 2.1}, {"f": 927, "v": 1.9}))

    catalog = parse_fonbet_catalog(payload)

    unknown = next(item for item in catalog.quotes if item.factor_id == "999999")
    incomplete_handicap = next(item for item in catalog.quotes if item.factor_id == "927")
    assert unknown.market_code == "unmapped_factor"
    assert unknown.selection == "factor:999999"
    assert incomplete_handicap.market_code == "unmapped_factor"


def test_parser_does_not_include_ordinary_football() -> None:
    catalog = parse_fonbet_catalog(load_fixture())

    assert all(event.external_id != "80000001" for event in catalog.events)
