"""Middleware: троттлинг (защита от спама) и логирование апдейтов."""
from __future__ import annotations

import logging
import time

import config
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)

_last_seen: dict[int, float] = {}


class ThrottlingMiddleware(BaseMiddleware):
    """Не чаще одного сообщения от пользователя в THROTTLE_MIN_INTERVAL с."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user is not None:
            now = time.monotonic()
            last = _last_seen.get(user.id, 0.0)
            if now - last < config.THROTTLE_MIN_INTERVAL:
                return None
            _last_seen[user.id] = now
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    """Пишет в лог все входящие сообщения и колбэки."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, Message):
            logger.info("msg from %s: %s", event.from_user.id,
                        (event.text or "[фото]" if event.photo else event.text)[:100])
        return await handler(event, data)
