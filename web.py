"""HTTP-API для Mini App (PRD: один backend, два рендерера).

Эндпоинты (тот же код, что и в чате — общие сессии и кэш):
  GET /api/search?q=…&markets=ozon,yandex&initData=…
  GET /api/reviews?marketplace=…&ext_id=…&initData=…
  GET /api/compare?q=…&initData=…
  GET /api/budget
  GET /health

Аутентификация:
  1) Telegram initData валидируется подписью HMAC-SHA256 (секрет =
     HMAC_SHA256(bot_token, "WebAppData")) — пользователь берётся из апдейта;
  2) если задан SHOPPER_API_TOKEN — Mini App шлёт его в X-API-Token;
  3) при невалидном initData и SHOPPER_API_ALLOW_ANON=1 (локальная разработка)
     используется user_id из query.

Контракт ответов совпадает с типами в miniapp/src/types.ts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qsl

from aiohttp import web
from aiohttp.web import AppKey

import config
from llm.gateway import BudgetExceeded
from models import Product, Review

logger = logging.getLogger(__name__)


# ── валидация initData ────────────────────────────────────────────

def parse_init_data(init_data: str, bot_token: str) -> int | None:
    """Возвращает user_id из Telegram initData или None (подпись не сошлась).

    Схема Telegram: data_check_string = строки 'k=v' по алфавиту через \\n;
    секрет = HMAC-SHA256(bot_token, "WebAppData"); подпись = hex(HMAC-SHA256).
    """
    if not init_data or not bot_token:
        return None
    try:
        params = dict(parse_qsl(init_data))
    except Exception:
        return None
    received = params.pop("hash", "")
    if not received:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        user = json.loads(params.get("user", "{}"))
        return int(user.get("id"))
    except (ValueError, TypeError):
        return None


def _resolve_user(request: web.Request) -> int:
    """user_id: initData → (dev) query user_id."""
    user_id = parse_init_data(request.query.get("initData", ""), config.BOT_TOKEN)
    if user_id is not None:
        return user_id
    if config.API_ALLOW_ANON:
        raw = request.query.get("user_id", "")
        if raw.isdigit():
            return int(raw)
    raise web.HTTPUnauthorized(text="initData невалиден или user_id не передан")


def _markets(raw: str) -> list[str] | None:
    chosen = [m for m in raw.split(",") if m in ("ozon", "yandex")]
    return chosen or None


def _serialize_review(r: Review) -> dict:
    """Поле review_id → id (контракт Mini App)."""
    return {
        "id": r.review_id,
        "rating": r.rating,
        "author": r.author,
        "date": r.date,
        "text": r.text,
        "pros": r.pros or None,
        "cons": r.cons or None,
        "bought_here": r.bought_here,
    }


# ── хендлеры ──────────────────────────────────────────────────────

async def handle_search(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    q = request.query.get("q", "").strip()
    if not q:
        raise web.HTTPBadRequest(text="Параметр q обязателен")
    user_id = _resolve_user(request)
    state = await ctx.db.get_session(user_id)
    outcome = await ctx.orch.search_with_constraints(
        user_id, q, state, None,
        markets=_markets(request.query.get("markets", "both")))
    return web.json_response({
        "products": [p.model_dump() for p in outcome.top],
        "constraints": outcome.constraints.model_dump(),
        "verdicts": {ext: [v.model_dump() for v in a.verdicts]
                     for ext, a in outcome.analyses.items()},
    })


async def handle_reviews(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    market = request.query.get("marketplace", "")
    ext_id = request.query.get("ext_id", "")
    if market not in ("ozon", "yandex") or not ext_id:
        raise web.HTTPBadRequest(text="marketplace/ext_id обязательны")
    user_id = _resolve_user(request)

    state = await ctx.db.get_session(user_id)
    product = next((p for p in state.last_results
                    if p.marketplace == market and p.ext_id == ext_id), None)
    if product is None:
        product = Product(marketplace=market, ext_id=ext_id,
                          title="", price=0, url="")
    reviews = await ctx.orch._load_reviews(product)
    return web.json_response({"reviews": [_serialize_review(r) for r in reviews]})


async def handle_compare(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    q = request.query.get("q", "").strip()
    if not q:
        raise web.HTTPBadRequest(text="Параметр q обязателен")
    _resolve_user(request)
    candidates = await ctx.orch.search_candidates(q, _markets(request.query.get("markets", "both")))
    ozon_items = [p for p in candidates if p.marketplace == "ozon"]
    yandex_items = [p for p in candidates if p.marketplace == "yandex"]
    rows = []
    for target in ozon_items[:3]:
        result = await ctx.matcher(target, yandex_items)
        if result is not None:
            rows.append(result.model_dump())
    return web.json_response({"rows": rows})


async def handle_budget(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    _resolve_user(request)
    return web.json_response(await ctx.llm.budget_info())


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "smart-shopper"})


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    if config.API_TOKEN and request.path != "/health":
        token = request.headers.get("X-API-Token", "")
        if token != config.API_TOKEN:
            raise web.HTTPUnauthorized(text="Неверный X-API-Token")
    return await handler(request)


@web.middleware
async def _cors_middleware(request: web.Request, handler):
    """CORS для Mini App: браузер на другом домене (GitHub Pages) делает
    fetch на этот API. Разрешаем все источники — токен авторизации
    (initData / X-API-Token) защищает эндпоинты, cookies не используются."""
    if request.method == "OPTIONS":
        return web.Response(headers=_cors_headers())
    resp = await handler(request)
    resp.headers.update(_cors_headers())
    return resp


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "X-API-Token, Content-Type",
    }


@web.middleware
async def _error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except BudgetExceeded:
        return web.json_response(
            {"error": "Дневной лимит бесплатных запросов исчерпан "
                      "(50/день; после пополнения от $10 — 1000/день)."},
            status=429)
    except web.HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("API error on %s", request.path)
        return web.json_response({"error": str(exc)}, status=500)


class ApiContext:
    """Держит ссылки на общие объекты бота (db, llm, orch, matcher)."""

    def __init__(self, db, llm, orch, adapters) -> None:
        self.db = db
        self.llm = llm
        self.orch = orch
        self.adapters = adapters
        # matcher-функция привязывается при создании контекста в bot.py,
        # чтобы web.py не зависел от сигнатуры внутренностей matcher.
        self.matcher = None


CTX_KEY = AppKey("ctx", ApiContext)


def create_app(ctx: ApiContext) -> web.Application:
    app = web.Application(
        middlewares=[_cors_middleware, _auth_middleware,
                     _error_middleware])
    app[CTX_KEY] = ctx
    app.router.add_get("/api/search", handle_search)
    app.router.add_get("/api/reviews", handle_reviews)
    app.router.add_get("/api/compare", handle_compare)
    app.router.add_get("/api/budget", handle_budget)
    app.router.add_get("/health", handle_health)
    return app


async def _run_standalone() -> None:
    """Запуск только HTTP-API (без поллинга бота) для публичного
    развёртывания. Mini App в этом режиме работает против демо-адаптеров
    и реального LLM (если задан OPENROUTER_API_KEY)."""
    import asyncio

    from adapters import build_adapters
    from core.orchestrator import Orchestrator
    from llm.gateway import LLMGateway
    from storage.db import Database

    db = Database(config.DB_PATH)
    await db.connect()
    llm = LLMGateway(db)
    orch = Orchestrator(db, llm, build_adapters())
    ctx = ApiContext(db, llm, orch, orch._adapters)
    ctx.matcher = lambda target, candidates: None  # сравнение цен без LLM
    app = create_app(ctx)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.API_PORT)
    await site.start()
    logger.info("HTTP-API автономно: http://0.0.0.0:%d (LLM: %s, демо: %s)",
                config.API_PORT, "OpenRouter" if llm.real else "mock",
                config.DEMO_MODE)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await db.close()
        await llm.aclose()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run_standalone())
