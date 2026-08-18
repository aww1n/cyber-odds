from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

SettlementResult = Literal["win", "loss", "return"]

HANDICAP = re.compile(
    r"^[F\u0424](?P<side>[12])\((?P<line>[+-]?\d+(?:[.,]\d+)?)\)$", re.I
)
TOTAL = re.compile(
    r"^(?P<kind>TB|TM|\u0422\u0411|\u0422\u041c)"
    r"\s*\(?\s*(?P<line>\d+(?:[.,]\d+)?)\s*\)?$",
    re.I,
)


@dataclass(frozen=True, slots=True)
class SettledBet:
    outcome: SettlementResult
    stake: float
    payout: float
    profit: float


def _compare(left: float, right: float) -> SettlementResult:
    if math.isclose(left, right, abs_tol=1e-9):
        return "return"
    return "win" if left > right else "loss"


def settle_market(selection: str, *, score1: int, score2: int) -> SettlementResult:
    if score1 < 0 or score2 < 0:
        raise ValueError("scores must be non-negative")
    normalized = selection.strip().upper().replace(" ", "")
    if normalized == "P1" or normalized == "\u041f1":
        return _compare(score1, score2) if score1 != score2 else "loss"
    if normalized == "P2" or normalized == "\u041f2":
        return _compare(score2, score1) if score1 != score2 else "loss"
    if normalized in {"X", "\u0425"}:
        return "win" if score1 == score2 else "loss"
    if normalized in {"1X", "1\u0425"}:
        return "win" if score1 >= score2 else "loss"
    if normalized in {"X2", "\u04252"}:
        return "win" if score2 >= score1 else "loss"
    if normalized == "12":
        return "win" if score1 != score2 else "loss"

    handicap = HANDICAP.fullmatch(normalized)
    if handicap is not None:
        line = float(handicap.group("line").replace(",", "."))
        if not math.isclose(line * 2, round(line * 2), abs_tol=1e-9):
            raise ValueError("quarter handicaps require split settlement and are not supported")
        if handicap.group("side") == "1":
            return _compare(score1 + line, score2)
        return _compare(score2 + line, score1)

    total = TOTAL.fullmatch(normalized)
    if total is not None:
        line = float(total.group("line").replace(",", "."))
        match_total = score1 + score2
        kind = total.group("kind").upper()
        if kind in {"TB", "\u0422\u0411"}:
            return _compare(match_total, line)
        return _compare(line, match_total)
    raise ValueError(f"Unsupported market selection: {selection}")


def settle_bet(
    outcome: SettlementResult,
    *,
    stake: float,
    odds: float,
) -> SettledBet:
    if not math.isfinite(stake) or stake <= 0:
        raise ValueError("stake must be finite and greater than zero")
    if not math.isfinite(odds) or odds <= 1:
        raise ValueError("odds must be finite and greater than one")
    if outcome == "win":
        payout = stake * odds
    elif outcome == "return":
        payout = stake
    else:
        payout = 0.0
    return SettledBet(outcome, stake, payout, payout - stake)
