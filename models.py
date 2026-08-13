"""Единые модели данных проекта «Умный Шоппер» (pydantic).

Все слои — адаптеры маркетплейсов, Review Intelligence, оркестратор,
хранилище — обмениваются этими моделями, чтобы бот не знал внутренностей
отдельных площадок (контракт «адаптеров» из PRD, раздел 4).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Marketplace = Literal["ozon", "yandex", "wb"]


class Product(BaseModel):
    """Товар в поисковой выдаче маркетплейса."""

    marketplace: Marketplace
    ext_id: str
    title: str
    price: int  # рубли, целые
    old_price: int | None = None
    url: str
    rating: float | None = None
    reviews_count: int = 0
    photo_url: str = ""
    ean: str | None = None  # штрихкод, если есть в карточке
    brand: str = ""
    traits: list[str] = Field(default_factory=list)  # «чёрный», «3D-маска»…

    def discount_percent(self) -> int | None:
        if not self.old_price or self.old_price <= 0:
            return None
        return round((1 - self.price / self.old_price) * 100)


class Review(BaseModel):
    """Отзыв о товаре."""

    product_market: Marketplace
    product_id: str
    review_id: str
    rating: int  # 1..5
    author: str = ""
    date: str = ""
    text: str
    pros: str = ""
    cons: str = ""
    photos: list[str] = Field(default_factory=list)
    bought_here: bool = False  # «куплен на маркетплейсе»


class RequirementVerdict(BaseModel):
    """Проверка одного требования пользователя по отзывам."""

    requirement: str
    verdict: Literal["confirmed", "rejected", "no_data"]
    mentions: int = 0
    quote: str = ""


class ReviewAnalysis(BaseModel):
    """Итог анализа отзывов одного товара."""

    product_market: Marketplace
    product_id: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    verdicts: list[RequirementVerdict] = Field(default_factory=list)
    summary: str = ""


class SearchConstraints(BaseModel):
    """Ограничения, извлечённые LLM из свободной реплики пользователя."""

    query: str  # поисковый запрос для маркетплейсов
    must_have: list[str] = Field(default_factory=list)  # обязательные требования
    nice_to_have: list[str] = Field(default_factory=list)
    max_price: int | None = None
    min_rating: float | None = None
    sort_by: Literal["relevance", "price_asc", "rating"] = "relevance"


class VisionDescription(BaseModel):
    """Структурированное описание товара с фотографии (сценарий 1)."""

    category: str = ""
    gender: str = ""
    color: str = ""
    material: str = ""
    silhouette: str = ""
    details: list[str] = Field(default_factory=list)  # только если уверены
    search_queries: list[str] = Field(default_factory=list)  # 2–3 запроса на русском


class CompareResult(BaseModel):
    """Строка сравнения цен одного товара на площадках (Ozon/Яндекс/WB)."""

    title: str
    ozon: int | None = None
    yandex: int | None = None
    wb: int | None = None
    ozon_url: str = ""
    yandex_url: str = ""
    wb_url: str = ""
    cheaper: Marketplace | None = None
    diff_percent: int | None = None  # насколько % дешевле


class SessionState(BaseModel):
    """Сжатое состояние диалога (память сессии, раздел 5 PRD)."""

    mode: str = "idle"  # idle | photo | search | compare
    last_query: str = ""
    constraints: SearchConstraints | None = None
    last_results: list[Product] = Field(default_factory=list)
    focus_product: Product | None = None
    history: list[str] = Field(default_factory=list)  # сжатые реплики
    default_market: str = "both"  # both | ozon | yandex | wb
