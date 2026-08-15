"""Адаптер Яндекс Маркета: канал 1 — внутренние JSON-эндпоинты SPA.

Официальный Partner API — API продавца (свои товары, заказы), поиска по
каталогу для третьих лиц в нём нет (PRD §3 [[28]][[60]]). Канал данных —
JSON-эндпоинты самого сайта (SPA): фиксируются процедурой из PRD §3
(tools/capture_endpoints.py) в captured/yandex.json — URL, заголовки и
cookies применяются 1-в-1 из реального браузерного запроса.

Если capture-файла нет — fallback на web-версию с JSON-LD-парсингом
(schema.org/Product) и cookie-бутстрапом через главную страницу.

Честный статус: web-версия защищена антиботом (Captcha) — с обычного IP
возвращает капчу/403. Решение — SHOPPER_PROXY, фиксация эндпоинтов с
рабочего IP, или демо-режим.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from adapters.base import BROWSER_HEADERS, BaseAdapter
from adapters.capture import load_captured, pick_endpoint
from models import Product

logger = logging.getLogger(__name__)

SEARCH_URL = "https://market.yandex.ru/search"


def _extract_json_ld(html: str) -> list[dict]:
    """Вытаскивает все блоки <script type="application/ld+json"> из HTML."""
    out: list[dict] = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
        elif isinstance(data, list):
            out.extend(data)
    return out


class YandexMarketAdapter(BaseAdapter):
    """Поиск по Яндекс Маркету через web-версию (JSON-LD)."""

    name = "yandex"
    headers = {
        **BROWSER_HEADERS,
        "Referer": "https://market.yandex.ru/",
        "Origin": "https://market.yandex.ru",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }

    def __init__(self, transport=None, region_id: int = 213, **kwargs) -> None:
        super().__init__(transport=transport, **kwargs)
        self._region = region_id
        self._captured = load_captured("yandex")
        self._bootstrapped = False

    async def _ensure_bootstrap(self) -> None:
        if self._bootstrapped:
            return
        self._bootstrapped = await self.bootstrap_cookies(SEARCH_URL)
        if not self._bootstrapped:
            logger.warning("YM: бутстрап cookies не удался")

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        # канал 1: зафиксированный JSON-эндпоинт поиска
        ep = pick_endpoint(self._captured, "search")
        if ep:
            try:
                products = await self._search_json(ep, query, limit)
                if products:
                    return products
            except Exception as exc:
                logger.warning("YM: JSON-эндпоинт упал: %s", exc)
        # fallback: web-версия + JSON-LD
        return await self._search_web(query, limit)

    async def _search_json(self, ep: dict, query: str, limit: int) -> list[Product]:
        url = ep["url"]
        status, text = await self._get(
            url, extra_headers=ep.get("request_headers"))
        if status != 200:
            logger.warning("YM JSON -> HTTP %s", status)
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return self._parse_json_search(data, limit, query)

    def _parse_json_search(self, data, limit: int, query: str) -> list[Product]:
        """Рекурсивный поиск карточек товаров в JSON (устойчив к структуре)."""
        raw: list[dict] = []
        _find_product_objs(data, raw)
        out: list[Product] = []
        seen: set[str] = set()
        for item in raw:
            name = item.get("name") or item.get("title") or ""
            price = item.get("price")
            if isinstance(price, dict):
                price = price.get("value") or price.get("price")
            ext_id = str(item.get("id") or item.get("sku") or item.get("productId") or "")
            if not name or price is None or not ext_id or ext_id in seen:
                continue
            seen.add(ext_id)
            try:
                price_rub = int(float(price))
            except (TypeError, ValueError):
                continue
            rating = item.get("rating")
            if isinstance(rating, dict):
                rating = rating.get("value")
            try:
                rating_f = round(float(rating), 1) if rating is not None else None
            except (TypeError, ValueError):
                rating_f = None
            out.append(Product(
                marketplace="yandex", ext_id=ext_id, title=name,
                price=price_rub,
                url=item.get("url") or f"{SEARCH_URL}?text={quote(query)}",
                brand=(item.get("brand") or "").strip()
                if isinstance(item.get("brand"), str) else "",
                rating=rating_f,
            ))
            if len(out) >= limit:
                break
        return out

    async def _search_web(self, query: str, limit: int) -> list[Product]:
        await self._ensure_bootstrap()
        params = {"text": query, "lr": self._region}
        try:
            status, text = await self._get(SEARCH_URL, params=params)
        except Exception as exc:
            logger.warning("YM: сетевой сбой для %r: %s", query, exc)
            return []
        if status != 200:
            logger.warning("YM -> HTTP %s для %r (антибот; нужен прокси или демо)", status, query)
            return []
        products = []
        for item in _extract_json_ld(text):
            name = item.get("name", "")
            offers = item.get("offers") or {}
            price = offers.get("price")
            if not name or price is None:
                continue
            try:
                price_rub = int(float(price))
            except (TypeError, ValueError):
                continue
            products.append(Product(
                marketplace="yandex",
                ext_id=item.get("sku", name)[:60],
                title=name, price=price_rub,
                url=item.get("url", f"{SEARCH_URL}?text={quote(query)}"),
                brand=(item.get("brand") or {}).get("name", "")
                if isinstance(item.get("brand"), dict) else "",
                rating=_parse_agg_rating(item),
            ))
            if len(products) >= limit:
                break
        return products

    async def get_card(self, ext_id: str) -> Product | None:
        return None

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list:
        logger.warning("YM: отзывы через web-версию не извлекаются (антибот). "
                       "Используйте демо-режим.")
        return []

    async def get_photos(self, ext_id: str) -> list[str]:
        return []


def _parse_agg_rating(item: dict) -> float | None:
    agg = item.get("aggregateRating")
    if not isinstance(agg, dict):
        return None
    try:
        return round(float(agg.get("ratingValue")), 1)
    except (TypeError, ValueError):
        return None


def _find_product_objs(node, acc: list[dict], depth: int = 0) -> None:
    """Ищет объекты товаров в JSON ответа (устойчиво к структуре)."""
    if depth > 8:
        return
    if isinstance(node, dict):
        if ("name" in node and ("price" in node or "offers" in node)
                and isinstance(node.get("name"), str)):
            acc.append(node)
        for value in node.values():
            _find_product_objs(value, acc, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _find_product_objs(item, acc, depth + 1)
