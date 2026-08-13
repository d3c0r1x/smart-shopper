"""Тесты гибридного реранкера (search/rerank.py).

Семантика подменяется фейковым эмбеддером: проверяется смешение
семантической, лексической и структурной составляющих, штрафы за
нарушение ограничений и деградация при недоступном эмбеддере.
"""
from __future__ import annotations

import asyncio

import pytest

from models import Product
from search.rerank import HybridReranker
from search.structfilter import StructFilters


def _p(title, price=1000, rating=4.5, reviews=100, brand="Urban",
       traits=None):
    return Product(marketplace="ozon", ext_id=title, title=title, price=price,
                   rating=rating, reviews_count=reviews, brand=brand,
                   traits=traits or [], url="https://x")


class FakeEmbedder:
    """Семантика = доля слов запроса, встретившихся в тексте кандидата."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def similarity(self, query, texts):
        if self.fail:
            return None
        q_words = set(query.lower().split())
        return [
            len(q_words & set(t.lower().replace(" | ", " ").split()))
            / max(1, len(q_words))
            for t in texts
        ]


def _run(coro):
    return asyncio.run(coro)


def test_lexical_score_ranks_relevant_first():
    r = HybridReranker(None, weights=(0.0, 1.0, 0.0))
    q = "маска для сна"
    assert r.lexical_score(q, _p("Маска для сна 3D чёрная")) > \
        r.lexical_score(q, _p("Кроссовки белые"))


def test_structural_penalty():
    r = HybridReranker(None, weights=(0.0, 0.0, 1.0))
    f = StructFilters(max_price=1000, brand="silksleep")
    ok = _p("Маска для сна", price=900, brand="SilkSleep")
    too_expensive = _p("Маска для сна", price=2500, brand="SilkSleep")
    wrong_brand = _p("Маска для сна", price=900, brand="Other")
    assert r.structural_score(f, ok) == pytest.approx(1.0)
    assert r.structural_score(f, too_expensive) < r.structural_score(f, ok)
    assert r.structural_score(f, wrong_brand) < r.structural_score(f, ok)


def test_semantic_dominates_similar_titles():
    async def run():
        r = HybridReranker(FakeEmbedder(), weights=(1.0, 0.0, 0.0))
        products = [_p("Маска для сна 3D чёрная"), _p("Кроссовки белые")]
        ranked = await r.rerank("маска для сна", StructFilters(), products)
        assert ranked[0][0].title == "Маска для сна 3D чёрная"
    _run(run())


def test_hybrid_rerank_order():
    async def run():
        r = HybridReranker(FakeEmbedder(), weights=(0.4, 0.4, 0.2))
        products = [
            _p("Маска для сна 3D чёрная", price=990),
            _p("Кроссовки белые", price=500, rating=4.9, reviews=5000),
            _p("Маска для сна шёлковая", price=1200),
        ]
        filters = StructFilters(max_price=1500, min_rating=4.0)
        ranked = await r.rerank("маска для сна чёрная", filters, products)
        titles = [p.title for p, _ in ranked]
        assert titles[0] == "Маска для сна 3D чёрная"
        assert titles[1] == "Маска для сна шёлковая"
        assert titles[2] == "Кроссовки белые"
    _run(run())


def test_fallback_when_embedder_fails():
    async def run():
        r = HybridReranker(FakeEmbedder(fail=True), weights=(0.4, 0.4, 0.2))
        products = [_p("Маска для сна 3D чёрная"), _p("Кроссовки белые")]
        ranked = await r.rerank("маска для сна", StructFilters(), products)
        assert ranked[0][0].title == "Маска для сна 3D чёрная"
    _run(run())


def test_sync_scoring_matches_async_without_semantics():
    async def run():
        r = HybridReranker(None, weights=(0.4, 0.4, 0.2))
        products = [_p("Маска для сна 3D"), _p("Кроссовки")]
        sync = r.score_sync("маска для сна", StructFilters(), products)
        async_ranked = await r.rerank("маска для сна", StructFilters(), products)
        assert [p.title for p, _ in sync] == \
            [p.title for p, _ in async_ranked]
    _run(run())
