"""Middleware: троттлинг (защита от спама) и логирование апдейтов."""
from __future__ import annotations

import logging
import time

import config
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

_last_seen: dict[int, float] = {}


class ThrottlingMiddleware(BaseMiddleware):
    """Не чаще одного события от пользователя в THROTTLE_MIN_INTERVAL с.

    Работает и для сообщений, и для inline-колбэков (у CallbackQuery есть
    from_user). Отброшенный колбэк получает пустой answer — Telegram гасит
    «крутящуюся» кнопку, но хендлер повторно не вызывается.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user is not None:
            now = time.monotonic()
            last = _last_seen.get(user.id, 0.0)
            if now - last < config.THROTTLE_MIN_INTERVAL:
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:  # pragma: no cover
                        pass
                return None
            _last_seen[user.id] = now
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    """Пишет в лог все входящие сообщения и колбэки."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, Message):
            logger.info("msg from %s: %s", event.from_user.id,
                        (event.text or "[фото]" if event.photo else event.text)[:100])
        elif isinstance(event, CallbackQuery):
            logger.info("cb from %s: %s", event.from_user.id,
                        (event.data or "")[:80])
        return await handler(event, data)
