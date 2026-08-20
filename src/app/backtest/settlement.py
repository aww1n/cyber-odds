from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
    stake: Decimal
    payout: Decimal
    profit: Decimal


def _decimal(value: Decimal | float | int | str, *, name: str) -> Decimal:
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not converted.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return converted


def _compare(
    left: Decimal | float | int,
    right: Decimal | float | int,
) -> SettlementResult:
    left_decimal = _decimal(left, name="left")
    right_decimal = _decimal(right, name="right")
    if left_decimal == right_decimal:
        return "return"
    return "win" if left_decimal > right_decimal else "loss"


def normalize_market_selection(
    selection: str,
    *,
    market: str,
    line: Decimal | float | int | None = None,
) -> str:
    """Return the canonical selection used by current storage contracts.

    Historical Fonbet rows used ``TB(2.5)``/``TM(2.5)`` (and their Cyrillic
    equivalents), while current rows keep ``over``/``under`` and the line in a
    separate column. Accept both representations when rebuilding derived data.
    """
    normalized_market = market.strip().lower()
    normalized = selection.strip().upper().replace(" ", "")
    if normalized_market == "total":
        if normalized in {"OVER", "UNDER"}:
            return normalized.lower()
        total = TOTAL.fullmatch(normalized)
        if total is None:
            raise ValueError(f"Unsupported total selection: {selection}")
        parsed_line = Decimal(total.group("line").replace(",", "."))
        if line is not None and parsed_line != _decimal(line, name="line"):
            raise ValueError(
                f"Total selection line {parsed_line} does not match line column {line}"
            )
        return "over" if total.group("kind").upper() in {"TB", "\u0422\u0411"} else "under"
    if normalized_market == "1x2":
        aliases = {
            "P1": "P1",
            "\u041f1": "P1",
            "P2": "P2",
            "\u041f2": "P2",
            "X": "X",
            "\u0425": "X",
        }
        try:
            return aliases[normalized]
        except KeyError as error:
            raise ValueError(f"Unsupported 1x2 selection: {selection}") from error
    return selection.strip()


def settle_market(
    selection: str,
    *,
    score1: int,
    score2: int,
    market: str | None = None,
    line: Decimal | float | int | None = None,
) -> SettlementResult:
    """Settle a bet using structured contract or legacy string parsing.

    Preferred modern API:
        settle_market("over", score1=2, score2=3, market="total", line=3.5)
        settle_market("P1", score1=2, score2=1, market="1x2")

    Legacy string-only API (backward compatibility):
        settle_market("TB(3.5)", score1=2, score2=3)
        settle_market("P1", score1=2, score2=1)
    """
    if score1 < 0 or score2 < 0:
        raise ValueError("scores must be non-negative")
    # Modern structured API path
    if market is not None:
        normalized_market = market.strip().lower()
        normalized_selection = normalize_market_selection(
            selection,
            market=normalized_market,
            line=line,
        ).lower()

        if normalized_market == "total":
            if line is None:
                raise ValueError("total market requires line parameter")
            decimal_line = _decimal(line, name="line")
            match_total = score1 + score2
            if normalized_selection == "over":
                return _compare(match_total, decimal_line)
            if normalized_selection == "under":
                return _compare(decimal_line, match_total)
            raise ValueError(f"Unsupported total selection: {selection}")

        if normalized_market == "1x2":
            if normalized_selection == "p1":
                return _compare(score1, score2) if score1 != score2 else "loss"
            if normalized_selection == "p2":
                return _compare(score2, score1) if score1 != score2 else "loss"
            if normalized_selection == "x":
                return "win" if score1 == score2 else "loss"
            raise ValueError(f"Unsupported 1x2 selection: {selection}")

        if normalized_market == "handicap":
            if line is None:
                raise ValueError("handicap market requires line parameter")
            decimal_line = _decimal(line, name="line")
            if decimal_line * 2 != (decimal_line * 2).to_integral_value():
                raise ValueError("quarter handicaps require split settlement and are not supported")
            if "f1" in normalized_selection or normalized_selection.startswith("1"):
                return _compare(Decimal(score1) + decimal_line, score2)
            if "f2" in normalized_selection or normalized_selection.startswith("2"):
                return _compare(Decimal(score2) + decimal_line, score1)
            raise ValueError(f"Unsupported handicap selection: {selection}")

        raise ValueError(f"Unsupported market: {market}")

    # Legacy string-only API (backward compatibility)
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
        parsed_line = Decimal(handicap.group("line").replace(",", "."))
        if parsed_line * 2 != (parsed_line * 2).to_integral_value():
            raise ValueError("quarter handicaps require split settlement and are not supported")
        if handicap.group("side") == "1":
            return _compare(Decimal(score1) + parsed_line, score2)
        return _compare(Decimal(score2) + parsed_line, score1)

    total = TOTAL.fullmatch(normalized)
    if total is not None:
        parsed_line = Decimal(total.group("line").replace(",", "."))
        match_total = score1 + score2
        kind = total.group("kind").upper()
        if kind in {"TB", "\u0422\u0411"}:
            return _compare(match_total, parsed_line)
        return _compare(parsed_line, match_total)
    raise ValueError(f"Unsupported market selection: {selection}")


def settle_bet(
    outcome: SettlementResult,
    *,
    stake: Decimal | float | int,
    odds: Decimal | float | int,
) -> SettledBet:
    decimal_stake = _decimal(stake, name="stake")
    decimal_odds = _decimal(odds, name="odds")
    if decimal_stake <= 0:
        raise ValueError("stake must be finite and greater than zero")
    if decimal_odds <= 1:
        raise ValueError("odds must be finite and greater than one")
    if outcome == "win":
        payout = decimal_stake * decimal_odds
    elif outcome == "return":
        payout = decimal_stake
    else:
        payout = Decimal("0")
    return SettledBet(outcome, decimal_stake, payout, payout - decimal_stake)
