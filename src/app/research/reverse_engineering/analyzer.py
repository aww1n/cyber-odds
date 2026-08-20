from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

from app.value.staking import observed_ml_bet_multiplier


@dataclass(frozen=True, slots=True)
class AnalysisOutputs:
    row_count: int
    csv_path: Path
    json_path: Path


def _optional_number(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{record.get('id', '<unknown>')}: {key} must be a number or null")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{record.get('id', '<unknown>')}: {key} must be finite")
    return number


def analyze_observation(record: dict[str, Any]) -> dict[str, Any]:
    """Derive formula outputs while preserving the observed fields verbatim."""

    observation_id = record.get("id")
    if not isinstance(observation_id, str) or not observation_id:
        raise ValueError("Every observation must have a non-empty string id")

    odds = _optional_number(record, "odds")
    value_ratio = _optional_number(record, "v")
    shown_value = _optional_number(record, "shown_value")
    minimum_odds = _optional_number(record, "mo")
    observed_bet_multiplier = _optional_number(record, "bet_mul")

    if odds is not None and odds <= 1:
        raise ValueError(f"{observation_id}: decimal odds must be greater than one")
    if value_ratio is not None and value_ratio <= 0:
        raise ValueError(f"{observation_id}: v must be greater than zero")

    probability = None
    fair = None
    if odds is not None and value_ratio is not None:
        probability = value_ratio / odds
        if probability > 1:
            raise ValueError(f"{observation_id}: inferred probability exceeds one")
        fair = 1.0 / probability

    predicted_value_percent = (
        (value_ratio - 1.0) * 100.0 if value_ratio is not None else None
    )
    shown_value_percent = shown_value * 100.0 if shown_value is not None else None
    predicted_bet_multiplier = (
        float(observed_ml_bet_multiplier(value_ratio))
        if value_ratio is not None
        else None
    )
    safety_multiplier = (
        minimum_odds / fair if minimum_odds is not None and fair is not None else None
    )

    return {
        **record,
        "inferred_probability": probability,
        "inferred_fair_odds": fair,
        "predicted_value_percent": predicted_value_percent,
        "inferred_safety_multiplier": safety_multiplier,
        "predicted_bet_mul": predicted_bet_multiplier,
        "value_percent_delta": (
            shown_value_percent - predicted_value_percent
            if shown_value_percent is not None and predicted_value_percent is not None
            else None
        ),
        "bet_mul_delta": (
            observed_bet_multiplier - predicted_bet_multiplier
            if observed_bet_multiplier is not None and predicted_bet_multiplier is not None
            else None
        ),
        "mo_minus_fair_odds": (
            minimum_odds - fair if minimum_odds is not None and fair is not None else None
        ),
    }


def analyze_file(input_path: Path, output_dir: Path) -> AnalysisOutputs:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Known alerts file must contain a JSON array")
    records = [analyze_observation(record) for record in payload]

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "known_alerts_analysis.json"
    csv_path = output_dir / "known_alerts_analysis.csv"

    artifact = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input": str(input_path),
        "row_count": len(records),
        "records": records,
    }
    json_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    fieldnames = list(records[0]) if records else []
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        if fieldnames:
            writer.writeheader()
            writer.writerows(records)

    return AnalysisOutputs(row_count=len(records), csv_path=csv_path, json_path=json_path)
