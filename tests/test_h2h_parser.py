from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.parsers.h2h import parse_sis_h2h_schedule, tournament_external_id
from app.providers.base import JsonValue

FIXTURES = Path(__file__).parent / "fixtures"


def _schedule() -> JsonValue:
    value: Any = json.loads((FIXTURES / "sis_h2h_schedule.json").read_bytes())
    assert isinstance(value, list)
    return value


def test_sis_h2h_schedule_preserves_utc_identity_and_results() -> None:
    history = parse_sis_h2h_schedule(
        _schedule(),
        day=date(2026, 8, 17),
        api_sport="fifa",
    )

    assert history.sport == "football"
    assert history.game == "esoccer"
    assert history.received_match_count == 3
    assert not history.rejection_reasons
    first = history.matches[0]
    assert first.external_id == "FI143170826"
    assert first.started_at == datetime(2026, 8, 17, 12, 20, tzinfo=UTC)
    assert first.participant1.external_player_key == "COSMOS"
    assert first.participant1.raw_name == "FRANCE (COSMOS)"
    assert first.score1 == 0
    assert first.score2 == 3
    assert first.is_finished
    assert history.matches[-1].status == "scheduled"
    assert not history.matches[-1].is_finished


def test_sis_h2h_tournament_identity_is_stable_and_name_sensitive() -> None:
    first = tournament_external_id("fifa", "Esoccer H2H GG League")
    second = tournament_external_id("fifa", "  esoccer   h2h gg league ")
    assert first == second
    assert first.startswith("fifa:")
    assert first != tournament_external_id("nba", "Esoccer H2H GG League")
