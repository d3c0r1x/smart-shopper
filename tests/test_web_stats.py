"""Тесты метрик HTTP-API (ТЗ §5): /api/stats и uptime в /health."""
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


def test_health_contains_uptime():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["uptime_s"] >= 0
        finally:
            await client.close()
            await ctx.db.close()
    asyncio.run(run())


def test_stats_tracks_requests_and_latency():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            # пара запросов, чтобы счётчики и p95 наполнились
            for _ in range(2):
                await client.get("/health")
            resp = await client.get("/api/stats", params={"user_id": "1"})
            assert resp.status == 200
            data = await resp.json()
            assert data["requests"] >= 2
            assert data["uptime_s"] >= 0
            assert "/health" in data["p95_ms"]
            assert data["p95_ms"]["/health"] >= 0
            assert "semantic" in data  # статус семантического слоя
        finally:
            await client.close()
            await ctx.db.close()
    asyncio.run(run())


def test_search_response_has_processing_ms():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.BOT_TOKEN = "12345:test-token"
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/search", params={
                "q": "маска для сна", "user_id": "1"})
            assert resp.status == 200
            data = await resp.json()
            assert "processing_ms" in data
            assert data["processing_ms"] >= 0
        finally:
            await client.close()
            await ctx.db.close()
    asyncio.run(run())
