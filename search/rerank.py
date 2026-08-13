"""search/rerank.py — двухэтапный реранкер (ТЗ §2 «Reranking»).

Этап 1 (retrieval) — выдача маркетплейсов: широкий пул кандидатов.
Этап 2 (rerank) — гибридный скор:
    score = w1·семантика + w2·лексика + w3·структура (+ крошечный тай-брейк)

  семантика — косинус эмбеддингов запроса и «название + бренд + признаки»;
  лексика   — быстрое совпадение токенов запроса в названии (rapidfuzz);
  структура — соблюдение жёстких ограничений (цена/бренд/рейтинг).

Первый этап детерминированный и бесплатный (локально, без LLM); финальное
слово оставляет LLM-ранжирование/анализ отзывов (второй этап в оркестраторе).
При недоступности эмбеддингов скор автоматически вырождается в
«лексика + структура» (семантический вес перераспределяется на лексику).
"""
from __future__ import annotations

import logging

from matcher.matcher import _normalize, _similar
from models import Product
from search.structfilter import StructFilters

logger = logging.getLogger(__name__)


def _norm_tokens(text: str) -> set[str]:
    return set(_normalize(text).split())


def _popularity_norm(p: Product, max_val: float) -> float:
    raw = (p.rating or 0) * (1 + (p.reviews_count or 0)) ** 0.2
    return raw / max_val if max_val > 0 else 0.0


class HybridReranker:
    """Гибридный реранкер: семантика + лексика + структурные ограничения."""

    def __init__(self, embedder, weights: tuple[float, float, float] = (0.45, 0.35, 0.20)) -> None:
        self._embedder = embedder
        self._w = weights

    @staticmethod
    def product_text(p: Product) -> str:
        """Текст для эмбеддинга: название + бренд + признаки карточки."""
        parts = [p.title]
        if p.brand:
            parts.append(p.brand)
        parts.extend(t for t in (p.traits or []) if t)
        return " | ".join(parts)

    @staticmethod
    def lexical_score(query: str, p: Product) -> float:
        """0..1: покрытие токенов запроса в названии + fuzzy-ratio."""
        q_tokens = _norm_tokens(query)
        if not q_tokens:
            return 0.5
        t_tokens = _norm_tokens(p.title)
        hit = len(q_tokens & t_tokens) / len(q_tokens)
        ratio = _similar(query, p.title)
        return 0.65 * hit + 0.35 * ratio

    @staticmethod
    def structural_score(filters: StructFilters, p: Product) -> float:
        """1.0 при полном соблюдении ограничений; иначе штраф (не ниже 0)."""
        if not filters:
            return 1.0
        score = 1.0
        if filters.max_price and p.price and p.price > filters.max_price:
            score -= 0.5
        if filters.min_price and p.price and p.price < filters.min_price:
            score -= 0.5
        if filters.min_rating and p.rating is not None \
                and p.rating < filters.min_rating:
            score -= 0.5
        if filters.brand and p.brand \
                and filters.brand.lower() not in p.brand.lower():
            score -= 0.5
        return max(score, 0.0)

    def score_product(self, query: str, filters: StructFilters,
                      p: Product, semantic: float | None) -> float:
        s = 0.0
        w_sem, w_lex, w_struct = self._w
        if semantic is not None:
            s += w_sem * semantic
        else:
            # эмбеддинги недоступны — вес семантики уходит на лексику
            w_lex += w_sem
        s += w_lex * self.lexical_score(query, p)
        s += w_struct * self.structural_score(filters, p)
        return s

    async def rerank(self, query: str, filters: StructFilters,
                     products: list[Product]) -> list[tuple[Product, float]]:
        """Полный гибридный скор: семантика (async) + лексика + структура."""
        if not products:
            return []
        sem = None
        if self._embedder is not None:
            sem = await self._embedder.similarity(
                query, [self.product_text(p) for p in products])
        return self.score_sync(query, filters, products, sem)

    def score_sync(self, query: str, filters: StructFilters,
                   products: list[Product],
                   semantic: list[float] | None = None
                   ) -> list[tuple[Product, float]]:
        """Синхронный скор (фолбэк без эмбеддингов, для eval и тестов)."""
        if not products:
            return []
        max_pop = max(((p.rating or 0) * (1 + (p.reviews_count or 0)) ** 0.2
                       for p in products), default=0.0)
        scored = []
        for i, p in enumerate(products):
            s = self.score_product(query, filters, p,
                                   semantic[i] if semantic is not None else None)
            # тай-брейк: при равных скорах популярнее — выше
            s += 1e-6 * _popularity_norm(p, max_pop)
            scored.append((p, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    async def aclose(self) -> None:
        if self._embedder is not None:
            await self._embedder.aclose()
