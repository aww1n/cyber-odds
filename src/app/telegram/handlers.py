from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.telegram.formatter import format_daily_stats, format_parser_health
from app.telegram.repository import TelegramRepository


class AdminOnly(BaseFilter):
    def __init__(self, admin_ids: tuple[int, ...]) -> None:
        self._admin_ids = frozenset(admin_ids)

    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in self._admin_ids


def _today_start_utc(timezone: str = "Europe/Moscow") -> datetime:
    local_now = datetime.now(ZoneInfo(timezone))
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _limit(command: CommandObject, *, default: int = 10) -> int:
    if not command.args:
        return default
    try:
        return max(1, min(50, int(command.args.strip())))
    except ValueError:
        return default


def _backtest_summary(output_dir: Path) -> str:
    lines = ["Последний probability backtest:"]
    for name, label in (
        ("baseline_backtest.json", "Best baseline"),
        ("ml_v1_backtest.json", "Logistic ML v1"),
    ):
        path = output_dir / name
        if not path.exists():
            lines.append(f"{label}: отчёт ещё не создан")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if name.startswith("baseline"):
            metrics_by_model = payload.get("out_of_sample", {})
            best_name, metrics = min(
                metrics_by_model.items(),
                key=lambda item: item[1]["log_loss"],
            )
            label = f"{label} ({best_name})"
        else:
            metrics = payload["out_of_sample"]
        lines.append(
            f"{escape(label)}: LogLoss {metrics['log_loss']:.4f}, "
            f"Brier {metrics['brier_score']:.4f}, n={metrics['predictions']}"
        )
    lines.extend(("", "ROI не считается без подтверждённых исторических рынков Fonbet."))
    return "\n".join(lines)


def build_router(
    *,
    admin_ids: tuple[int, ...],
    sessions: async_sessionmaker[AsyncSession],
    research_output_dir: Path = Path("research/output"),
    parser_freshness_seconds: dict[str, float] | None = None,
    bot: Bot | None = None,
    alert_chat_id: int | None = None,
) -> Router:
    router = Router(name="telegram-admin")
    router.message.filter(AdminOnly(admin_ids), F.chat.type == "private")
    repository = TelegramRepository()

    @router.message(Command("start", "status"))
    async def status(message: Message) -> None:
        async with sessions() as session:
            view = await repository.system_status(session)
        last_created = (
            view.last_alert_created_at.strftime("%d.%m %H:%M UTC")
            if view.last_alert_created_at
            else "—"
        )
        last_sent = (
            view.last_alert_sent_at.strftime("%d.%m %H:%M UTC")
            if view.last_alert_sent_at
            else "—"
        )
        await message.answer(
            "✅ Система работает.\n"
            f"Прогнозов: {view.predictions}\n"
            f"Алертов в очереди: {view.pending_alerts}\n"
            f"Алертов доставлено: {view.sent_alerts}\n"
            f"Последний alert создан: {last_created}\n"
            f"Последний alert отправлен: {last_sent}\n"
            f"Результатов ждут отправки: {view.pending_settlements}\n"
            f"Ошибок парсеров: {view.parsers_with_errors}\n\n"
            "Ставки автоматически не размещаются; бот отправляет аналитические сигналы."
        )

    @router.message(Command("testsignal"))
    async def testsignal(message: Message) -> None:
        """Send a test alert through Telegram without touching predictions."""
        text = (
            "🧪 <b>ТЕСТОВЫЙ СИГНАЛ</b>\n\n"
            "⚽ Киберфутбол\n"
            "Игрок A 🆚 Игрок B\n\n"
            "🎯 <b>Ставка:</b> П1\n"
            "💰 Коэффициент: 2.50\n"
            "📊 Модель: 58.4%\n"
            "📈 Value: +21.6%\n\n"
            "⚠️ Реальная ставка не создаётся. Это проверка Telegram доставки."
        )
        if bot is None or alert_chat_id is None:
            await message.answer("❌ Telegram test publisher не настроен")
            return
        try:
            sent = await bot.send_message(chat_id=alert_chat_id, text=text)
            await message.answer(
                f"✅ Тестовый сигнал отправлен\nmessage_id: {sent.message_id}"
            )
        except Exception as exc:
            await message.answer(f"❌ Ошибка отправки: {exc}")

    @router.message(Command("stats", "today"))
    async def stats(message: Message) -> None:
        async with sessions() as session:
            view = await repository.stats(
                session,
                since=_today_start_utc(),
                label="Today",
            )
        await message.answer(format_daily_stats(view))

    @router.message(Command("data"))
    async def data(message: Message) -> None:
        async with sessions() as session:
            totals = await repository.data_totals(session)
        await message.answer(
            "📊 Собранные данны:\n"
            f"События: {totals.events:,}\n"
            f"Результаты: {totals.results:,}\n"
            f"Снимки коэффициентов: {totals.odds_snapshots:,}\n"
            f"Игроки: {totals.players:,}\n"
            f"Турниры: {totals.tournaments:,}\n"
            f"Прогнозы: {totals.predictions:,}"
        )

    @router.message(Command("models"))
    async def models(message: Message) -> None:
        async with sessions() as session:
            view = await repository.stats(session, since=None, label="All models")
        if not view.models:
            await message.answer("Сохранённых модельных сигналов пока нет.")
            return
        await message.answer(format_daily_stats(view))

    @router.message(Command("model"))
    async def model(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Использование: /model model_name")
            return
        model_name = command.args.strip()
        async with sessions() as session:
            view = await repository.stats(
                session,
                since=None,
                label=f"Model {model_name}",
                model_name=model_name,
            )
        await message.answer(format_daily_stats(view))

    @router.message(Command("signals"))
    async def signals(message: Message, command: CommandObject) -> None:
        async with sessions() as session:
            rows = await repository.recent_signals(session, limit=_limit(command))
        if not rows:
            await message.answer("Сигналов пока нет.")
            return
        await message.answer(
            "Recent signals:\n"
            + "\n".join(
                f"{item.created_at:%m-%d %H:%M} {escape(item.decision.upper())} "
                f"{escape(item.model)} {escape(item.selection)} @{item.odds:.2f} "
                f"value {item.value_percent:+.1f}% [{escape(item.external_id)}]"
                for item in rows
            )
        )

    @router.message(Command("results"))
    async def results(message: Message, command: CommandObject) -> None:
        async with sessions() as session:
            rows = await repository.recent_results(session, limit=_limit(command))
        if not rows:
            await message.answer("Рассчитанных результатов пока нет.")
            return
        await message.answer(
            "Recent results:\n"
            + "\n".join(
                f"{item.settled_at:%m-%d %H:%M} {escape(item.outcome.upper())} "
                f"{item.score1}:{item.score2} PnL {item.profit:+.2f} "
                f"[{escape(item.external_id)}]"
                for item in rows
            )
        )

    @router.message(Command("bank"))
    async def bank(message: Message) -> None:
        async with sessions() as session:
            view = await repository.stats(session, since=None, label="Analytics bank")
        await message.answer(
            "Ставки автоматически не размещаются; это аналитический settlement PnL.\n\n"
            + format_daily_stats(view)
        )

    @router.message(Command("backtest"))
    async def backtest(message: Message) -> None:
        await message.answer(_backtest_summary(research_output_dir))

    @router.message(Command("parsers"))
    async def parsers(message: Message) -> None:
        async with sessions() as session:
            rows = await repository.parser_health(session)
        await message.answer(
            format_parser_health(
                rows,
                now=datetime.now(UTC),
                freshness_seconds=parser_freshness_seconds,
            )
        )

    @router.message(Command("errors"))
    async def errors(message: Message) -> None:
        async with sessions() as session:
            rows = await repository.recent_errors(session)
        if not rows:
            await message.answer("Ошибок парсеров не сохранено.")
            return
        await message.answer(
            "Recent parser errors:\n"
            + "\n".join(
                f"{escape(item.source)}: {escape((item.last_error or item.status)[:300])}"
                for item in rows
            )
        )

    return router
