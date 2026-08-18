from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.backtest.settlement import SettledBet, settle_bet, settle_market
from app.value.calculator import ValueMetrics, calculate_value
from app.value.filters import SignalFilters
from app.value.staking import (
    fixed_stake,
    fractional_kelly_stake,
    observed_ml_bet_multiplier,
    observed_ml_stake,
)

StakeMode = Literal["fixed", "observed_ml", "kelly_0_1", "kelly_0_25"]


@dataclass(frozen=True, slots=True)
class BacktestOpportunity:
    event_id: int
    event_started_at: datetime
    decision_at: datetime
    feature_cutoff_at: datetime
    odds_received_at: datetime
    odds_snapshot_id: int
    probability: float
    odds: float
    market: str
    selection: str
    tournament: str
    sample_size: int
    h2h_samples: int
    match_confidence: float
    score1: int | None
    score2: int | None


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    safety_multiplier: float = 1.0
    filters: SignalFilters = field(default_factory=SignalFilters)
    stake_mode: StakeMode = "fixed"
    base_stake: float = 1.0
    initial_bankroll: float = 100.0
    max_stake: float | None = None


@dataclass(frozen=True, slots=True)
class SimulatedPrediction:
    event_id: int
    odds_snapshot_id: int
    decision_at: datetime
    metrics: ValueMetrics
    minimum_odds: float
    decision: Literal["alert", "skip"]
    filter_reasons: tuple[str, ...]
    anomaly_flags: tuple[str, ...]
    bet_multiplier: float | None
    suggested_stake: float | None
    settlement: SettledBet | None


@dataclass(frozen=True, slots=True)
class BacktestRun:
    predictions: tuple[SimulatedPrediction, ...]
    final_bankroll: float

    @property
    def bets(self) -> tuple[SimulatedPrediction, ...]:
        return tuple(item for item in self.predictions if item.decision == "alert")


class BacktestEngine:
    def run(
        self,
        opportunities: list[BacktestOpportunity],
        config: BacktestConfig,
    ) -> BacktestRun:
        bankroll = config.initial_bankroll
        predictions: list[SimulatedPrediction] = []
        ordered = sorted(opportunities, key=lambda item: (item.decision_at, item.event_id))
        for item in ordered:
            self._validate_timestamps(item)
            metrics = calculate_value(item.probability, item.odds)
            minimum_odds = metrics.fair_odds * config.safety_multiplier
            reasons, anomalies = self._filter(item, metrics, config.filters)
            if item.odds < minimum_odds:
                reasons.append("below_minimum_odds")
            decision: Literal["alert", "skip"] = "skip" if reasons else "alert"
            settlement = None
            bet_multiplier = None
            suggested_stake = None
            if decision == "alert":
                stake, bet_multiplier = self._stake(item, metrics, bankroll, config)
                if stake <= 0:
                    decision = "skip"
                    reasons.append("zero_stake")
                else:
                    suggested_stake = stake
                    if item.score1 is not None and item.score2 is not None:
                        outcome = settle_market(
                            item.selection,
                            score1=item.score1,
                            score2=item.score2,
                        )
                        settlement = settle_bet(outcome, stake=stake, odds=item.odds)
                        bankroll += settlement.profit
            predictions.append(
                SimulatedPrediction(
                    event_id=item.event_id,
                    odds_snapshot_id=item.odds_snapshot_id,
                    decision_at=item.decision_at,
                    metrics=metrics,
                    minimum_odds=minimum_odds,
                    decision=decision,
                    filter_reasons=tuple(reasons),
                    anomaly_flags=tuple(anomalies),
                    bet_multiplier=bet_multiplier,
                    suggested_stake=suggested_stake,
                    settlement=settlement,
                )
            )
        return BacktestRun(tuple(predictions), bankroll)

    @staticmethod
    def _validate_timestamps(item: BacktestOpportunity) -> None:
        if item.feature_cutoff_at > item.decision_at:
            raise ValueError("feature cutoff is after decision timestamp")
        if item.odds_received_at > item.decision_at:
            raise ValueError("odds snapshot is from the future")
        if item.decision_at > item.event_started_at:
            raise ValueError("decision timestamp is after event start")

    @staticmethod
    def _filter(
        item: BacktestOpportunity,
        metrics: ValueMetrics,
        filters: SignalFilters,
    ) -> tuple[list[str], list[str]]:
        reasons: list[str] = []
        anomalies: list[str] = []
        if item.sample_size < filters.min_samples:
            reasons.append("insufficient_sample")
        if item.h2h_samples < filters.min_h2h:
            reasons.append("insufficient_h2h")
        if not filters.min_odds <= item.odds <= filters.max_odds:
            reasons.append("odds_out_of_range")
        if not filters.min_probability <= item.probability <= filters.max_probability:
            reasons.append("probability_out_of_range")
        if filters.allowed_markets and item.market not in filters.allowed_markets:
            reasons.append("market_not_allowed")
        if filters.allowed_tournaments and item.tournament not in filters.allowed_tournaments:
            reasons.append("tournament_not_allowed")
        if item.match_confidence < filters.min_match_confidence:
            reasons.append("event_match_confidence")
            anomalies.append("incorrect_event_mapping")
        if item.decision_at - item.odds_received_at > filters.stale_after:
            reasons.append("stale_odds")
            anomalies.append("stale_odds")
        if metrics.value_percent > filters.suspicious_edge_percent:
            reasons.append("suspiciously_high_edge")
            anomalies.append("suspiciously_high_edge")
        return reasons, anomalies

    @staticmethod
    def _stake(
        item: BacktestOpportunity,
        metrics: ValueMetrics,
        bankroll: float,
        config: BacktestConfig,
    ) -> tuple[float, float]:
        if config.stake_mode == "fixed":
            return fixed_stake(config.base_stake), 1.0
        if config.stake_mode == "observed_ml":
            multiplier = observed_ml_bet_multiplier(metrics.value_ratio)
            return observed_ml_stake(config.base_stake, metrics.value_ratio), multiplier
        fraction = 0.1 if config.stake_mode == "kelly_0_1" else 0.25
        stake = fractional_kelly_stake(
            bankroll,
            probability=item.probability,
            odds=item.odds,
            fraction=fraction,
            max_stake=config.max_stake,
        )
        return stake, stake / config.base_stake
