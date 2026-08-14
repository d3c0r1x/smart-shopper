"""SQLite-хранилище (aiosqlite): сессии, кэш, избранное, бюджет LLM.

MVP-замена PostgreSQL+Redis из PRD (раздел 4, пункт 9): одна файловая БД с
таблицами под все нужды. Схема спроектирована так, чтобы позже мигрировать
на PostgreSQL без изменения контрактов (методы остаются теми же).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite

from models import Product, Review, SessionState


class Database:
    """Тонкая обёртка над aiosqlite с идемпотентной инициализацией схемы."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._init_schema()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                user_id   INTEGER PRIMARY KEY,
                state     TEXT NOT NULL,
                updated   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS favorites (
                user_id      INTEGER NOT NULL,
                marketplace  TEXT NOT NULL,
                ext_id       TEXT NOT NULL,
                title        TEXT NOT NULL,
                price        INTEGER NOT NULL,
                url          TEXT NOT NULL,
                added        TEXT NOT NULL,
                PRIMARY KEY (user_id, marketplace, ext_id)
            );

            CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS llm_budget (
                day       TEXT NOT NULL,
                used      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
            """
        )
        # миграция: created_at для метрики Data Freshness (ТЗ §5)
        try:
            await self._conn.execute("ALTER TABLE cache ADD COLUMN created_at TEXT")
        except Exception:
            pass  # колонка уже есть
        await self._conn.commit()

    # ── сессии диалога ────────────────────────────────────────────
    async def get_session(self, user_id: int) -> SessionState:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT state FROM sessions WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return SessionState()
        try:
            return SessionState.model_validate_json(row["state"])
        except Exception:
            return SessionState()

    async def save_session(self, user_id: int, state: SessionState) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO sessions (user_id, state, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, "
            "updated = excluded.updated",
            (user_id, state.model_dump_json(), datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    # ── кэш (поиск / карточки / отзывы / аналитика) ───────────────
    async def cache_get(self, key: str) -> str | None:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT value FROM cache WHERE key = ? AND expires_at > ?",
            (key, datetime.now(timezone.utc).isoformat()),
        )
        row = await cur.fetchone()
        return row["value"] if row else None

    async def cache_set(self, key: str, value: str, ttl_seconds: int) -> None:
        assert self._conn is not None
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        await self._conn.execute(
            "INSERT INTO cache (key, value, created_at, expires_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "created_at = excluded.created_at, expires_at = excluded.expires_at",
            (key, value, now.isoformat(), expires),
        )
        await self._conn.commit()

    async def cache_get_products(self, key: str) -> list[Product] | None:
        raw = await self.cache_get(key)
        if raw is None:
            return None
        return [Product.model_validate(p) for p in json.loads(raw)]

    async def cache_set_products(self, key: str, products: list[Product],
                                 ttl_seconds: int) -> None:
        await self.cache_set(key, json.dumps([p.model_dump() for p in products]),
                             ttl_seconds)

    async def cache_freshness(self) -> dict:
        """Метрика Data Freshness (ТЗ §5): возраст самой свежей записи кэша."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT COUNT(*) AS n, MAX(created_at) AS newest, "
            "MIN(created_at) AS oldest FROM cache")
        row = await cur.fetchone()
        n = row["n"] or 0
        newest = row["newest"]
        if not newest:
            return {"entries": n, "newest_age_s": None, "newest_at": None}
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(newest)).total_seconds()
        return {"entries": n, "newest_age_s": round(age, 1), "newest_at": newest}

    async def cache_get_reviews(self, key: str) -> list[Review] | None:
        raw = await self.cache_get(key)
        if raw is None:
            return None
        return [Review.model_validate(r) for r in json.loads(raw)]

    async def cache_set_reviews(self, key: str, reviews: list[Review],
                                ttl_seconds: int) -> None:
        await self.cache_set(key, json.dumps([r.model_dump() for r in reviews]),
                             ttl_seconds)

    # ── избранное ──────────────────────────────────────────────────
    async def add_favorite(self, user_id: int, p: Product) -> bool:
        """True — добавлено, False — уже было (идемпотентно)."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "INSERT OR IGNORE INTO favorites "
            "(user_id, marketplace, ext_id, title, price, url, added) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, p.marketplace, p.ext_id, p.title, p.price, p.url,
             datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def remove_favorite(self, user_id: int, p: Product) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND marketplace = ? AND ext_id = ?",
            (user_id, p.marketplace, p.ext_id),
        )
        await self._conn.commit()

    async def list_favorites(self, user_id: int) -> list[Product]:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT marketplace, ext_id, title, price, url FROM favorites "
            "WHERE user_id = ? ORDER BY added DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [
            Product(marketplace=r["marketplace"], ext_id=r["ext_id"],
                    title=r["title"], price=r["price"], url=r["url"])
            for r in rows
        ]

    # ── дневной бюджет LLM (PRD, разделы 3/8) ──────────────────────
    async def budget_used(self, day: str) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT used FROM llm_budget WHERE day = ?", (day,)
        )
        row = await cur.fetchone()
        return row["used"] if row else 0

    async def budget_increment(self, day: str) -> int:
        """Увеличивает счётчик и возвращает новое значение."""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO llm_budget (day, used) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET used = used + 1",
            (day,),
        )
        await self._conn.commit()
        return await self.budget_used(day)

    # ── глобальные настройки (профиль моделей и т.п.) ────────────────
    async def get_setting(self, key: str, default: str = "") -> str:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))
        await self._conn.commit()

    # ── утилиты ────────────────────────────────────────────────────
    async def stats(self) -> dict:
        assert self._conn is not None
        counts: dict[str, int] = {}
        for table in ("sessions", "favorites", "cache"):
            cur = await self._conn.execute(f"SELECT COUNT(*) AS c FROM {table}")
            row = await cur.fetchone()
            counts[table] = row["c"]
        return counts

    async def cleanup_expired(self) -> int:
        """Удаляет истёкшие записи кэша; возвращает число удалённых."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "DELETE FROM cache WHERE expires_at <= ?", (datetime.now(timezone.utc).isoformat(),)
        )
        await self._conn.commit()
        return cur.rowcount
