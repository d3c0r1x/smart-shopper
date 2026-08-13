"""Адаптеры маркетплейсов с единым контрактом (PRD, раздел 4, пункт 7).

Контракт: search() → карточки, get_card(), get_reviews(), get_photos().
Выбор:
  * демо-режим → MockOzonAdapter + MockYandexAdapter + MockWbAdapter
    (встроенный каталог, цены на трёх площадках);
  * реальный режим: если задан SHOPPER_PROXY и установлен Playwright —
    браузерный канал для всех трёх площадок (OzonBrowserAdapter,
    YandexBrowserAdapter, WbBrowserAdapter): настоящий браузер + прокси
    проходит антибот (проверено с этого IP: Ozon челлендж решается,
    WB/Яндекс отдают DOM выдачи);
  * иначе — HTTP-адаптеры ozon.py / yandex.py (composer-api / JSON-LD),
    WB без браузера с этого IP не отдаёт данные (429/403).
"""
from __future__ import annotations

import config

from adapters.demo import MockOzonAdapter, MockWbAdapter, MockYandexAdapter
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
        return [MockOzonAdapter(), MockYandexAdapter(), MockWbAdapter()]
    browser_ok = bool(config.PROXY) and _has_playwright()
    if browser_ok:
        from adapters.ozon_browser import OzonBrowserAdapter
        from adapters.wb_browser import WbBrowserAdapter
        from adapters.yandex_browser import YandexBrowserAdapter
        return [
            OzonBrowserAdapter(proxy=config.PROXY),
            YandexBrowserAdapter(proxy=config.PROXY),
            WbBrowserAdapter(proxy=config.PROXY),
        ]
    return [OzonAdapter(), YandexMarketAdapter(region_id=config.YM_REGION)]


__all__ = [
    "OzonAdapter",
    "YandexMarketAdapter",
    "OzonBrowserAdapter",
    "YandexBrowserAdapter",
    "WbBrowserAdapter",
    "MockOzonAdapter",
    "MockYandexAdapter",
    "MockWbAdapter",
    "build_adapters",
]
