"""Регрессия: кириллический query через HTTP-API не искажается.

Живой сценарий вскрыл, что при неверной кодировке запроса маркетплейс
возвращает нерелевантные товары (запрос доходит битым). Тест проверяет,
что q с не-ASCII символами корректно декодируется из percent-encoding
и попадает в constraints без искажений.
"""
from __future__ import annotations

import asyncio
import tempfile
from urllib.parse import quote

import pytest
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


@pytest.mark.parametrize("q", ["маска для сна", "до 1200 рублей", "чёрный бренд"])
def test_cyrillic_query_undamaged(q):
    """UTF-8 percent-encoded query → constraints.query без cp1251-искажений."""
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.API_ALLOW_ANON = True
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            url = f"/api/search?q={quote(q)}&user_id=1"
            resp = await client.get(url)
            assert resp.status == 200
            data = await resp.json()
            stored = data["constraints"]["query"]
            # искажённая cp1251-строка выглядит как байты в %XX — не должна
            assert "%" not in stored, f"query искажён: {stored!r}"
            assert stored == q or stored in q or q in stored
        finally:
            await client.close()
            await ctx.db.close()
    asyncio.run(run())
