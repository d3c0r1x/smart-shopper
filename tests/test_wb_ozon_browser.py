# -*- coding: utf-8 -*-
"""Юнит-тесты браузерных адаптеров Ozon и Wildberries (канал 1).

Парсинг карточек выделен в чистые функции (_cards_from_raw, _parse_price) —
тестируем их на сэмплах, повторяющих реальную структуру DOM выдачи.
Сеть/браузер в тестах не используются.
"""
from adapters.ozon_browser import OzonBrowserAdapter
from adapters.wb_browser import WbBrowserAdapter


def test_ozon_price_parse():
    assert OzonBrowserAdapter._parse_price("253\u2009₽") == 253
    assert OzonBrowserAdapter._parse_price("1\u2009290\u2009₽") == 1290
    assert OzonBrowserAdapter._parse_price("1 206 ₽") == 1206
    assert OzonBrowserAdapter._parse_price("нет цены") is None


def test_ozon_cards_from_raw():
    raw = [
        {"label": "Маска для сна женская мужская 3D",
         "price": "253\u2009₽",
         "href": "/product/maska-dlya-sna-zhenskaya-muzhskaya-3d-3606417783/?at=xxx"},
        {"label": "Распродажа",  # промо-бейдж без нормальной цены — пропустим
         "price": "",
         "href": "/product/bad-1/"},
        {"label": "Shumin Маска для сна черная",
         "price": "408\u2009₽",
         "href": "/product/shumin-maska-dlya-sna-chernaya-1563221610/"},
    ]
    cards = OzonBrowserAdapter()._cards_from_raw(raw)
    assert len(cards) == 2
    first = cards[0]
    assert first.marketplace == "ozon"
    assert first.ext_id == "maska-dlya-sna-zhenskaya-muzhskaya-3d-3606417783"
    assert first.price == 253
    assert first.url.startswith("https://www.ozon.ru/product/")


def test_ozon_empty_raw():
    assert OzonBrowserAdapter()._cards_from_raw([]) == []


def test_wb_price_parse():
    # в элементе цены WB лежит «489 ₽2 940 ₽−83%» — берём первое число
    assert WbBrowserAdapter._parse_price("489\u2009₽2\u2009940\u2009₽−83%") == 489
    assert WbBrowserAdapter._parse_price("1\u2009206\u2009₽3\u2009110\u2009₽−60%") == 1206
    assert WbBrowserAdapter._parse_price("—") is None


def test_wb_cards_from_raw():
    raw = [
        {"label": "Маска для сна 3D, усовершенствованная HOME+",
         "price": "489\u2009₽2\u2009940\u2009₽−83%",
         "href": "https://www.wildberries.ru/catalog/151936475/detail.aspx"},
        {"label": "Маска для сна SHUMIN",
         "price": "266\u2009₽800\u2009₽−66%",
         "href": "https://www.wildberries.ru/catalog/327497598/detail.asp"},
        {"label": "", "price": "", "href": ""},
    ]
    cards = WbBrowserAdapter()._cards_from_raw(raw)
    assert len(cards) == 2
    first = cards[0]
    assert first.marketplace == "wb"
    assert first.ext_id == "151936475"
    assert first.price == 489
    assert "wildberries.ru/catalog/151936475" in first.url


def test_wb_empty_raw():
    assert WbBrowserAdapter()._cards_from_raw([]) == []
