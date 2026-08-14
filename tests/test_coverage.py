"""Тест мониторинга успешности парсинга (ТЗ §5, Coverage >= 90%)."""
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


def test_market_stats_records_outcomes():
    """"Счётчики: успех и ошибка учитываются, coverage считается."""
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        orch = ctx.orch
        # имитация исходов поиска
        for a in orch._adapters:
            orch._record_market(a.name, "ok")
        orch._record_market(orch._adapters[0].name, "error")
        orch._record_market(orch._adapters[0].name, "empty")
        stats = orch.market_stats()
        # у всех адаптеров есть запись
        assert len(stats) == len(orch._adapters)
        first = stats[orch._adapters[0].name]
        assert first["total"] == 3
        assert first["ok"] == 1
        assert first["error"] == 1
        assert first["empty"] == 1
        assert first["coverage_pct"] == round(100.0 * 1 / 3, 1)
        await ctx.db.close()
    asyncio.run(run())


def test_stats_api_includes_coverage():
    """/api/stats отдаёт блок coverage (может быть пустым до поисков)."""
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
            assert "coverage" in data
            assert isinstance(data["coverage"], dict)
        finally:
            await client.close()
            await ctx.db.close()
    asyncio.run(run())
