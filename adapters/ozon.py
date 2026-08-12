"""Адаптер Ozon: поиск через публичный composer-api.

URL: www.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/?text=…
Ответ — widgetStates с JSON-строками; парсер ищет объекты товара рекурсивно
(устойчив к смене структуры — как в проекте 11).

Честный статус (README, раздел «Публичные API»): с IP без валидного
region-cookie composer-api отдаёт HTTP 307 (редирект-петля антибота).
Решение — прокси (SHOPPER_PROXY) или демо-режим. Отзывы через web-версию
не извлекаются (страница отзывов рендерится на клиенте) — возвращается
пустой список, в демо-режиме отзывы из встроенного каталога.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from adapters.base import BROWSER_HEADERS, BaseAdapter
from models import Product

logger = logging.getLogger(__name__)

COMPOSER_API_URL = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"


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


class OzonAdapter(BaseAdapter):
    """Поиск по Ozon через публичный composer-api."""

    name = "ozon"
    headers = {
        **BROWSER_HEADERS,
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
        "x-o3-app-name": "rich",
    }

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        params = {"url": f"/search/?text={quote(query)}"}
        try:
            status, text = await self._get(COMPOSER_API_URL, params=params)
        except Exception as exc:
            logger.warning("Ozon: сетевой сбой для %r: %s", query, exc)
            return []
        if status == 307:
            logger.warning("Ozon -> HTTP 307 для %r (регион-блок, нужен прокси)", query)
            return []
        if status != 200:
            logger.warning("Ozon -> HTTP %s для %r", status, query)
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Ozon: не-JSON ответ для %r", query)
            return []
        return self._parse_response(data, limit)

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

    async def get_card(self, ext_id: str) -> Product | None:
        return None  # карточка по ID — из кэша поиска (см. orchestrator)

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list:
        logger.warning("Ozon: отзывы через web-версию не извлекаются (антибот). "
                       "Используйте демо-режим.")
        return []

    async def get_photos(self, ext_id: str) -> list[str]:
        return []


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
