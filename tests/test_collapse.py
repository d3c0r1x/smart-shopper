"""Тесты нормализации выдачи: один товар с нескольких площадок → дешевле."""
from __future__ import annotations

from core.orchestrator import _collapse_cross_market
from models import Product


def _p(market, ean, price, title="Маска для сна 3D"):
    return Product(marketplace=market, ext_id=f"{market}-{ean}", title=title,
                   price=price, ean=ean, url=f"https://{market}")


def test_collapse_keeps_cheapest_per_ean():
    items = [
        _p("ozon", "EAN1", 990),
        _p("wb", "EAN1", 780),
        _p("yandex", "EAN1", 1050),
    ]
    out = _collapse_cross_market(items)
    assert len(out) == 1
    assert out[0].marketplace == "wb"
    assert out[0].price == 780


def test_collapse_keeps_distinct_skus_and_no_ean():
    items = [
        _p("ozon", "EAN1", 990),
        _p("wb", "EAN2", 500),
        _p("ozon", None, 1234, title="Без штрихкода"),
    ]
    out = _collapse_cross_market(items)
    assert len(out) == 3
    assert {p.ext_id for p in out} == {"ozon-EAN1", "wb-EAN2", "ozon-None"}


def test_collapse_empty():
    assert _collapse_cross_market([]) == []
