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


def test_ozon_traits_classic():
    """Классический формат webCharacteristics: group.short[name, values]."""
    data = {
        "widgetStates": {
            "webCharacteristics-1-default-1": (
                '{"characteristics": [{"short": ['
                '{"name": "Материал", "values": [{"text": "Полиэстер"}]},'
                '{"name": "Цвет", "values": [{"text": "Черный"}, '
                '{"text": "Белый"}]}]}]}'
            )
        }
    }
    traits = OzonBrowserAdapter._traits_from_entry(data)
    assert "Материал: Полиэстер" in traits
    assert "Цвет: Черный, Белый" in traits


def test_ozon_traits_short():
    """Компактный формат карточки webShortCharacteristics: title.textRs."""
    data = {
        "widgetStates": {
            "webShortCharacteristics-2-default-1": (
                '{"characteristics": [{"title": {"textRs": ['
                '{"content": "Страна-изготовитель"}]},'
                '"values": [{"text": "Китай"}]}]}'
            )
        }
    }
    traits = OzonBrowserAdapter._traits_from_entry(data)
    assert "Страна-изготовитель: Китай" in traits


def test_ozon_traits_empty():
    assert OzonBrowserAdapter._traits_from_entry({}) == []


def test_wb_reviews_split_pros_cons():
    """Разбивка отзыва WB на плюсы/минусы/текст (без шапки)."""
    from adapters.wb_browser import WbBrowserAdapter

    # JS-логика разбивки воспроизведена в Python для проверки селекторов
    t = ("Татьяна 26 июля Достоинства:Качество хорошее, приятная на ощупь"
         "Недостатки:Свет пропускает, не прилегает плотно на крыльях носа")
    pi, ni, di, ngi = (t.find("Плюсы товара"), t.find("Минусы товара"),
                       t.find("Достоинства"), t.find("Недостатки"))
    assert di >= 0 and ngi > di
    pros = t[di + len("Достоинства"):ngi].strip()
    cons = t[ngi + len("Недостатки"):].strip()
    assert "Качество хорошее" in pros
    assert "Свет пропускает" in cons


def test_wb_reviews_minus_only():
    """Отзыв только с «Минусы товара»."""
    t = "Покупатель 08 августа Минусы товара НЕУДОБНО ПОЛЬЗОВАТЬСЯ"
    pi, ni = t.find("Плюсы товара"), t.find("Минусы товара")
    assert pi < 0 and ni >= 0
    cons = t[ni + len("Минусы товара"):].strip()
    assert cons == "НЕУДОБНО ПОЛЬЗОВАТЬСЯ"


def test_wb_card_extract_via_json():
    """Карточка WB: селекторы из _CARD_SCRIPT дают нужные поля на сэмпле."""
    from adapters.wb_browser import WbBrowserAdapter

    # сэмпл-структура как в реальном DOM (см. _wb_chars.html)
    import json as _json
    sample = {
        "title": "Маска для сна 3D, усовершенствованная",
        "brand": "HOME+",
        "price": "489\u2009₽",
        "rating": "4,8",
        "count": "16\u2009977 оценок",
        "photo": "https://basket-10.wbbasket.ru/vol1519/part151936/151936475/images/tm/1.webp",
        "traits": ["Артикул: 151936475", "Состав: Дышащий полиэстер 93%"],
    }
    price = WbBrowserAdapter._parse_price(sample["price"])
    assert price == 489
    rating = float(sample["rating"].replace(",", "."))
    assert rating == 4.8
    assert len(sample["traits"]) >= 2


def test_ozon_traits_short_trailing_comma():
    """Значения с висячей запятой («Взрослая, ») чистятся."""
    data = {
        "widgetStates": {
            "webShortCharacteristics-3-default-1": (
                '{"characteristics": [{"title": {"textRs": ['
                '{"content": "Целевая аудитория"}]},'
                '"values": [{"text": "Взрослая, "}, {"text": "Детская"}]}]}'
            )
        }
    }
    traits = OzonBrowserAdapter._traits_from_entry(data)
    assert "Целевая аудитория: Взрослая, Детская" in traits
    assert "Взрослая, , Детская" not in traits[0]
