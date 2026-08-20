from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.telegram.formatter import format_settlement, format_signal_alert
from app.telegram.repository import PendingAlert, PendingSettlement, TelegramRepository

LOGGER = logging.getLogger(__name__)


class TelegramPublisher:
    """Deliver persisted alerts and settlement replies without blocking the queue.

    A single malformed/inaccessible Telegram message must not prevent every later
    signal from being attempted. Persisted signals remain pending until Telegram
    accepts them or their quote expires.
    """

    def __init__(
        self,
        *,
        bot: Bot,
        chat_id: int,
        sessions: async_sessionmaker[AsyncSession],
        repository: TelegramRepository | None = None,
        alert_window_minutes: int | None = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._sessions = sessions
        self._repository = repository or TelegramRepository()
        self._alert_window_minutes = alert_window_minutes

    async def _send_alert(self, alert: PendingAlert) -> bool:
        text = format_signal_alert(alert.view)
        try:
            message = await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
            )
        except TelegramBadRequest as exc:
            # Formatting should already be escaped, but a plain-text fallback
            # keeps one unexpected Telegram HTML edge case from blocking the
            # entire signal queue.
            if "parse" not in str(exc).casefold() and "entity" not in str(exc).casefold():
                LOGGER.exception(
                    "Telegram alert send failed signal_id=%s chat_id=%s",
                    alert.signal_id,
                    self._chat_id,
                )
                return False
            LOGGER.warning(
                "Telegram rejected formatted alert signal_id=%s; retrying as plain text: %s",
                alert.signal_id,
                exc,
            )
            try:
                message = await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode=None,
                )
            except Exception:
                LOGGER.exception(
                    "Telegram plain-text alert retry failed signal_id=%s chat_id=%s",
                    alert.signal_id,
                    self._chat_id,
                )
                return False
        except Exception:
            LOGGER.exception(
                "Telegram alert send failed signal_id=%s chat_id=%s",
                alert.signal_id,
                self._chat_id,
            )
            return False

        try:
            async with self._sessions.begin() as session:
                await self._repository.mark_alert_sent(
                    session,
                    signal_id=alert.signal_id,
                    message_id=message.message_id,
                    sent_at=datetime.now(UTC),
                )
        except Exception:
            # Telegram has already accepted the message. Surface this loudly;
            # a retry can duplicate the visible notification, but silently
            # pretending it was never sent is worse for settlement accounting.
            LOGGER.exception(
                "Telegram alert was sent but database acknowledgement failed "
                "signal_id=%s message_id=%s",
                alert.signal_id,
                message.message_id,
            )
            return False

        LOGGER.info(
            "Telegram alert delivered signal_id=%s message_id=%s chat_id=%s",
            alert.signal_id,
            message.message_id,
            self._chat_id,
        )
        return True

    async def _send_settlement(self, settlement: PendingSettlement) -> bool:
        text = format_settlement(settlement.view)
        try:
            if settlement.reply_to_message_id:
                try:
                    await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=settlement.reply_to_message_id,
                        text=text,
                    )
                except Exception:
                    LOGGER.warning(
                        "Telegram settlement edit failed; sending fallback "
                        "settlement_id=%s message_id=%s",
                        settlement.settlement_id,
                        settlement.reply_to_message_id,
                        exc_info=True,
                    )
                    await self._bot.send_message(chat_id=self._chat_id, text=text)
            else:
                await self._bot.send_message(chat_id=self._chat_id, text=text)
        except Exception:
            LOGGER.exception(
                "Telegram settlement update failed settlement_id=%s chat_id=%s",
                settlement.settlement_id,
                self._chat_id,
            )
            return False

        try:
            async with self._sessions.begin() as session:
                await self._repository.mark_settlement_sent(
                    session,
                    settlement_id=settlement.settlement_id,
                    sent_at=datetime.now(UTC),
                )
        except Exception:
            LOGGER.exception(
                "Telegram settlement was sent but database acknowledgement failed settlement_id=%s",
                settlement.settlement_id,
            )
            return False
        LOGGER.info(
            "Telegram settlement delivered settlement_id=%s reply_to=%s",
            settlement.settlement_id,
            settlement.reply_to_message_id,
        )
        return True

    async def publish_once(self) -> tuple[int, int]:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            expired = await self._repository.expire_pending_alerts(session, now=now)
            alerts = await self._repository.pending_alerts(
                session,
                now=now,
                alert_window_minutes=self._alert_window_minutes,
            )
            settlements = await self._repository.pending_settlements(session)

        if expired or alerts or settlements:
            LOGGER.info(
                "Telegram delivery cycle: pending_alerts=%s pending_settlements=%s "
                "expired=%s chat_id=%s",
                len(alerts),
                len(settlements),
                expired,
                self._chat_id,
            )

        sent_alerts = 0
        for alert in alerts:
            if await self._send_alert(alert):
                sent_alerts += 1

        sent_settlements = 0
        for settlement in settlements:
            if await self._send_settlement(settlement):
                sent_settlements += 1
        return sent_alerts, sent_settlements

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        interval_seconds: float,
    ) -> None:
        LOGGER.info(
            "Telegram publisher started chat_id=%s interval=%.1fs",
            self._chat_id,
            interval_seconds,
        )
        while not stop.is_set():
            try:
                await self.publish_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Telegram publisher iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
