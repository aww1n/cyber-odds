from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from app.backtest.engine import BacktestRun


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    predictions: int
    bets: int
    wins: int
    losses: int
    returns: int
    unsettled: int
    hit_rate_percent: Decimal
    total_stake: Decimal
    profit: Decimal
    roi_percent: Decimal
    yield_percent: Decimal
    average_odds: Decimal
    max_drawdown: Decimal
    longest_losing_streak: int
    brier_score: float | None
    log_loss: float | None


def calculate_backtest_metrics(run: BacktestRun) -> BacktestMetrics:
    bets = run.bets
    settled = [item for item in bets if item.settlement is not None]
    total_stake = sum(
        (item.settlement.stake for item in settled if item.settlement),
        Decimal("0"),
    )
    profit = sum(
        (item.settlement.profit for item in settled if item.settlement),
        Decimal("0"),
    )
    wins = sum(item.settlement is not None and item.settlement.outcome == "win" for item in bets)
    losses = sum(
        item.settlement is not None and item.settlement.outcome == "loss" for item in bets
    )
    returns = sum(
        item.settlement is not None and item.settlement.outcome == "return" for item in bets
    )
    decisive = wins + losses
    hit_rate = (
        Decimal("100") * Decimal(wins) / Decimal(decisive)
        if decisive
        else Decimal("0")
    )
    roi = Decimal("100") * profit / total_stake if total_stake else Decimal("0")
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    losing_streak = 0
    longest_losing_streak = 0
    for item in settled:
        assert item.settlement is not None
        cumulative += item.settlement.profit
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        if item.settlement.outcome == "loss":
            losing_streak += 1
            longest_losing_streak = max(longest_losing_streak, losing_streak)
        elif item.settlement.outcome == "win":
            losing_streak = 0

    binary = [
        (float(item.metrics.probability), 1.0 if item.settlement.outcome == "win" else 0.0)
        for item in settled
        if item.settlement is not None and item.settlement.outcome != "return"
    ]
    brier = (
        sum((probability - outcome) ** 2 for probability, outcome in binary) / len(binary)
        if binary
        else None
    )
    epsilon = 1e-15
    log_loss = (
        -sum(
            outcome * math.log(min(1 - epsilon, max(epsilon, probability)))
            + (1 - outcome)
            * math.log(min(1 - epsilon, max(epsilon, 1 - probability)))
            for probability, outcome in binary
        )
        / len(binary)
        if binary
        else None
    )
    return BacktestMetrics(
        predictions=len(run.predictions),
        bets=len(bets),
        wins=wins,
        losses=losses,
        returns=returns,
        unsettled=len(bets) - len(settled),
        hit_rate_percent=hit_rate,
        total_stake=total_stake,
        profit=profit,
        roi_percent=roi,
        yield_percent=roi,
        average_odds=(
            sum(
                (item.metrics.odds for item in bets),
                Decimal("0"),
            )
            / len(bets)
            if bets
            else Decimal("0")
        ),
        max_drawdown=max_drawdown,
        longest_losing_streak=longest_losing_streak,
        brier_score=brier,
        log_loss=log_loss,
    )
