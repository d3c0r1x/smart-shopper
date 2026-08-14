"""Тест метрики Data Freshness (ТЗ §5): /api/stats отдаёт возраст кэша."""
from __future__ import annotations

import asyncio
import tempfile

from aiohttp.test_utils import TestClient, TestServer

import config
import web as webmod
from adapters import build_adapters
from core.orchestrator import Orchestrator
from llm.gateway import LLMGateway
from storage.db import Database


def _mk_ctx():
    db = Database(tempfile.mktemp(suffix=".db"))
    llm = LLMGateway(db, api_key="")
    orch = Orchestrator(db, llm, build_adapters(demo=True))
    ctx = webmod.ApiContext(db, llm, orch, build_adapters(demo=True))
    ctx.matcher = lambda target, candidates: None
    return ctx


def test_stats_contains_data_freshness():
    """/api/stats отдаёт data_freshness: entries, возраст, цель <= 1 ч."""
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/stats", params={"user_id": "1"})
            assert resp.status == 200
            data = await resp.json()
            df = data.get("data_freshness")
            assert df is not None, "data_freshness отсутствует в /api/stats"
            assert df["target_s"] == 3600
            assert "entries" in df and "newest_age_s" in df
            # пустой кэш -> newest_age_s None -> ok True (цель не нарушена)
            assert df["ok"] is True
        finally:
            await client.close()
            await ctx.db.close()
    asyncio.run(run())


def test_freshness_after_cache_write():
    """После cache_set возраст самой свежей записи мал (< 60 с)."""
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        await ctx.db.cache_set("k:1", "v", ttl_seconds=3600)
        await ctx.db.cache_set("k:2", "v", ttl_seconds=3600)
        f = await ctx.db.cache_freshness()
        assert f["entries"] == 2
        assert f["newest_age_s"] < 60
        await ctx.db.close()
    asyncio.run(run())
