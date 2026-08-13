"""Адаптеры маркетплейсов с единым контрактом (PRD, раздел 4, пункт 7).

Контракт: search() → карточки, get_card(), get_reviews(), get_photos().
Выбор:
  * демо-режим → MockOzonAdapter + MockYandexAdapter (встроенный каталог);
  * реальный режим → OzonAdapter (composer-api web+mobile);
  * Яндекс: если задан SHOPPER_PROXY и установлен Playwright — браузерный
    канал (YandexBrowserAdapter, реальные данные через настоящий браузер);
    иначе — YandexMarketAdapter (JSON-эндпоинты/JSON-LD web-версии).
"""
from __future__ import annotations

import config

from adapters.demo import MockOzonAdapter, MockYandexAdapter
from adapters.ozon import OzonAdapter
from adapters.yandex import YandexMarketAdapter


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def build_adapters(demo: bool | None = None) -> list:
    """Собирает список адаптеров по конфигу (демо-режим или реальный)."""
    demo = config.DEMO_MODE if demo is None else demo
    if demo:
        return [MockOzonAdapter(), MockYandexAdapter()]
    yandex: object
    if config.PROXY and _has_playwright():
        from adapters.yandex_browser import YandexBrowserAdapter
        yandex = YandexBrowserAdapter(proxy=config.PROXY)
    else:
        yandex = YandexMarketAdapter(region_id=config.YM_REGION)
    return [OzonAdapter(), yandex]


__all__ = [
    "OzonAdapter",
    "YandexMarketAdapter",
    "YandexBrowserAdapter",
    "MockOzonAdapter",
    "MockYandexAdapter",
    "build_adapters",
]
