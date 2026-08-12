"""Тесты HTTP-API для Mini App: initData-валидация и эндпоинты."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import tempfile
import time
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import config
import web as webmod
from adapters import build_adapters
from core.orchestrator import Orchestrator
from llm.gateway import LLMGateway
from storage.db import Database

BOT_TOKEN = "12345:test-token-for-initdata"


def make_init_data(user_id: int = 42, token: str = BOT_TOKEN,
                   tamper: bool = False) -> str:
    """initData с валидной подписью HMAC-SHA256 (как у Telegram)."""
    params = {
        "user": json.dumps({"id": user_id, "first_name": "Test",
                            "username": "tester"}),
        "auth_date": str(int(time.time())),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, dcs.encode(),
                              hashlib.sha256).hexdigest()
    if tamper:
        params["hash"] = "0" * 64
    return urlencode(params)


def test_parse_init_data_valid_and_invalid():
    assert webmod.parse_init_data(make_init_data(42), BOT_TOKEN) == 42
    assert webmod.parse_init_data(make_init_data(7), BOT_TOKEN) == 7
    assert webmod.parse_init_data(make_init_data(tamper=True), BOT_TOKEN) is None
    assert webmod.parse_init_data("", BOT_TOKEN) is None
    assert webmod.parse_init_data(make_init_data(42), "wrong-token") is None


def _mk_ctx():
    db = Database(tempfile.mktemp(suffix=".db"))
    llm = LLMGateway(db, api_key="")
    orch = Orchestrator(db, llm, build_adapters(demo=True))
    ctx = webmod.ApiContext(db, llm, orch, build_adapters(demo=True))
    ctx.matcher = lambda target, candidates: None
    return ctx


def _run_coro(coro):
    return asyncio.run(coro)


def test_search_endpoint():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.BOT_TOKEN = BOT_TOKEN
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/search", params={
                "q": "маска для сна",
                "markets": "ozon,yandex",
                "initData": make_init_data(42),
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["products"], "пустая выдача"
            assert data["products"][0]["title"]
            assert data["constraints"]["query"]
            # сессия создана для пользователя из initData
            state = await ctx.db.get_session(42)
            assert state.last_query
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())


def test_search_anon_user_id_when_initdata_invalid():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.BOT_TOKEN = BOT_TOKEN
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/search", params={
                "q": "маска", "user_id": "777",
                "initData": make_init_data(tamper=True),
            })
            assert resp.status == 200
            state = await ctx.db.get_session(777)
            assert state.last_query
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())


def test_compare_endpoint():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.BOT_TOKEN = BOT_TOKEN
        config.API_TOKEN = ""
        # matcher как в bot.py: полный цикл с mock-арбитром
        from matcher.matcher import compare_across_markets

        async def matcher(target, candidates):
            return await compare_across_markets(ctx.llm, target, candidates)
        ctx.matcher = matcher
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/compare", params={
                "q": "маска для сна", "user_id": "5"})
            assert resp.status == 200
            rows = (await resp.json())["rows"]
            assert rows, "нет строк сравнения"
            assert rows[0]["ozon"] and rows[0]["yandex"]
            assert rows[0]["cheaper"] in ("ozon", "yandex")
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())


def test_budget_and_health():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.BOT_TOKEN = BOT_TOKEN
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/budget", params={"user_id": "1"})
            assert resp.status == 200
            info = await resp.json()
            assert info["limit"] == 50 and info["remaining"] == 50
            resp = await client.get("/health")
            assert (await resp.json())["ok"] is True
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())


def test_token_auth_required():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.BOT_TOKEN = BOT_TOKEN
        config.API_TOKEN = "secret-token"
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/budget", params={"user_id": "1"})
            assert resp.status == 401
            resp = await client.get("/api/budget", params={"user_id": "1"},
                                    headers={"X-API-Token": "secret-token"})
            assert resp.status == 200
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())


def test_missing_query_bad_request():
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/search", params={"user_id": "1"})
            assert resp.status == 400
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())


def test_cors_headers_and_preflight():
    """Mini App с другого домена: CORS-заголовки + OPTIONS-preflight."""
    async def run():
        ctx = _mk_ctx()
        await ctx.db.connect()
        config.API_TOKEN = ""
        app = webmod.create_app(ctx)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/health",
                                    headers={"Origin": "https://d3c0r1x.github.io"})
            assert resp.status == 200
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"

            resp = await client.options(
                "/api/search",
                headers={
                    "Origin": "https://d3c0r1x.github.io",
                    "Access-Control-Request-Method": "GET",
                })
            assert resp.status == 200
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            assert "GET" in resp.headers.get(
                "Access-Control-Allow-Methods", "")

            # заголовки должны быть и на ОШИБКАХ: браузерный fetch иначе
            # блокирует ответ как CORS-нарушение (регрессия: 401 без CORS)
            resp = await client.get(
                "/api/search",
                params={"q": "тест"},
                headers={"Origin": "https://d3c0r1x.github.io"})
            assert resp.status == 401
            assert resp.headers.get(
                "Access-Control-Allow-Origin") == "*"
        finally:
            await client.close()
            await ctx.db.close()
    _run_coro(run())
