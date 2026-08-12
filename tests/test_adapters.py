"""Тесты адаптеров: контракт search/get_card/get_reviews/get_photos."""
from __future__ import annotations

import asyncio

from adapters import MockOzonAdapter, MockYandexAdapter
from models import Product, Review


def _ozon():
    return MockOzonAdapter()


def test_demo_search_returns_products():
    async def run():
        results = await _ozon().search("маска для сна", limit=5)
        assert results
        for p in results:
            assert isinstance(p, Product)
            assert p.marketplace == "ozon"
            assert p.title and p.price > 0
            assert p.url.startswith("https://")
            assert p.ean and len(p.ean) >= 8
    asyncio.run(run())


def test_demo_search_word_matching():
    async def run():
        masks = await _ozon().search("маска для сна", limit=20)
        sneakers = await _ozon().search("кроссовки белые", limit=20)
        assert masks and sneakers
        assert all("маск" in p.title.lower() or "для сна" in p.title.lower()
                   for p in masks)
        assert all("маск" not in p.title.lower() for p in sneakers)
    asyncio.run(run())


def test_demo_search_no_stopword_pollution():
    """Слова «для»/«сна» не должны подтягивать кроссовки в выдачу масок."""
    async def run():
        results = await _ozon().search(
            "чёрная маска для сна с пространством для ресниц", limit=20)
        assert results
        assert all("маск" in p.title.lower() for p in results)
    asyncio.run(run())


def test_demo_get_card():
    async def run():
        card = await _ozon().get_card("msk-001")
        assert card is not None and "Маска" in card.title
        assert await _ozon().get_card("nope-999") is None
    asyncio.run(run())


def test_demo_reviews_and_photos():
    async def run():
        reviews = await _ozon().get_reviews("msk-001", limit=5)
        assert reviews and len(reviews) <= 5
        for r in reviews:
            assert isinstance(r, Review)
            assert r.text and 1 <= r.rating <= 5
        assert await _ozon().get_photos("msk-001") == []
    asyncio.run(run())


def test_yandex_demo_has_shared_ean_for_compare():
    """Тот же товар (EAN) присутствует на обеих площадках — для сравнения."""
    async def run():
        ozon_eans = {p.ean for p in await _ozon().search("маска для сна", limit=20)}
        yandex_eans = {p.ean for p in await MockYandexAdapter().search(
            "маска для сна", limit=20)}
        assert ozon_eans & yandex_eans, "нет общих EAN — сравнение невозможно"
    asyncio.run(run())
