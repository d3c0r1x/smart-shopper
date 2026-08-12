"""Тесты Review Intelligence: вердикты, кэш анализов, ранжирование."""
from __future__ import annotations

import asyncio
import tempfile

from adapters import MockOzonAdapter
from llm.gateway import LLMGateway
from models import Product, ReviewAnalysis
from review.intelligence import analyze_reviews, rank_by_verdicts
from storage.db import Database


def _setup():
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        llm = LLMGateway(db, api_key="")
        adapter = MockOzonAdapter()
        product = await adapter.get_card("msk-001")
        reviews = await adapter.get_reviews("msk-001", limit=10)
        return db, llm, product, reviews
    return asyncio.run(run())


def test_verdicts_confirmed_for_mask_reviews():
    db, llm, product, reviews = _setup()
    async def run():
        analysis = await analyze_reviews(llm, product, reviews,
                                         ["чёрный", "ресниц"], db=db)
        by_req = {v.requirement: v for v in analysis.verdicts}
        assert by_req["чёрный"].verdict == "confirmed"
        assert by_req["ресниц"].verdict == "confirmed"
        assert by_req["чёрный"].mentions >= 1
    asyncio.run(run())


def test_analysis_cached():
    db, llm, product, reviews = _setup()
    async def run():
        a1 = await analyze_reviews(llm, product, reviews, ["чёрный"], db=db)
        a2 = await analyze_reviews(llm, product, reviews, ["чёрный"], db=db)
        assert a1.model_dump() == a2.model_dump()
        # требование другое → другой ключ кэша → пересчёт
        a3 = await analyze_reviews(llm, product, reviews, ["прилега"], db=db)
        assert a3.verdicts and a3.verdicts[0].requirement == "прилега"
    asyncio.run(run())


def test_no_data_when_no_reviews():
    db, llm, product, _ = _setup()
    async def run():
        analysis = await analyze_reviews(llm, product, [], ["чёрный"], db=db)
        assert analysis.verdicts[0].verdict == "no_data"
    asyncio.run(run())


def test_negation_detected_for_single_word():
    """«прилегает» с отрицанием в отзыве → rejected."""
    db, llm, product, _ = _setup()
    async def run():
        fake = [__import__("models").Review(
            product_market="ozon", product_id="x", review_id="r1", rating=2,
            text="Маска не прилегает к лицу, свет пробивается по краям.",
        )]
        analysis = await analyze_reviews(llm, product, fake, ["прилега"], db=None)
        assert analysis.verdicts[0].verdict == "rejected"
    asyncio.run(run())


def test_rank_by_verdicts_pushes_rejected_down():
    def _analysis(pid: str, verdict: str) -> ReviewAnalysis:
        return ReviewAnalysis(
            product_market="ozon", product_id=pid,
            verdicts=[{"requirement": "чёрный", "verdict": verdict,
                       "mentions": 1, "quote": ""}],
        )

    products = [
        Product(marketplace="ozon", ext_id="a", title="A", price=100,
                url="https://x/a"),
        Product(marketplace="ozon", ext_id="b", title="B", price=100,
                url="https://x/b"),
    ]
    analyses = [_analysis("a", "confirmed"), _analysis("b", "rejected")]
    ranked = rank_by_verdicts(products, analyses)
    assert [p.ext_id for p in ranked] == ["a", "b"]
