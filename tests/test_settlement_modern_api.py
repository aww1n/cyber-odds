"""Tests for modern structured settlement API with normalized totals."""

import pytest

from app.backtest.settlement import settle_market


class TestModernTotalsAPI:
    """Test structured API for totals: selection=over/under + line=Decimal."""

    def test_over_wins_when_total_exceeds_line(self) -> None:
        result = settle_market("over", score1=3, score2=2, market="total", line=3.5)
        assert result == "win"

    def test_over_loses_when_total_below_line(self) -> None:
        result = settle_market("over", score1=1, score2=1, market="total", line=3.5)
        assert result == "loss"

    def test_under_wins_when_total_below_line(self) -> None:
        result = settle_market("under", score1=1, score2=1, market="total", line=3.5)
        assert result == "win"

    def test_under_loses_when_total_exceeds_line(self) -> None:
        result = settle_market("under", score1=3, score2=2, market="total", line=3.5)
        assert result == "loss"

    def test_over_return_when_total_equals_integer_line(self) -> None:
        result = settle_market("over", score1=2, score2=2, market="total", line=4.0)
        assert result == "return"

    def test_under_return_when_total_equals_integer_line(self) -> None:
        result = settle_market("under", score1=2, score2=2, market="total", line=4.0)
        assert result == "return"

    def test_case_insensitive_selection(self) -> None:
        result = settle_market("OVER", score1=3, score2=2, market="total", line=3.5)
        assert result == "win"
        result = settle_market("Under", score1=1, score2=1, market="total", line=3.5)
        assert result == "win"

    def test_structured_api_accepts_legacy_total_selection(self) -> None:
        result = settle_market(
            "TB(3.5)", score1=3, score2=2, market="total", line=3.5
        )
        assert result == "win"

    def test_structured_api_rejects_conflicting_legacy_line(self) -> None:
        with pytest.raises(ValueError, match="does not match"):
            settle_market(
                "TM(2.5)", score1=1, score2=1, market="total", line=3.5
            )

    def test_requires_line_for_totals(self) -> None:
        with pytest.raises(ValueError, match="total market requires line"):
            settle_market("over", score1=2, score2=2, market="total")


class TestModern1X2API:
    """Test structured API for 1x2."""

    def test_p1_wins(self) -> None:
        result = settle_market("p1", score1=2, score2=1, market="1x2")
        assert result == "win"

    def test_p1_loses_on_draw(self) -> None:
        result = settle_market("p1", score1=1, score2=1, market="1x2")
        assert result == "loss"

    def test_p2_wins(self) -> None:
        result = settle_market("p2", score1=1, score2=2, market="1x2")
        assert result == "win"

    def test_draw_wins(self) -> None:
        result = settle_market("x", score1=1, score2=1, market="1x2")
        assert result == "win"

    def test_case_insensitive(self) -> None:
        result = settle_market("P1", score1=2, score2=1, market="1x2")
        assert result == "win"


class TestModernHandicapAPI:
    """Test structured API for handicap."""

    def test_f1_positive_handicap(self) -> None:
        result = settle_market("f1", score1=2, score2=2, market="handicap", line=0.5)
        assert result == "win"

    def test_f2_negative_handicap(self) -> None:
        # F2(-1.5): team2 gets -1.5 handicap, so 1 + (-1.5) = -0.5 vs 3 = loss
        result = settle_market("f2", score1=3, score2=1, market="handicap", line=-1.5)
        assert result == "loss"
    
    def test_f2_positive_handicap_wins(self) -> None:
        # F2(+1.5): team2 gets +1.5 handicap, so 1 + 1.5 = 2.5 vs 2 = win
        result = settle_market("f2", score1=2, score2=1, market="handicap", line=1.5)
        assert result == "win"

    def test_handicap_return(self) -> None:
        result = settle_market("f1", score1=2, score2=1, market="handicap", line=-1.0)
        assert result == "return"

    def test_requires_line_for_handicap(self) -> None:
        with pytest.raises(ValueError, match="handicap market requires line"):
            settle_market("f1", score1=2, score2=1, market="handicap")


class TestBackwardCompatibility:
    """Legacy string API must still work."""

    def test_legacy_tb_format(self) -> None:
        result = settle_market("TB(3.5)", score1=3, score2=2)
        assert result == "win"

    def test_legacy_tm_format(self) -> None:
        result = settle_market("TM(3.5)", score1=1, score2=1)
        assert result == "win"

    def test_legacy_p1(self) -> None:
        result = settle_market("P1", score1=2, score2=1)
        assert result == "win"

    def test_legacy_handicap(self) -> None:
        result = settle_market("F1(-1)", score1=2, score2=1)
        assert result == "return"
