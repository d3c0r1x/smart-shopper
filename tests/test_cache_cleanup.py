"""Ревью-фикс: фоновая очистка кэша (bot.cache_cleanup_loop).

Раньше cleanup_expired вызывался только в /stats и на старте; таблица cache
росла между рестартами. Цикл теперь живёт в фоне и переживает ошибки БД.
"""
from __future__ import annotations

import asyncio

from bot import cache_cleanup_loop


class _FakeDb:
    def __init__(self) -> None:
        self.calls = 0

    async def cleanup_expired(self) -> int:
        self.calls += 1
        return 1


def test_cleanup_loop_runs_periodically_and_stops_on_cancel():
    async def run():
        fake = _FakeDb()
        task = asyncio.create_task(cache_cleanup_loop(fake, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert fake.calls >= 2, fake.calls
    asyncio.run(run())


def test_cleanup_loop_survives_db_error():
    """Ошибка очистки не убивает цикл и не роняет бота."""

    class _BadDb:
        async def cleanup_expired(self) -> int:
            raise RuntimeError("boom")

    async def run():
        task = asyncio.create_task(cache_cleanup_loop(_BadDb(), interval=0.01))
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # дошли сюда — цикл пережил ошибку и был отменён корректно
    asyncio.run(run())
