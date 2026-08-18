from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.backtest import BacktestConfig, BacktestEngine, BacktestOpportunity
from app.backtest.metrics import calculate_backtest_metrics
from app.value.filters import SignalFilters

START = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _opportunity(
    event_id: int,
    *,
    probability: float = 0.6,
    odds: float = 2.0,
    score: tuple[int, int] = (2, 1),
    odds_received_at: datetime | None = None,
) -> BacktestOpportunity:
    return BacktestOpportunity(
        event_id=event_id,
        event_started_at=START + timedelta(minutes=10 * event_id),
        decision_at=START + timedelta(minutes=10 * event_id - 1),
        feature_cutoff_at=START + timedelta(minutes=10 * event_id - 1),
        odds_received_at=odds_received_at
        or START + timedelta(minutes=10 * event_id - 2),
        odds_snapshot_id=1000 + event_id,
        probability=probability,
        odds=odds,
        market="1X2",
        selection="P1",
        tournament="Liga-1",
        sample_size=50,
        h2h_samples=10,
        match_confidence=0.99,
        score1=score[0],
        score2=score[1],
    )


def test_backtest_saves_alerts_and_skips_and_settles_only_bets() -> None:
    run = BacktestEngine().run(
        [_opportunity(1), _opportunity(2, probability=0.4, odds=2.0)],
        BacktestConfig(base_stake=10, initial_bankroll=100),
    )

    assert len(run.predictions) == 2
    assert len(run.bets) == 1
    assert run.predictions[0].decision == "alert"
    assert run.predictions[0].settlement is not None
    assert run.predictions[1].decision == "skip"
    assert "below_minimum_odds" in run.predictions[1].filter_reasons
    assert run.final_bankroll == 110

    metrics = calculate_backtest_metrics(run)
    assert metrics.bets == 1
    assert metrics.wins == 1
    assert metrics.profit == 10
    assert metrics.roi_percent == 100


def test_suspicious_edge_is_blocked_and_flagged() -> None:
    run = BacktestEngine().run(
        [_opportunity(1, probability=0.9, odds=2.0)],
        BacktestConfig(filters=SignalFilters(suspicious_edge_percent=50)),
    )

    prediction = run.predictions[0]
    assert prediction.decision == "skip"
    assert "suspiciously_high_edge" in prediction.anomaly_flags


def test_backtest_rejects_future_odds_snapshot() -> None:
    item = _opportunity(1, odds_received_at=START + timedelta(minutes=10))

    with pytest.raises(ValueError, match="odds snapshot is from the future"):
        BacktestEngine().run([item], BacktestConfig())


def test_observed_ml_stake_uses_the_same_value_ratio() -> None:
    run = BacktestEngine().run(
        [_opportunity(1, probability=0.6, odds=2.0)],
        BacktestConfig(stake_mode="observed_ml", base_stake=10),
    )

    prediction = run.predictions[0]
    assert prediction.bet_multiplier == pytest.approx(2.0)
    assert prediction.suggested_stake == pytest.approx(20)
    assert prediction.settlement is not None
    assert prediction.settlement.stake == pytest.approx(20)
