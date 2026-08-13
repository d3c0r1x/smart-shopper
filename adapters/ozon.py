"""Адаптер Ozon: поиск через публичные JSON-эндпоинты (канал 1).

Канал данных — внутренние JSON-API самого сайта (SPA):
  * web:  www.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/?text=…
  * mobile: api.ozon.ru/composer-api.bx/page/json/v2 (тот же формат)
Ответ — widgetStates с JSON-строками; парсер ищет объекты товара рекурсивно
(устойчив к смене структуры — как в проекте 11).

Точные заголовки и cookies применяются 1-в-1 из зафиксированного запроса
(captured/ozon.json, инструмент tools/capture_endpoints.py — процедура из
PRD §3). Если capture-файла нет — используется документированное семейство
composer-api + cookie-бутстрап через главную страницу (антибот ставит
регион-куки при первом заходе, сессия их сохраняет).

Честный статус: с IP без валидного region-cookie сервер отдаёт 307
(редирект-петля) или 403 (челлендж). Решение — SHOPPER_PROXY или
фиксация эндпоинтов с рабочего IP (tools/capture_endpoints.py).
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from adapters.base import BROWSER_HEADERS, BaseAdapter
from adapters.capture import load_captured, pick_endpoint
from models import Product, Review

logger = logging.getLogger(__name__)

COMPOSER_WEB = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
COMPOSER_MOBILE = "https://api.ozon.ru/composer-api.bx/page/json/v2"
MAIN_URL = "https://www.ozon.ru/"


def _iter_widget_states(data: dict):
    for key, raw in (data.get("widgetStates") or {}).items():
        if not isinstance(raw, str):
            continue
        try:
            yield key, json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue


def _find_products(node, acc: list[dict], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, dict):
        if "product" in node and isinstance(node["product"], dict):
            acc.append(node["product"])
        for value in node.values():
            _find_products(value, acc, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _find_products(item, acc, depth + 1)


def _to_rubles(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("value") or value.get("price")
    if isinstance(value, (int, float)):
        return int(round(value)) if isinstance(value, float) else (value if value < 100000 else value // 100)
    s = str(value).replace("\u00a0", " ").replace(" ", "").replace(",", ".").strip()
    if not s or not s.replace(".", "").isdigit():
        return None
    num = float(s)
    return int(num // 100) if num >= 100000 else int(round(num))


def _parse_rating(raw: dict) -> float | None:
    rating = raw.get("rating")
    if isinstance(rating, dict):
        rating = rating.get("value")
    try:
        return round(float(rating), 1) if rating is not None else None
    except (TypeError, ValueError):
        return None


def _parse_review_count(raw: dict) -> int:
    feedbacks = raw.get("feedbackCount") or raw.get("reviewsCount")
    if isinstance(feedbacks, dict):
        feedbacks = feedbacks.get("value")
    try:
        return int(feedbacks)
    except (TypeError, ValueError):
        return 0


class OzonAdapter(BaseAdapter):
    """Канал 1: поиск/отзывы через composer-api (web + mobile)."""

    name = "ozon"
    headers = {
        **BROWSER_HEADERS,
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
        "x-o3-app-name": "rich",
    }
    # заголовки мобильного клиента (как шлёт Android-приложение)
    mobile_headers = {
        "User-Agent": "OzonApp/18.0.0+5395 (Android 13; ru)",
        "Accept": "application/json",
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
    }

    def __init__(self, transport=None, max_retries: int | None = None,
                 auto_bootstrap: bool = True) -> None:
        super().__init__(transport=transport, max_retries=max_retries)
        self._captured = load_captured("ozon")
        self._bootstrapped = False
        self._auto_bootstrap = auto_bootstrap

    # ── подготовка ────────────────────────────────────────────────
    async def _ensure_bootstrap(self) -> None:
        if self._bootstrapped or not self._auto_bootstrap:
            return
        # главная ставит region/сессионные куки; сессия их сохраняет
        self._bootstrapped = await self.bootstrap_cookies(MAIN_URL)
        if not self._bootstrapped:
            logger.warning("Ozon: бутстрап cookies не удался")

    def _endpoint_base(self) -> str:
        """Приоритет: зафиксированный эндпоинт → web → mobile."""
        ep = pick_endpoint(self._captured, "search")
        if ep:
            return ep["url"].split("?")[0]
        return COMPOSER_WEB

    def _headers_for(self, url: str) -> dict | None:
        ep = pick_endpoint(self._captured, "search")
        if ep and ep.get("request_headers"):
            return {k: v for k, v in ep["request_headers"].items()
                    if k.lower() not in {"host", "content-length"}}
        if "api.ozon.ru" in url:
            return dict(self.mobile_headers)
        return None

    # ── поиск ─────────────────────────────────────────────────────
    async def search(self, query: str, limit: int = 5) -> list[Product]:
        await self._ensure_bootstrap()
        params = {"url": f"/search/?text={quote(query)}"}
        last = None
        for base in (self._endpoint_base(), COMPOSER_WEB, COMPOSER_MOBILE):
            try:
                status, text = await self._get(
                    base, params=params,
                    extra_headers=self._headers_for(base))
            except Exception as exc:
                last = exc
                continue
            if status in (200,):
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                products = self._parse_response(data, limit)
                if products:
                    return products
            elif status == 307:
                logger.warning("Ozon %s -> 307 (регион-блок), пробую mobile",
                               base.split("//")[1].split("/")[0])
            elif status == 403:
                logger.warning("Ozon %s -> 403 (челлендж), пробую mobile",
                               base.split("//")[1].split("/")[0])
        if last is not None:
            logger.warning("Ozon: все эндпоинты упали (%s)", last)
        return []

    def _parse_response(self, data: dict, limit: int) -> list[Product]:
        raw_products: list[dict] = []
        for _key, state in _iter_widget_states(data):
            _find_products(state, raw_products)
        seen: set[str] = set()
        out: list[Product] = []
        for raw in raw_products:
            ext_id = str(raw.get("id") or raw.get("sku") or "")
            if not ext_id or ext_id in seen:
                continue
            seen.add(ext_id)
            price = _to_rubles(raw.get("price") or raw.get("salePrice"))
            old = _to_rubles(raw.get("oldPrice"))
            title = raw.get("title") or raw.get("name") or ""
            if not title or price is None:
                continue
            rating = _parse_rating(raw)
            out.append(Product(
                marketplace="ozon", ext_id=ext_id, title=title, price=price,
                old_price=old, url=f"https://www.ozon.ru/product/{ext_id}",
                rating=rating,
                reviews_count=_parse_review_count(raw),
                brand=(raw.get("brand") or "").strip(),
            ))
            if len(out) >= limit:
                break
        return out

    # ── карточка ──────────────────────────────────────────────────
    async def get_card(self, ext_id: str) -> Product | None:
        return None  # карточка по ID — из кэша поиска (см. orchestrator)

    # ── отзывы (канал 1: JSON-эндпоинт отзывов) ───────────────────
    async def get_reviews(self, ext_id: str, limit: int = 20) -> list[Review]:
        await self._ensure_bootstrap()
        # 1) зафиксированный эндпоинт отзывов (если есть)
        ep = pick_endpoint(self._captured, "reviews")
        if ep:
            url = ep["url"].split("?")[0]
            status, text = await self._get(url, extra_headers=ep.get("request_headers"))
            if status == 200:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = None
                reviews = self._parse_reviews(data, limit)
                if reviews:
                    return reviews
        # 2) composer-api с url отзывов (web + mobile)
        for base in (COMPOSER_WEB, COMPOSER_MOBILE):
            params = {"url": f"/product/{ext_id}/reviews/"}
            try:
                status, text = await self._get(base, params=params,
                                               extra_headers=self._headers_for(base))
            except Exception:
                continue
            if status == 200:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                reviews = self._parse_reviews(data, limit)
                if reviews:
                    return reviews
        logger.warning("Ozon: отзывы %s не извлечены (антибот/нет capture). "
                       "Зафиксируйте эндпоинт: tools/capture_endpoints.py", ext_id)
        return []

    def _parse_reviews(self, data: dict | None, limit: int) -> list[Review]:
        if not data:
            return []
        raw_reviews: list[dict] = []
        for _key, state in _iter_widget_states(data):
            _find_reviews(state, raw_reviews)
        out: list[Review] = []
        seen: set[str] = set()
        for raw in raw_reviews:
            rid = str(raw.get("id") or raw.get("reviewId") or raw.get("uuid") or "")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            rating = raw.get("rating") or raw.get("score")
            if isinstance(rating, dict):
                rating = rating.get("value")
            text = (raw.get("text") or raw.get("comment") or "").strip()
            if not text:
                continue
            author = (raw.get("author") or raw.get("user") or {})
            if isinstance(author, dict):
                author_name = author.get("name") or author.get("displayName") or ""
            else:
                author_name = ""
            date = raw.get("date") or raw.get("createdAt") or ""
            out.append(Review(
                product_market="ozon", product_id="", review_id=rid,
                rating=int(rating) if rating is not None else None,
                text=text, author=author_name, date=str(date),
            ))
            if len(out) >= limit:
                break
        return out

    async def get_photos(self, ext_id: str) -> list[str]:
        return []


def _find_reviews(node, acc: list[dict], depth: int = 0) -> None:
    """Ищет объекты отзывов (устойчиво к смене структуры widgetStates)."""
    if depth > 8:
        return
    if isinstance(node, dict):
        if ("text" in node and ("rating" in node or "score" in node)
                and isinstance(node.get("text"), str)):
            acc.append(node)
        for value in node.values():
            _find_reviews(value, acc, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _find_reviews(item, acc, depth + 1)
