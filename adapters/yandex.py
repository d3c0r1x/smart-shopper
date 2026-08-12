"""Адаптер Яндекс Маркета: поиск через публичную web-версию.

Официальный Partner API — это API продавца (ассортимент, заказы, отзывы
только о своих товарах), поиска по каталогу для третьих лиц в нём нет
(PRD раздел 3 [[28]][[60]]). Поэтому канал данных — публичная web-версия
market.yandex.ru/search?text=…

Честный статус (README): web-версия защищена антиботом (Captcha), с
обычного IP возвращает 403/редирект. Решение — прокси или демо-режим.
Парсер написан на JSON-LD (schema.org/Product) — устойчив к разметке.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from adapters.base import BROWSER_HEADERS, BaseAdapter
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

    async def search(self, query: str, limit: int = 5) -> list[Product]:
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
