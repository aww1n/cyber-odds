from __future__ import annotations

import pytest

from app.backtest.settlement import settle_bet, settle_market


@pytest.mark.parametrize(
    ("selection", "score", "expected"),
    [
        ("P1", (3, 1), "win"),
        ("P1", (1, 1), "loss"),
        ("X", (2, 2), "win"),
        ("P2", (1, 3), "win"),
        ("1X", (1, 1), "win"),
        ("X2", (3, 1), "loss"),
        ("12", (0, 0), "loss"),
    ],
)
def test_one_x_two_and_double_chance(
    selection: str,
    score: tuple[int, int],
    expected: str,
) -> None:
    assert settle_market(selection, score1=score[0], score2=score[1]) == expected


@pytest.mark.parametrize(
    ("selection", "score", "expected"),
    [
        ("F1(0)", (2, 2), "return"),
        ("\u04242(0)", (1, 2), "win"),
        ("F1(-1)", (2, 1), "return"),
        ("F2(+1.5)", (2, 1), "win"),
        ("F1(-1.5)", (2, 1), "loss"),
    ],
)
def test_draw_no_bet_and_handicaps(
    selection: str,
    score: tuple[int, int],
    expected: str,
) -> None:
    assert settle_market(selection, score1=score[0], score2=score[1]) == expected


@pytest.mark.parametrize(
    ("selection", "score", "expected"),
    [
        ("TB 2.5", (2, 1), "win"),
        ("\u0422\u041c(3.5)", (2, 1), "win"),
        ("TB 3", (2, 1), "return"),
        ("TM 3", (2, 1), "return"),
        ("\u0422\u0411 4.5", (2, 1), "loss"),
    ],
)
def test_totals(selection: str, score: tuple[int, int], expected: str) -> None:
    assert settle_market(selection, score1=score[0], score2=score[1]) == expected


def test_quarter_handicap_is_rejected_until_split_settlement_exists() -> None:
    with pytest.raises(ValueError, match="quarter handicaps"):
        settle_market("F1(-0.25)", score1=2, score2=1)


def test_payout_handles_win_loss_and_return() -> None:
    assert settle_bet("win", stake=10, odds=2.5).profit == 15
    assert settle_bet("loss", stake=10, odds=2.5).profit == -10
    assert settle_bet("return", stake=10, odds=2.5).profit == 0
