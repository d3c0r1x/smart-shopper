"""Адаптеры маркетплейсов с единым контрактом (PRD, раздел 4, пункт 7).

Контракт: search() → карточки, get_card(), get_reviews(), get_photos().
Выбор: демо-режим → MockOzonAdapter + MockYandexAdapter (встроенный каталог);
реальный режим → OzonAdapter (composer-api) + YandexAdapter (web-версия).
"""
from __future__ import annotations

import config

from adapters.demo import MockOzonAdapter, MockYandexAdapter
from adapters.ozon import OzonAdapter
from adapters.yandex import YandexMarketAdapter


def build_adapters(demo: bool | None = None) -> list:
    """Собирает список адаптеров по конфигу (демо-режим или реальный)."""
    demo = config.DEMO_MODE if demo is None else demo
    if demo:
        return [MockOzonAdapter(), MockYandexAdapter()]
    return [OzonAdapter(), YandexMarketAdapter(region_id=config.YM_REGION)]


__all__ = [
    "OzonAdapter",
    "YandexMarketAdapter",
    "MockOzonAdapter",
    "MockYandexAdapter",
    "build_adapters",
]
