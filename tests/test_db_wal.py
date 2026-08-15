"""Ревью-фикс: WAL + busy_timeout для конкурентной записи SQLite (ТЗ §4).

Без WAL/busy_timeout параллельная запись из нескольких соединений могла
ронять "database is locked". Тесты фиксируют, что режим журнала включён,
timeout задан, а одновременная запись из двух соединений не падает.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

from storage.db import Database


def _new_db(path: str) -> Database:
    return Database(path)


def test_journal_mode_wal_and_busy_timeout_set():
    """PRAGMA journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL."""
    async def run():
        path = tempfile.mktemp(suffix=".db")
        db = Database(path)
        await db.connect()
        try:
            cur = await db._conn.execute("PRAGMA journal_mode;")
            row = await cur.fetchone()
            assert row[0].lower() == "wal", row[0]

            cur = await db._conn.execute("PRAGMA busy_timeout;")
            row = await cur.fetchone()
            assert row[0] == 5000, row[0]

            cur = await db._conn.execute("PRAGMA synchronous;")
            row = await cur.fetchone()
            assert row[0] == 1, row[0]  # NORMAL
        finally:
            await db.close()
    asyncio.run(run())


def test_cache_roundtrip_and_cleanup_expired():
    """Запись/чтение кэша и удаление истёкших записей."""
    async def run():
        path = tempfile.mktemp(suffix=".db")
        db = Database(path)
        await db.connect()
        try:
            await db.cache_set("k", "v", ttl_seconds=60)
            assert await db.cache_get("k") == "v"

            # протухаем вручную и проверяем cleanup
            await db._conn.execute(
                "UPDATE cache SET expires_at = ? WHERE key = 'k'",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
            )
            await db._conn.commit()
            removed = await db.cleanup_expired()
            assert removed == 1
            assert await db.cache_get("k") is None
        finally:
            await db.close()
    asyncio.run(run())


def test_concurrent_writers_from_two_connections():
    """Два соединения пишут одновременно: busy_timeout гасит блокировку."""
    async def run():
        path = tempfile.mktemp(suffix=".db")
        db = Database(path)
        await db.connect()
        db2 = Database(path)
        await db2.connect()
        try:
            async def writer(d: Database, prefix: str) -> None:
                for i in range(10):
                    await d.cache_set(f"{prefix}{i}", "x", ttl_seconds=60)

            await asyncio.gather(writer(db, "a"), writer(db2, "b"))
            stats = await db.stats()
            assert stats["cache"] == 20, stats
        finally:
            await db2.close()
            await db.close()
    asyncio.run(run())
