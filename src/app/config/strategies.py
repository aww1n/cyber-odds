from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.backtest.engine import BacktestConfig
from app.value.filters import SignalFilters


class StrategySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prediction_enabled: bool = False
    alerts_enabled: bool = False
    source: str
    model_name: str
    model_version: str
    safety_multiplier: float = Field(gt=0)
    stake_mode: Literal["fixed", "observed_ml", "kelly_0_1", "kelly_0_25"] = "fixed"
    base_stake: float = Field(default=1.0, gt=0)
    max_stake: float | None = Field(default=None, gt=0)
    initial_bankroll: float = Field(default=100.0, gt=0)
    min_samples: int = Field(default=0, ge=0)
    min_h2h: int = Field(default=0, ge=0)
    min_odds: float = Field(default=1.01, gt=1)
    max_odds: float = Field(default=100.0, gt=1)
    min_probability: float = Field(default=0.0, ge=0, le=1)
    max_probability: float = Field(default=1.0, ge=0, le=1)
    min_match_confidence: float = Field(default=0.90, ge=0, le=1)
    stale_after_seconds: float = Field(default=120.0, gt=0)
    min_alert_lead_seconds: float = Field(default=15.0, ge=0, le=600)
    suspicious_edge_percent: float = Field(default=50.0, gt=0)
    allowed_markets: tuple[str, ...] = ()
    allowed_tournaments: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_consistent_ranges(self) -> Self:
        if self.min_odds > self.max_odds:
            raise ValueError("min_odds cannot exceed max_odds")
        if self.min_probability > self.max_probability:
            raise ValueError("min_probability cannot exceed max_probability")
        if self.alerts_enabled and not self.prediction_enabled:
            raise ValueError("alerts_enabled requires prediction_enabled")
        return self

    def backtest_config(self) -> BacktestConfig:
        return BacktestConfig(
            safety_multiplier=self.safety_multiplier,
            filters=SignalFilters(
                min_samples=self.min_samples,
                min_h2h=self.min_h2h,
                min_odds=self.min_odds,
                max_odds=self.max_odds,
                min_probability=self.min_probability,
                max_probability=self.max_probability,
                allowed_markets=frozenset(self.allowed_markets),
                allowed_tournaments=frozenset(self.allowed_tournaments),
                min_match_confidence=self.min_match_confidence,
                stale_after=timedelta(seconds=self.stale_after_seconds),
                suspicious_edge_percent=self.suspicious_edge_percent,
            ),
            stake_mode=self.stake_mode,
            base_stake=self.base_stake,
            initial_bankroll=self.initial_bankroll,
            max_stake=self.max_stake,
        )


class StrategiesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategies: dict[str, StrategySettings]


def load_strategies(path: Path) -> StrategiesSettings:
    if not path.exists():
        raise FileNotFoundError(f"Strategy configuration does not exist: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return StrategiesSettings.model_validate(payload)
