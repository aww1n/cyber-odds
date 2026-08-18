from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import StrategySettings, load_strategies


def test_live_uel_strategy_is_enabled_with_current_sample_threshold() -> None:
    path = Path(__file__).parents[1] / "config" / "strategies.yaml"

    configured = load_strategies(path)
    strategy = configured.strategies["uel_football"]

    assert strategy.prediction_enabled
    assert strategy.alerts_enabled
    assert strategy.min_samples == 12
    assert strategy.safety_multiplier == 1.15
    assert strategy.allowed_markets == ("1x2",)
    assert strategy.backtest_config().filters.min_samples == 12
    assert not configured.strategies["fon_ml_v1"].prediction_enabled


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"min_odds": 3.0, "max_odds": 2.0}, "min_odds cannot exceed max_odds"),
        (
            {"min_probability": 0.8, "max_probability": 0.2},
            "min_probability cannot exceed max_probability",
        ),
        (
            {"prediction_enabled": False, "alerts_enabled": True},
            "alerts_enabled requires prediction_enabled",
        ),
    ],
)
def test_strategy_rejects_inconsistent_configuration(
    overrides: dict[str, object],
    message: str,
) -> None:
    payload: dict[str, object] = {
        "source": "uel_ef",
        "model_name": "individual_h2h_fixed",
        "model_version": "test",
        "safety_multiplier": 1.0,
    }
    payload.update(overrides)

    with pytest.raises(ValidationError, match=message):
        StrategySettings.model_validate(payload)
