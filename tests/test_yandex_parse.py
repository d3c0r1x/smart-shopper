"""Ревью-фикс (ruff F821): парсер JSON Яндекса не падал с NameError.

Раньше `_parse_json_search` использовал `query`, которого не было в скоупе:
карточка без `url` роняла поиск в рантайме. Теперь query передаётся явно,
и для карточек без url строится фолбэк-ссылка поиска.
"""
from __future__ import annotations

from urllib.parse import quote

from adapters.yandex import SEARCH_URL, YandexMarketAdapter


def test_parse_json_search_without_url_builds_fallback():
    a = YandexMarketAdapter()
    data = {"items": [{"name": "Маска", "price": 123, "id": "x1"}]}
    out = a._parse_json_search(data, limit=10, query="маска для сна")
    assert len(out) == 1
    p = out[0]
    assert p.title == "Маска"
    assert p.price == 123
    assert p.ext_id == "x1"
    # url отсутствует у карточки — собирается ссылка поиска с запросом
    assert p.url == f"{SEARCH_URL}?text={quote('маска для сна')}"


def test_parse_json_search_keeps_explicit_url():
    a = YandexMarketAdapter()
    data = {"items": [{"name": "Маска", "price": 999, "id": "x2",
                       "url": "https://market.yandex.ru/product/x2"}]}
    out = a._parse_json_search(data, limit=10, query="маска")
    assert len(out) == 1
    assert out[0].url == "https://market.yandex.ru/product/x2"
