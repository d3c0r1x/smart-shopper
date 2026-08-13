"""Демо-каталог для оффлайн-режима (SHOPPER_DEMO_MODE=1).

Реалистичные товары двух категорий (кроссовки и маски для сна) с ценами,
рейтингами и отзывами на русском. Отзывы содержат ключевые слова
(«пространство для ресниц», «прилегает», «не давит», «белый», «чёрный»…),
чтобы Review Intelligence и mock-провайдер давали осмысленные вердикты.

Это данные ДЕМО: цены и отзывы выдуманы, в реальном режиме адаптеры
ходят в публичные эндпоинты маркетплейсов (см. ozon.py / yandex.py).
"""
from __future__ import annotations

import logging

from adapters.base import BaseAdapter
from models import Product, Review

logger = logging.getLogger(__name__)


def _p(market, ext, title, price, rating, rc, ean, brand, traits, old=None):
    return Product(
        marketplace=market, ext_id=ext, title=title, price=price,
        old_price=old, url=f"https://demo.local/{market}/{ext}",
        rating=rating, reviews_count=rc, ean=ean, brand=brand, traits=traits,
    )


# ── каталог: Ozon ─────────────────────────────────────────────────
OZON = [
    _p("ozon", "snk-001", "Кроссовки белые унисекс Urban Runner", 4990, 4.3, 1540,
       "4607000000011", "Urban", ["белый", "кроссовки", "кожа"], old=6490),
    _p("ozon", "snk-002", "Кроссовки мужские белые Air Flex", 7490, 4.7, 2140,
       "4607000000012", "AirFlex", ["белый", "мужские", "кроссовки"], old=8990),
    _p("ozon", "snk-003", "Кроссовки женские розовые Comfy Run", 6290, 4.5, 980,
       "4607000000013", "Comfy", ["розовый", "женские", "кроссовки"]),
    _p("ozon", "snk-004", "Кеды белые Classic Canvas", 3990, 4.2, 760,
       "4607000000014", "Classic", ["белый", "кеды", "текстиль"]),
    _p("ozon", "snk-005", "Кроссовки чёрные Night Runner", 6890, 4.6, 1830,
       "4607000000015", "Night", ["чёрный", "кроссовки"]),
    _p("ozon", "snk-006", "Кроссовки для бега чёрные Marathon", 7990, 4.8, 3210,
       "4607000000016", "Marathon", ["чёрный", "кроссовки", "для бега"], old=9500),
    _p("ozon", "msk-001", "Маска для сна 3D чёрная с пространством для ресниц", 990,
       4.7, 1240, "4607000000021", "SilkSleep", ["чёрный", "3D", "для ресниц"]),
    _p("ozon", "msk-002", "Маска для сна 3D серая мягкая", 890, 4.5, 680,
       "4607000000022", "SilkSleep", ["серый", "3D"]),
    _p("ozon", "msk-003", "Маска для сна шёлковая чёрная", 1290, 4.6, 920,
       "4607000000023", "SilkSleep", ["чёрный", "шёлк"]),
    _p("ozon", "msk-004", "Маска для сна 3D с охлаждающим гелем", 1190, 4.4, 430,
       "4607000000024", "CoolNight", ["3D", "гель"]),
]

# ── каталог: Яндекс Маркет (те же товары, другие цены — для сравнения) ──
YANDEX = [
    _p("yandex", "snk-001y", "Кроссовки белые Urban Runner", 5290, 4.2, 1100,
       "4607000000011", "Urban", ["белый", "кроссовки"]),
    _p("yandex", "snk-002y", "Кроссовки мужские Air Flex белые", 7190, 4.6, 1700,
       "4607000000012", "AirFlex", ["белый", "мужские", "кроссовки"]),
    _p("yandex", "snk-003y", "Кроссовки женские Comfy Run розовые", 6490, 4.4, 700,
       "4607000000013", "Comfy", ["розовый", "женские", "кроссовки"]),
    _p("yandex", "msk-001y", "Маска для сна 3D чёрная с пространством для ресниц",
       1050, 4.6, 900, "4607000000021", "SilkSleep", ["чёрный", "3D", "для ресниц"]),
    _p("yandex", "msk-002y", "Маска для сна 3D серая", 940, 4.4, 500,
       "4607000000022", "SilkSleep", ["серый", "3D"]),
    _p("yandex", "msk-003y", "Маска шёлковая чёрная для сна", 1350, 4.5, 760,
       "4607000000023", "SilkSleep", ["чёрный", "шёлк"]),
]

# ── каталог: Wildberries (те же товары, цены WB — для сравнения втроём) ──
WILD = [
    _p("wb", "snk-001w", "Кроссовки белые Urban Runner", 4590, 4.1, 2080,
       "4607000000011", "Urban", ["белый", "кроссовки"]),
    _p("wb", "snk-002w", "Кроссовки мужские Air Flex белые", 6990, 4.5, 3120,
       "4607000000012", "AirFlex", ["белый", "мужские", "кроссовки"]),
    _p("wb", "snk-003w", "Кроссовки женские Comfy Run розовые", 5990, 4.3, 1400,
       "4607000000013", "Comfy", ["розовый", "женские", "кроссовки"]),
    _p("wb", "msk-001w", "Маска для сна 3D чёрная с пространством для ресниц",
       780, 4.6, 2150, "4607000000021", "SilkSleep", ["чёрный", "3D", "для ресниц"]),
    _p("wb", "msk-002w", "Маска для сна 3D серая", 720, 4.3, 890,
       "4607000000022", "SilkSleep", ["серый", "3D"]),
    _p("wb", "msk-003w", "Маска шёлковая чёрная для сна", 1150, 4.4, 1300,
       "4607000000023", "SilkSleep", ["чёрный", "шёлк"]),
]

# ── отзывы (ключевые слова — для вердиктов Review Intelligence) ───
_REVIEWS: dict[tuple[str, str], list[Review]] = {}

_R = (
    ("ozon", "msk-001", 5, "Отлично, пространство для ресниц реально есть, ресницы не мнутся.",
     "Купила из-за отзыва про ресницы, не пожалела."),
    ("ozon", "msk-001", 4, "Прилегает плотно, свет не пробивается. Немного давит переносицу в первые ночи.",
     ""),
    ("ozon", "msk-001", 5, "Чёрная, как на фото. Пространство для ресниц большое, спать удобно.",
     ""),
    ("ozon", "msk-001", 4, "Плотно прилегает к лицу, но на широком лице может давить. В целом хорошо.",
     ""),
    ("ozon", "msk-001", 5, "Материал приятный, не жарко. Ресницы целы, прилегание отличное.",
     ""),
    ("ozon", "msk-001", 3, "Чёрная маска, но резинка слабовата — со временем не так плотно прилегает.",
     ""),
    ("ozon", "msk-003", 5, "Шёлк приятный, чёрный цвет глубокий, совсем не давит на глаза.",
     ""),
    ("ozon", "msk-003", 4, "Мягкая, прилегает хорошо. Для ресниц пространства меньше, чем у 3D.",
     ""),
    ("ozon", "msk-002", 4, "Серая маска, мягкая, прилегает нормально. Ресницы не мнутся.",
     ""),
    ("ozon", "msk-004", 4, "Гель охлаждает, прилегает плотно. Пространство для ресниц есть.",
     ""),
    ("ozon", "snk-001", 4, "Белые кроссовки, смотрятся отлично, но быстро пачкаются.",
     "Размер в размер."),
    ("ozon", "snk-001", 5, "Удобные, лёгкие. Белый цвет — классика.",
     ""),
    ("ozon", "snk-002", 5, "Кожа мягкая, белые и красивые. Носятся уже месяц, нареканий нет.",
     ""),
    ("ozon", "snk-002", 4, "Хорошие кроссовки, чуть маломерят — берите на размер больше.",
     ""),
    ("ozon", "snk-005", 4, "Чёрные, строгие, подходят под офис. Неплохие.",
     ""),
    ("ozon", "snk-006", 5, "Для бега отличные: лёгкие, дышащие, амортизация хорошая.",
     ""),
)


def _seed_reviews() -> None:
    for market, ext, rating, text, pros in _R:
        key = (market, ext)
        _REVIEWS.setdefault(key, []).append(Review(
            product_market=market, product_id=ext,
            review_id=f"{market}-{ext}-{len(_REVIEWS.get(key, [])) + 1}",
            rating=rating, author="Покупатель", date="2026-07",
            text=text, pros=pros,
        ))

    # Товары с тем же EAN на других площадках делят пул отзывов: в реальности
    # у одного SKU отзывы одни, какая бы площадка ни показывала карточку.
    # Это делает демо-каталог согласованным с коллапсом кросс-маркетных
    # дублей в оркестраторе (дешёвая WB-карточка получает те же отзывы).
    by_ean: dict[str, list[Review]] = {}
    for p in OZON + YANDEX + WILD:
        if p.ean and _REVIEWS.get((p.marketplace, p.ext_id)):
            by_ean.setdefault(p.ean, []).extend(
                _REVIEWS[(p.marketplace, p.ext_id)])
    for p in OZON + YANDEX + WILD:
        if not p.ean or _REVIEWS.get((p.marketplace, p.ext_id)):
            continue
        src = by_ean.get(p.ean)
        if not src:
            continue
        _REVIEWS[(p.marketplace, p.ext_id)] = [
            Review(product_market=p.marketplace, product_id=p.ext_id,
                   review_id=f"{p.marketplace}-{p.ext_id}-{i + 1}",
                   rating=r.rating, author=r.author, date=r.date,
                   text=r.text, pros=r.pros)
            for i, r in enumerate(src)
        ]


_seed_reviews()


_STOP = {"для", "с", "на", "и", "в", "из", "по", "от", "не", "за",
         "или", "а", "но", "до", "как", "что"}


def _word_score(title: str, traits: list[str], query: str) -> int:
    """Число значимых слов запроса, найденных в названии или признаках."""
    words = [w for w in query.lower().split()
             if len(w) > 2 and w not in _STOP]
    hay = (title + " " + " ".join(traits)).lower()
    return sum(1 for w in words if w in hay)


class _MockAdapter(BaseAdapter):
    """Базовый демо-адаптер на встроенном каталоге."""

    name = "demo"
    marketplace: str = "ozon"

    @property
    def catalog(self) -> list[Product]:
        return {"ozon": OZON, "yandex": YANDEX, "wb": WILD}[self.marketplace]

    def _reviews(self, ext_id: str) -> list[Review]:
        return list(_REVIEWS.get((self.marketplace, ext_id), []))

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        scored = sorted(self.catalog,
                        key=lambda p: _word_score(p.title, p.traits, query),
                        reverse=True)
        return [p for p in scored if _word_score(p.title, p.traits, query) > 0][:limit] \
            or self.catalog[:limit]

    async def get_card(self, ext_id: str) -> Product | None:
        return next((p for p in self.catalog if p.ext_id == ext_id), None)

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list[Review]:
        return self._reviews(ext_id)[:limit]

    async def get_photos(self, ext_id: str) -> list[str]:
        return []  # в демо фото не отдаём — карточки текстовые

    async def aclose(self) -> None:
        return None


class MockOzonAdapter(_MockAdapter):
    name = "ozon"
    marketplace = "ozon"


class MockYandexAdapter(_MockAdapter):
    name = "yandex"
    marketplace = "yandex"


class MockWbAdapter(_MockAdapter):
    name = "wb"
    marketplace = "wb"
