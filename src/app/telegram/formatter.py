from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class SignalAlertView:
    source_tag: str
    sport: str
    started_at: datetime
    tournament: str
    event_format: str | None
    participant1: str
    participant2: str
    market: str
    selection: str
    line: Decimal | None
    calculation_odds: Decimal
    display_odds: Decimal | None
    probability: Decimal
    fair_odds: Decimal
    value_percent: Decimal
    minimum_odds: Decimal
    bet_multiplier: Decimal | None
    suggested_stake: Decimal | None
    sample_size: int | None
    model: str
    external_id: str
    bankroll_at_signal: Decimal | None = None
    stake_percent: Decimal | None = None
    stake_amount: Decimal | None = None
    game: str | None = None
    team1: str | None = None
    team2: str | None = None
    corridor: dict[str, object] | None = None
    signal_id: int | None = None


@dataclass(frozen=True, slots=True)
class SettlementView:
    external_id: str
    participant1: str
    participant2: str
    score1: int
    score2: int
    selection: str
    odds: Decimal
    stake: Decimal
    payout: Decimal
    outcome: str
    profit: Decimal
    market: str | None = None
    line: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ModelStatsView:
    model: str
    signals: int
    settled: int
    profit: Decimal
    roi_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class DailyStatsView:
    label: str
    signals: int
    wins: int
    losses: int
    returns: int
    pending: int
    total_stake: Decimal
    profit: Decimal
    roi_percent: Decimal | None
    average_odds: Decimal | None
    models: tuple[ModelStatsView, ...] = ()


@dataclass(frozen=True, slots=True)
class ParserHealthView:
    source: str
    status: str
    finished_at: datetime | None
    latency_ms: int | None
    events_parsed: int
    events_rejected: int
    last_error: str | None = None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: Decimal, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _selection_label(
    selection: str,
    participant1: str,
    participant2: str,
    line: Decimal | None = None,
) -> str:
    labels = {
        "P1": f"П1 — {participant1}",
        "X": "Ничья",
        "P2": f"П2 — {participant2}",
    }
    if selection.upper() in {"OVER", "UNDER"} and line is not None:
        line_str = _decimal(line, 3).rstrip("0").rstrip(".")
        if selection.upper() == "OVER":
            return f"ТБ {line_str}"
        return f"ТМ {line_str}"
    return labels.get(selection, selection)


def _money(value: Decimal) -> str:
    return f"{value:,.0f}".replace(",", " ")


def format_signal_alert(
    view: SignalAlertView,
    *,
    timezone: str = "Europe/Moscow",
    now: datetime | None = None,
) -> str:
    started_at = _aware_utc(view.started_at)
    local_time = started_at.astimezone(ZoneInfo(timezone))
    current = _aware_utc(now or datetime.now(UTC))
    minutes_to_start = max(0, int((started_at - current).total_seconds() / 60))
    time_display = (
        f"⏱ До матча: {minutes_to_start} мин"
        if minutes_to_start > 0
        else "⏱ Начинается сейчас"
    )

    icon = "🏒" if "hockey" in view.sport.casefold() else "⚽"
    sport_label = "Киберхоккей" if icon == "🏒" else "Киберфутбол"
    shown_odds = view.display_odds or view.calculation_odds
    bet_label = _selection_label(
        view.selection,
        view.participant1,
        view.participant2,
        view.line,
    )
    market_display_map = {
        "1x2": "Основной исход",
        "total": "Тотал",
        "handicap": "Гандикап",
    }
    market_display = market_display_map.get(view.market.lower(), view.market)

    is_total = view.selection.lower() in {"over", "under"}
    line_display = ""
    if view.line is not None and not is_total:
        line_display = f" • линия {_decimal(view.line, 3).rstrip('0').rstrip('.')}"

    format_suffix = f" • {escape(view.event_format)}" if view.event_format else ""
    sample = f"{view.sample_size} матчей" if view.sample_size is not None else "нет данных"

    bankroll_display = ""
    if (
        view.bankroll_at_signal is not None
        and view.stake_percent is not None
        and view.stake_amount is not None
    ):
        bankroll_display = (
            f"\n\n🏦 Банк: {_money(view.bankroll_at_signal)} ₽\n"
            f"📌 Ставка: {_decimal(view.stake_percent, 1)}% банка\n"
            f"💵 Сумма: {_money(view.stake_amount)} ₽"
        )
    elif view.suggested_stake is not None:
        bankroll_display = f"\n💵 Ставка по стратегии: {_decimal(view.suggested_stake, 2)}"

    corridor = view.corridor or {"status": "insufficient"}
    if corridor.get("status") == "insufficient":
        corridor_display = "\n\n📊 Коридор:\nНедостаточно исторических наблюдений"
    else:
        odds_min = Decimal(str(corridor["odds_min"]))
        odds_max = Decimal(str(corridor["odds_max"]))
        win_rate = Decimal(str(corridor["win_rate"])) * 100
        roi = Decimal(str(corridor["roi_percent"]))
        corridor_contract = f"{bet_label}\n" if is_total else ""
        corridor_display = (
            "\n\n📊 Коридор:\n"
            f"{corridor_contract}"
            f"Кэф {_decimal(odds_min)}–{_decimal(odds_max)}\n"
            f"Выборка: {corridor['sample_size']}\n"
            f"Winrate: {_decimal(win_rate, 1)}%\n"
            f"ROI: {roi:+.1f}%"
        )

    path_nodes = [view.source_tag.upper()]
    if view.game:
        path_nodes.append(view.game)
    elif view.sport:
        path_nodes.append(sport_label)
    if view.tournament:
        path_nodes.append(view.tournament)
    path_nodes.extend(
        (
            f"{view.participant1} — {view.participant2}",
            market_display,
            bet_label,
        )
    )
    path = "\n\n📍 <b>ГДЕ СТАВИТЬ:</b>\n" + "\n→ ".join(
        escape(node) for node in path_nodes
    )
    teams = (
        f"\n🌍 {escape(view.team1)} — {escape(view.team2)}"
        if view.team1 and view.team2
        else ""
    )
    identity = f"Event: {escape(view.external_id)}"
    if view.signal_id is not None:
        identity += f" / Signal: {view.signal_id}"

    return (
        "🔥 <b>СИГНАЛ НА СТАВКУ</b>\n\n"
        f"{time_display}\n"
        f"🕐 Начало: {local_time:%d.%m.%Y %H:%M}\n\n"
        f"{icon} <b>{sport_label}</b>\n"
        f"🏆 {escape(view.tournament)}{format_suffix}\n"
        f"👥 <b>{escape(view.participant1)}</b> — <b>{escape(view.participant2)}</b>"
        f"{teams}\n\n"
        f"🎯 <b>Ставка: {escape(bet_label)}</b>\n"
        f"🏦 Букмекер: <b>{escape(view.source_tag.upper())}</b>\n"
        f"📌 Рынок: {escape(market_display)}{line_display}\n"
        f"💰 <b>Коэффициент: {_decimal(shown_odds)}</b>\n\n"
        f"📊 Вероятность модели: {_decimal(view.probability * 100)}%\n"
        f"🎲 Fair odds: {_decimal(view.fair_odds)}\n"
        f"📈 Value: <b>{view.value_percent:+.1f}%</b>\n"
        f"🛡 Минимальный коэффициент: {_decimal(view.minimum_odds)}\n"
        f"📚 Выборка: {sample}"
        f"{corridor_display}"
        f"{bankroll_display}"
        f"{path}\n\n"
        f"🤖 {escape(view.model)}\n"
        f"🆔 {identity}"
    )


def format_settlement(view: SettlementView) -> str:
    if view.outcome == "win":
        title = "✅ <b>ПРОГНОЗ СЫГРАЛ</b>"
    elif view.outcome == "loss":
        title = "❌ <b>ПРОГНОЗ НЕ СЫГРАЛ</b>"
    elif view.outcome == "return":
        title = "↩️ <b>ВОЗВРАТ СТАВКИ</b>"
    else:
        title = "🚫 <b>СТАВКА АННУЛИРОВАНА</b>"
    bet_label = _selection_label(
        view.selection,
        view.participant1,
        view.participant2,
        view.line,
    )
    return (
        f"{title}\n\n"
        f"👥 {escape(view.participant1)} — {escape(view.participant2)}\n"
        f"🏁 Итоговый счёт: <b>{view.score1}:{view.score2}</b>\n\n"
        f"🎯 Прогноз: <b>{escape(bet_label)}</b>\n"
        f"💰 Коэффициент: {_decimal(view.odds)}\n"
        f"💵 Ставка: {_decimal(view.stake)}\n"
        f"💳 Выплата: {_decimal(view.payout)}\n"
        f"📊 PnL: <b>{view.profit:+.2f}</b>\n\n"
        f"🆔 {escape(view.external_id)}"
    )


def format_daily_stats(view: DailyStatsView) -> str:
    roi = f"{view.roi_percent:+.1f}%" if view.roi_percent is not None else "n/a"
    odds = _decimal(view.average_odds) if view.average_odds is not None else "n/a"
    lines = [
        f"{escape(view.label)}:",
        f"Signals: {view.signals}",
        f"Wins: {view.wins}",
        f"Losses: {view.losses}",
        f"Returns: {view.returns}",
        f"Pending: {view.pending}",
        "",
        f"ROI: {roi}",
        f"Profit: {view.profit:+.2f}",
        f"Average odds: {odds}",
    ]
    for model in view.models:
        model_roi = (
            f"{model.roi_percent:+.1f}%" if model.roi_percent is not None else "n/a"
        )
        lines.extend(
            (
                "",
                f"{escape(model.model)}:",
                f"{model.signals} signals / {model.settled} settled",
                f"ROI {model_roi}, PnL {model.profit:+.2f}",
            )
        )
    return "\n".join(lines)


def format_parser_health(
    rows: tuple[ParserHealthView, ...],
    *,
    now: datetime,
    freshness_seconds: dict[str, float] | None = None,
) -> str:
    if not rows:
        return "Нет запусков парсеров."
    current = _aware_utc(now)
    output = ["Parsers:"]
    for row in rows:
        finished = _aware_utc(row.finished_at) if row.finished_at else None
        age_seconds = (current - finished).total_seconds() if finished else None
        fresh_for = (freshness_seconds or {}).get(row.source, 120.0)
        if (
            row.status == "success"
            and age_seconds is not None
            and age_seconds <= fresh_for
        ):
            icon = "✅"
        elif row.status in {"success", "partial"}:
            icon = "⚠️"
        else:
            icon = "❌"
        age = _format_age(age_seconds)
        output.append(
            f"{escape(row.source)} {icon} {age}; parsed {row.events_parsed}, "
            f"rejected {row.events_rejected}"
        )
    return "\n".join(output)


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} sec ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"
