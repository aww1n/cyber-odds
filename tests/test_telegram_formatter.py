from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.telegram.formatter import (
    DailyStatsView,
    ModelStatsView,
    ParserHealthView,
    SettlementView,
    SignalAlertView,
    format_daily_stats,
    format_parser_health,
    format_settlement,
    format_signal_alert,
)


def test_alert_keeps_calculation_snapshot_distinct_from_display_odds() -> None:
    text = format_signal_alert(
        SignalAlertView(
            source_tag="fonbet",
            sport="football",
            started_at=datetime(2026, 8, 17, 16, 45, tzinfo=UTC),
            tournament="FC 26. H2H Liga-1",
            event_format="2x4 min",
            participant1="Italy (BATUYKA)",
            participant2="Spain (PRITISTREET)",
            market="handicap",
            selection="F2",
            line=Decimal("0"),
            calculation_odds=Decimal("2.15"),
            display_odds=Decimal("2.18"),
            probability=Decimal("0.5744"),
            fair_odds=Decimal("1.74"),
            value_percent=Decimal("23.5"),
            minimum_odds=Decimal("1.74"),
            bet_multiplier=Decimal("2.35"),
            suggested_stake=Decimal("7000"),
            sample_size=18,
            model="FON_ml_v1",
            external_id="h2h_ef_123",
        )
    )

    assert "Коэффициент: 2.18" in text
    assert "Вероятность модели: 57.44%" in text
    assert "Ставка: F2" in text
    assert "Букмекер: <b>FONBET</b>" in text
    assert "Выборка: 18 матчей" in text
    assert "+23.5%" in text
    assert "h2h_ef_123" in text


def test_total_alert_contains_line_bankroll_corridor_and_exact_path() -> None:
    now = datetime(2026, 8, 17, 16, 40, tzinfo=UTC)
    text = format_signal_alert(
        SignalAlertView(
            source_tag="fonbet",
            sport="football",
            started_at=now + timedelta(minutes=8),
            tournament="UEL",
            event_format=None,
            participant1="A",
            participant2="B",
            market="total",
            selection="over",
            line=Decimal("3.5"),
            calculation_odds=Decimal("1.92"),
            display_odds=None,
            probability=Decimal("0.584"),
            fair_odds=Decimal("1.7123"),
            value_percent=Decimal("12.128"),
            minimum_odds=Decimal("1.80"),
            bet_multiplier=Decimal("1"),
            suggested_stake=Decimal("1"),
            sample_size=950,
            model="poisson_goals:totals_v1",
            external_id="event-total",
            bankroll_at_signal=Decimal("100000"),
            stake_percent=Decimal("1.5"),
            stake_amount=Decimal("1500"),
            game="FC26",
            corridor={
                "status": "confirm",
                "odds_min": "1.75",
                "odds_max": "2.00",
                "sample_size": 712,
                "win_rate": "0.567",
                "roi_percent": "4.3",
            },
        ),
        now=now,
    )

    assert "До матча: 8 мин" in text
    assert "Ставка: ТБ 3.5" in text
    assert "Банк: 100 000 ₽" in text
    assert "Сумма: 1 500 ₽" in text
    assert "Выборка: 712" in text
    assert "📊 Коридор:\nТБ 3.5" in text
    assert "FONBET\n→ FC26\n→ UEL\n→ A — B\n→ Тотал\n→ ТБ 3.5" in text


def test_settlement_and_daily_stats_include_returns_and_virtual_pnl() -> None:
    settlement = format_settlement(
        SettlementView(
            external_id="event-1",
            participant1="A",
            participant2="B",
            score1=2,
            score2=2,
            selection="X",
            odds=Decimal("3.10"),
            stake=Decimal("100"),
            payout=Decimal("100"),
            outcome="return",
            profit=Decimal("0"),
        )
    )
    stats = format_daily_stats(
        DailyStatsView(
            label="Today",
            signals=3,
            wins=1,
            losses=1,
            returns=1,
            pending=0,
            total_stake=Decimal("300"),
            profit=Decimal("25"),
            roi_percent=Decimal("8.333"),
            average_odds=Decimal("2.31"),
            models=(
                ModelStatsView("baseline", 3, 3, Decimal("25"), Decimal("8.333")),
            ),
        )
    )

    assert "ВОЗВРАТ СТАВКИ" in settlement
    assert "Прогноз: <b>Ничья</b>" in settlement
    assert "Returns: 1" in stats
    assert "ROI: +8.3%" in stats
    assert "baseline:" in stats


def test_parser_health_distinguishes_fresh_stale_and_error() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    text = format_parser_health(
        (
            ParserHealthView("fonbet", "success", now - timedelta(seconds=3), 10, 5, 0),
            ParserHealthView("uel", "success", now - timedelta(minutes=4), 20, 5, 0),
            ParserHealthView("h2h", "error", now - timedelta(seconds=20), 30, 0, 1),
        ),
        now=now,
    )

    assert "fonbet ✅ 3 sec ago" in text
    assert "uel ⚠️ 4 min ago" in text
    assert "h2h ❌ 20 sec ago" in text


def test_parser_health_uses_source_specific_collection_interval() -> None:
    now = datetime(2026, 8, 17, 14, 17, tzinfo=UTC)
    text = format_parser_health(
        (
            ParserHealthView(
                "uel_ef", "success", now - timedelta(minutes=4), 72, 72, 0
            ),
        ),
        now=now,
        freshness_seconds={"uel_ef": 750.0},
    )

    assert "uel_ef ✅ 4 min ago" in text
