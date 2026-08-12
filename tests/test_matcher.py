"""Тесты Matcher: сопоставление товаров и сравнение цен."""
from __future__ import annotations

import asyncio
import tempfile

from llm.gateway import LLMGateway
from matcher.matcher import (_normalize, build_compare_result,
                             compare_across_markets, find_counterpart)
from models import Product
from storage.db import Database


def _p(market, ext, title, price, ean=None):
    return Product(marketplace=market, ext_id=ext, title=title, price=price,
                   url=f"https://x/{market}/{ext}", ean=ean)


def test_normalize():
    # порядок слов не важен — сравниваем множества токенов
    assert sorted(_normalize("Кроссовки белые Urban Runner (арт. 12)",
                             strip_parens=True).split()) == \
        sorted(_normalize("Белые кроссовки urban runner").split())
    # похожие названия дают высокую схожесть (max по двум нормализациям)
    from matcher.matcher import _similar
    assert _similar("Кроссовки белые Urban Runner (арт. 12)",
                    "Белые кроссовки urban runner") > 0.7


def test_find_counterpart_by_ean():
    target = _p("ozon", "a", "Маска для сна 3D чёрная", 990, ean="4607000000021")
    candidates = [_p("yandex", "b", "Маска для сна 3D чёрная", 1050,
                     ean="4607000000021"),
                  _p("yandex", "c", "Маска шёлковая чёрная", 1350,
                     ean="4607000000023")]
    assert find_counterpart(target, candidates).ext_id == "b"


def test_find_counterpart_fuzzy():
    target = _p("ozon", "a", "Маска для сна 3D чёрная с пространством для ресниц",
                990)
    candidates = [_p("yandex", "b", "Маска для сна 3D чёрная (пространство для "
                                    "ресниц)", 1050)]
    assert find_counterpart(target, candidates).ext_id == "b"


def test_find_counterpart_none_for_different():
    target = _p("ozon", "a", "Кроссовки белые Urban", 4990)
    candidates = [_p("yandex", "b", "Маска для сна шёлковая чёрная", 1350)]
    assert find_counterpart(target, candidates) is None


def test_build_compare_result():
    ozon = _p("ozon", "a", "Маска 3D", 990)
    yandex = _p("yandex", "b", "Маска 3D", 1050)
    res = build_compare_result(ozon, yandex)
    assert res.ozon == 990 and res.yandex == 1050
    assert res.cheaper == "ozon"
    assert res.diff_percent == 6  # (1050-990)/1050 ≈ 5.7 → 6


def test_compare_across_markets_with_mock_arbiter():
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            llm = LLMGateway(db, api_key="")
            target = _p("ozon", "a", "Маска для сна 3D чёрная", 990,
                        ean="4607000000021")
            candidates = [_p("yandex", "b", "Маска для сна 3D чёрная", 1050,
                             ean="4607000000021")]
            res = await compare_across_markets(llm, target, candidates)
            assert res is not None
            assert res.cheaper == "ozon"
            # разные EAN и разные названия → арбитр говорит «разные товары»
            other = [_p("yandex", "c", "Носки спортивные", 500,
                        ean="4607000000099")]
            assert await compare_across_markets(llm, target, other) is None
        finally:
            await db.close()
    asyncio.run(run())
