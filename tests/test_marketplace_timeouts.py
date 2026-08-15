from __future__ import annotations

import asyncio
from types import SimpleNamespace

import config
from core.orchestrator import Orchestrator
from models import Product


class Cache:
    async def cache_get_reviews(self, key):
        return None

    async def cache_set_reviews(self, key, value, ttl):
        raise AssertionError("зависшие отзывы не должны записываться в кэш")


async def hanging_reviews(ext_id, limit=20):
    await asyncio.Event().wait()


async def hanging_search(query, limit=10):
    await asyncio.Event().wait()


def test_hanging_reviews_do_not_block_result(monkeypatch):
    """Антибот площадки не должен удерживать выдачу бесконечно."""
    monkeypatch.setattr(
        config, "REVIEWS_FETCH_TIMEOUT_SECONDS", 0.01, raising=False)
    adapter = SimpleNamespace(name="ozon", get_reviews=hanging_reviews)
    product = Product(
        marketplace="ozon", ext_id="real-1", title="Реальный товар",
        price=500, url="https://www.ozon.ru/product/real-1/",
    )
    orch = Orchestrator(Cache(), SimpleNamespace(), [adapter])

    result = asyncio.run(
        asyncio.wait_for(orch._load_reviews(product), timeout=0.1))

    assert result == []


def test_hanging_search_does_not_block_other_markets(monkeypatch):
    """Зависший поиск одной площадки должен завершиться пустым результатом."""
    monkeypatch.setattr(
        config, "MARKET_SEARCH_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(
        config, "OZON_TIMEOUT_SECONDS", 0.01, raising=False)

    class SearchCache:
        async def cache_get_products(self, key):
            return None

    adapter = SimpleNamespace(name="ozon", search=hanging_search)
    orch = Orchestrator(SearchCache(), SimpleNamespace(), [adapter])

    result = asyncio.run(
        asyncio.wait_for(orch._search_market(adapter, "маска"), timeout=0.1))

    assert result == []
