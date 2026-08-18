from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.config import Settings
from app.database import build_async_engine, build_session_factory
from app.telegram.handlers import build_router
from app.telegram.publisher import TelegramPublisher

LOGGER = logging.getLogger(__name__)

ADMIN_COMMANDS = [
    BotCommand(command="status", description="Состояние системы"),
    BotCommand(command="data", description="Объём собранных данных"),
    BotCommand(command="stats", description="Статистика за сегодня"),
    BotCommand(command="today", description="Сигналы за сегодня"),
    BotCommand(command="models", description="Статистика моделей"),
    BotCommand(command="model", description="Статистика выбранной модели"),
    BotCommand(command="signals", description="Последние сигналы"),
    BotCommand(command="results", description="Последние результаты"),
    BotCommand(command="bank", description="Виртуальный PnL"),
    BotCommand(command="backtest", description="Последний backtest"),
    BotCommand(command="parsers", description="Здоровье источников"),
    BotCommand(command="errors", description="Ошибки источников"),
    BotCommand(command="testsignal", description="Тест отправки сигнала"),
]


async def run_telegram_bot(settings: Settings) -> None:
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if not settings.admin_ids:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain at least one admin")
    if settings.alert_chat_id is None:
        raise RuntimeError("TELEGRAM_ALERT_CHAT_ID or TELEGRAM_ADMIN_IDS is required")

    engine = build_async_engine(settings.database_url)
    sessions = build_session_factory(engine)
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Fail loudly at startup when the configured destination is wrong or the
    # bot cannot access it. Polling being healthy does not imply channel
    # publishing permissions are healthy.
    try:
        target_chat = await bot.get_chat(settings.alert_chat_id)
    except Exception as exc:
        await bot.session.close()
        await engine.dispose()
        raise RuntimeError(
            f"Telegram alert target {settings.alert_chat_id} is not accessible to the bot"
        ) from exc
    target_type_raw = getattr(target_chat, "type", "unknown")
    target_type = getattr(target_type_raw, "value", str(target_type_raw))
    if target_type == "channel":
        try:
            me = await bot.get_me()
            membership = await bot.get_chat_member(settings.alert_chat_id, me.id)
        except Exception as exc:
            await bot.session.close()
            await engine.dispose()
            raise RuntimeError(
                f"Cannot verify bot posting permissions for channel {settings.alert_chat_id}"
            ) from exc
        status_raw = getattr(membership, "status", "")
        status = getattr(status_raw, "value", str(status_raw))
        can_post = bool(getattr(membership, "can_post_messages", False))
        if status not in {"creator", "administrator"} or not can_post:
            await bot.session.close()
            await engine.dispose()
            raise RuntimeError(
                "Telegram bot must be a channel administrator with can_post_messages=true "
                f"for alert target {settings.alert_chat_id}"
            )

    LOGGER.info(
        "Telegram alert target verified chat_id=%s type=%s title=%s",
        settings.alert_chat_id,
        target_type,
        getattr(target_chat, "title", None)
        or getattr(target_chat, "full_name", None)
        or "private",
    )

    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(
            admin_ids=settings.admin_ids,
            sessions=sessions,
            bot=bot,
            alert_chat_id=settings.alert_chat_id,
            parser_freshness_seconds={
                "fonbet": settings.odds_collection_interval_seconds * 2.5,
                "uel_ef": settings.history_collection_interval_seconds * 2.5,
                "esb_ef": settings.history_collection_interval_seconds * 2.5,
                "h2h_ef": settings.history_collection_interval_seconds * 2.5,
            },
        )
    )
    publisher = TelegramPublisher(
        bot=bot,
        chat_id=settings.alert_chat_id,
        sessions=sessions,
    )
    # Attempt any already-persisted alerts immediately instead of waiting for
    # the first publisher interval.
    await publisher.publish_once()

    stop = asyncio.Event()
    publisher_task = asyncio.create_task(
        publisher.run_forever(
            stop=stop,
            interval_seconds=settings.telegram_publish_interval_seconds,
        )
    )
    try:
        await bot.set_my_commands(ADMIN_COMMANDS)
        await dispatcher.start_polling(bot)
    finally:
        stop.set()
        publisher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher_task
        await bot.session.close()
        await engine.dispose()
