"""Тесты детерминированной структурной фильтрации (search/structfilter.py)."""
from __future__ import annotations

from search.structfilter import StructFilters, apply_structural, parse_structural
from models import Product


def _p(title, price, rating=4.5, brand="Urban"):
    return Product(marketplace="ozon", ext_id="x", title=title, price=price,
                   rating=rating, brand=brand, url="https://x")


# ── извлечение цены ──────────────────────────────────────────────

def test_max_price_digits():
    sf = parse_structural("наушники до 3000 рублей")
    assert sf.max_price == 3000
    assert sf.query == "наушники"


def test_max_price_without_unit():
    sf = parse_structural("маска для сна до 1000")
    assert sf.max_price == 1000


def test_max_price_words():
    sf = parse_structural("кроссовки не дороже пяти тысяч рублей")
    assert sf.max_price == 5000


def test_min_price():
    sf = parse_structural("наушники от 2000 рублей")
    assert sf.min_price == 2000
    assert sf.max_price is None


def test_price_range():
    sf = parse_structural("телефон от 10000 до 20000 рублей")
    assert sf.min_price == 10000
    assert sf.max_price == 20000


def test_price_range_dash():
    sf = parse_structural("клавиатура 3000-5000 руб")
    assert sf.min_price == 3000
    assert sf.max_price == 5000


def test_price_with_space_separator():
    sf = parse_structural("холодильник до 30 000 руб")
    assert sf.max_price == 30000


def test_no_price():
    sf = parse_structural("кроссовки белые")
    assert sf.max_price is None
    assert sf.min_price is None
    assert sf.query == "кроссовки белые"


# ── рейтинг ──────────────────────────────────────────────────────

def test_min_rating_explicit():
    sf = parse_structural("наушники с рейтингом не ниже 4.5")
    assert sf.min_rating == 4.5


def test_min_rating_star():
    sf = parse_structural("маска рейтинг от 4 звёзд")
    assert sf.min_rating == 4.0


# ── бренд ────────────────────────────────────────────────────────

def test_brand_known_word():
    sf = parse_structural("наушники sony беспроводные")
    assert sf.brand == "sony"


def test_brand_explicit():
    sf = parse_structural("хочу бренд Samsung")
    assert sf.brand == "samsung"


def test_brand_none():
    sf = parse_structural("маска для сна 3d")
    assert sf.brand is None


# ── очистка запроса ──────────────────────────────────────────────

def test_clean_query_only_price_removed():
    sf = parse_structural("маска для сна до 1000 рублей чёрная")
    assert sf.max_price == 1000
    # ценовая фраза вырезана, значимые слова остались
    assert "до" not in sf.query
    assert "маска" in sf.query
    assert "чёрная" in sf.query


def test_empty_input():
    sf = parse_structural("")
    assert sf.max_price is None
    assert sf.query == ""


def test_garbage_does_not_crash():
    sf = parse_structural("до   до  до")
    assert sf.query  # не падает, запрос остаётся


# ── жёсткие фильтры ──────────────────────────────────────────────

def test_apply_structural_max_price():
    items = [_p("a", 900), _p("b", 1500)]
    out = apply_structural(items, StructFilters(max_price=1000))
    assert [p.title for p in out] == ["a"]


def test_apply_structural_keeps_unknown_price():
    items = [_p("a", 0), _p("b", 2000)]
    out = apply_structural(items, StructFilters(max_price=1000))
    # у товара с price=0 нет данных — не отбрасываем
    assert len(out) == 1


def test_apply_structural_brand():
    items = [_p("a", 100, brand="Sony"), _p("b", 100, brand="Urban")]
    out = apply_structural(items, StructFilters(brand="sony"))
    assert [p.title for p in out] == ["a"]
