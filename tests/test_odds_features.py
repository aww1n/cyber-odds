from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.features.odds import OddsPoint, normalized_implied_probabilities, odds_movement_before


def test_one_x_two_margin_is_removed() -> None:
    probabilities = normalized_implied_probabilities({"P1": 2.0, "X": 4.0, "P2": 4.0})

    assert probabilities["P1"].raw_probability == 0.5
    assert probabilities["P1"].normalized_probability == 0.5
    assert sum(item.normalized_probability for item in probabilities.values()) == 1


def test_odds_movement_uses_only_snapshots_strictly_before_cutoff() -> None:
    cutoff = datetime(2026, 8, 17, 12, tzinfo=UTC)
    movement = odds_movement_before(
        [
            OddsPoint(2.0, cutoff - timedelta(minutes=10)),
            OddsPoint(2.2, cutoff - timedelta(minutes=1)),
            OddsPoint(9.9, cutoff),
        ],
        cutoff_at=cutoff,
    )

    assert movement is not None
    assert movement.opening_odds == 2.0
    assert movement.current_odds == 2.2
    assert movement.change_absolute == pytest.approx(0.2)
    assert movement.change_percent == pytest.approx(10)
    assert movement.observations == 2
