"""Юнит-тесты канала 1 (публичные JSON-эндпоинты SPA).

Проверяют: парсинг widgetStates Ozon, JSON-поиск Яндекса, классификацию
зафиксированных эндпоинтов, выбор эндпоинта, fallback-поведение при
307/403 (антибот) — без реальной сети.
"""
from __future__ import annotations

import asyncio
import json

from adapters.capture import _classify, load_captured, pick_endpoint
from adapters.ozon import OzonAdapter, _find_reviews, _iter_widget_states
from adapters.yandex import YandexMarketAdapter, _find_product_objs
from models import Review

# ── сэмпл реальной структуры composer-api (widgetStates) ──────────
OZON_SEARCH_SAMPLE = {
    "widgetStates": {
        "searchResultsV2-1": json.dumps({
            "items": [
                {"product": {
                    "id": 123456789,
                    "title": "Маска для сна 3D чёрная с пространством для ресниц",
                    "price": {"price": "990.00"},
                    "oldPrice": {"value": "1290.00"},
                    "rating": {"value": 4.7},
                    "feedbackCount": {"value": 1240},
                    "brand": "SilkSleep",
                }},
                {"product": {
                    "id": 987654321,
                    "title": "Маска для сна шёлковая",
                    "price": {"price": 1290},
                    "rating": {"value": 4.5},
                    "feedbackCount": {"value": 320},
                }},
            ]
        }),
        "header-1": json.dumps({"title": "Поиск"}),
    }
}

OZON_REVIEWS_SAMPLE = {
    "widgetStates": {
        "reviewsList-1": json.dumps({
            "items": [
                {"id": "r1", "rating": 5, "text": "Пространство для ресниц реально есть",
                 "author": {"name": "Иван"}, "date": "2026-08-01"},
                {"id": "r2", "rating": 4, "text": "Прилегает плотно, но давит переносицу",
                 "author": {"name": "Мария"}, "date": "2026-07-20"},
            ]
        })
    }
}

YM_SEARCH_SAMPLE = {
    "items": [
        {"id": "ym-1", "name": "Маска для сна 3D", "price": 1050,
         "rating": 4.6, "url": "https://market.yandex.ru/product/ym-1"},
        {"id": "ym-2", "name": "Маска шёлковая", "price": {"value": 1490},
         "rating": {"value": 4.4}, "url": "https://market.yandex.ru/product/ym-2"},
    ]
}

CAPTURED_SAMPLE = {
    "market": "ozon",
    "query": "маска",
    "endpoints": [
        {"url": "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fsearch%2F",
         "method": "GET", "status": 200,
         "request_headers": {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}},
        {"url": "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F1%2Freviews%2F",
         "method": "GET", "status": 200,
         "request_headers": {"User-Agent": "Mozilla/5.0"}},
    ]
}


# ── парсинг Ozon ──────────────────────────────────────────────────
def test_ozon_parse_search_widget_states():
    a = OzonAdapter(auto_bootstrap=False)
    products = a._parse_response(OZON_SEARCH_SAMPLE, limit=5)
    assert len(products) == 2
    p = products[0]
    assert p.marketplace == "ozon"
    assert p.ext_id == "123456789"
    assert "ресниц" in p.title
    assert p.price == 990
    assert p.old_price == 1290
    assert p.rating == 4.7
    assert p.reviews_count == 1240
    assert p.brand == "SilkSleep"
    assert p.url.startswith("https://www.ozon.ru/product/")


def test_ozon_parse_reviews_widget_states():
    reviews = OzonAdapter(auto_bootstrap=False)._parse_reviews(
        OZON_REVIEWS_SAMPLE, limit=5)
    assert len(reviews) == 2
    r = reviews[0]
    assert isinstance(r, Review)
    assert r.rating == 5
    assert "ресниц" in r.text
    assert r.author == "Иван"


def test_find_reviews_recursive():
    acc: list[dict] = []
    for _k, state in _iter_widget_states(OZON_REVIEWS_SAMPLE):
        _find_reviews(state, acc)
    assert len(acc) == 2


def test_iter_widget_states_skips_bad_json():
    data = {"widgetStates": {"a": "{not json", "b": "[unclosed"}}
    states = list(_iter_widget_states(data))
    assert len(states) == 0  # оба невалидны


# ── парсинг Яндекса ───────────────────────────────────────────────
def test_yandex_parse_json_search():
    a = YandexMarketAdapter()
    products = a._parse_json_search(YM_SEARCH_SAMPLE, limit=5, query="маска")
    assert len(products) == 2
    p = products[0]
    assert p.marketplace == "yandex"
    assert p.price == 1050
    assert p.rating == 4.6
    # цена из dict
    assert products[1].price == 1490


def test_find_product_objs_recursive():
    acc: list[dict] = []
    _find_product_objs(YM_SEARCH_SAMPLE, acc)
    assert len(acc) == 2


# ── классификация и выбор эндпоинтов ──────────────────────────────
def test_classify_endpoints():
    assert _classify("https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/") == "search"
    assert _classify("https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/1/reviews/") == "reviews"
    assert _classify("https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/1/") == "card"
    assert _classify("https://market.yandex.ru/api/render-lazy") is None


def test_pick_endpoint_prefers_200():
    eps = [
        {"url": "https://x/1", "status": 403},
        {"url": "https://x/2", "status": 200},
    ]
    picked = pick_endpoint({"search": eps, "card": [], "reviews": [], "other": []},
                           "search")
    assert picked["url"] == "https://x/2"


def test_load_captured(tmp_path, monkeypatch):
    from adapters import capture as cap_mod
    d = tmp_path / "captured"
    d.mkdir()
    (d / "ozon.json").write_text(json.dumps(CAPTURED_SAMPLE, ensure_ascii=False),
                                 encoding="utf-8")
    monkeypatch.setattr(cap_mod, "CAPTURED_DIR", d)
    captured = load_captured("ozon")
    assert captured["search"], "должен найти search-эндпоинт"
    assert captured["reviews"], "должен найти reviews-эндпоинт"
    assert load_captured("yandex") is None  # файла нет


# ── fallback-поведение при антиботе ───────────────────────────────
class FakeTransport:
    """Подмена транспорта: возвращает заданные ответы по URL."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self._responses = responses
        self.calls: list[str] = []

    async def get(self, url, *, params=None, headers=None, cookies=None):
        key = url.split("?")[0]
        self.calls.append(url)
        return self._responses.get(key, (403, "{}"))

    async def aclose(self):
        pass


def test_ozon_falls_back_to_mobile_on_307():
    web = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
    mobile = "https://api.ozon.ru/composer-api.bx/page/json/v2"
    ft = FakeTransport({
        web: (307, "redirect"),
        mobile: (200, json.dumps(OZON_SEARCH_SAMPLE)),
    })
    a = OzonAdapter(transport=ft, auto_bootstrap=False)
    products = asyncio.run(a.search("маска для сна", limit=5))
    assert len(products) == 2
    assert any(mobile in c for c in ft.calls), "должен был дойти до mobile"


def test_ozon_returns_empty_when_all_blocked():
    ft = FakeTransport({
        "https://www.ozon.ru/api/composer-api.bx/page/json/v2": (403, "challenge"),
        "https://api.ozon.ru/composer-api.bx/page/json/v2": (307, "redirect"),
    })
    a = OzonAdapter(transport=ft, auto_bootstrap=False)
    assert asyncio.run(a.search("маска", limit=5)) == []


def test_yandex_web_fallback_used_when_no_capture(monkeypatch, tmp_path):
    from adapters import yandex as ymod
    d = tmp_path / "captured"
    d.mkdir()
    monkeypatch.setattr(ymod, "load_captured", lambda market: None)
    # без capture: _search_json не вызывается, идёт _search_web
    ft = FakeTransport({"https://market.yandex.ru/search": (200, "<html/>")})
    a = YandexMarketAdapter(transport=ft)
    products = asyncio.run(a.search("маска", limit=5))
    assert products == []
    assert any("search" in c for c in ft.calls)
