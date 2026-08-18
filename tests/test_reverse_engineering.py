from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.reverse_engineering.analyzer import analyze_file, analyze_observation


def test_full_example_derives_target_and_deltas() -> None:
    result = analyze_observation(
        {
            "id": "alert-1",
            "odds": 2.15,
            "v": 1.235,
            "shown_value": 0.235,
            "mo": 1.74,
            "bet_mul": 2.35,
        }
    )

    assert result["inferred_probability"] == pytest.approx(1.235 / 2.15)
    assert result["inferred_fair_odds"] == pytest.approx(2.15 / 1.235)
    assert result["predicted_value_percent"] == pytest.approx(23.5)
    assert result["inferred_safety_multiplier"] == pytest.approx(1.74 / (2.15 / 1.235))
    assert result["predicted_bet_mul"] == pytest.approx(2.35)
    assert result["value_percent_delta"] == pytest.approx(0.0)
    assert result["bet_mul_delta"] == pytest.approx(0.0)


def test_partial_formula_example_does_not_invent_missing_probability() -> None:
    result = analyze_observation(
        {"id": "formula-only", "odds": None, "v": 1.364, "bet_mul": 3.0}
    )

    assert result["inferred_probability"] is None
    assert result["inferred_fair_odds"] is None
    assert result["predicted_bet_mul"] == 3.0


def test_analyze_file_writes_reproducible_tables(tmp_path: Path) -> None:
    input_path = tmp_path / "alerts.json"
    input_path.write_text(
        json.dumps([{"id": "example", "odds": 2.0, "v": 1.2}]), encoding="utf-8"
    )

    outputs = analyze_file(input_path, tmp_path / "output")

    assert outputs.row_count == 1
    assert outputs.csv_path.exists()
    assert outputs.json_path.exists()
    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["records"][0]["inferred_probability"] == pytest.approx(0.6)

