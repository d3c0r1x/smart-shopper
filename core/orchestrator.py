"""Оркестратор диалога («мозг», PRD раздел 4).

Поток хода: вход → определение намерения → план (инструменты) → исполнение
адаптерами → возврат фактов модели → финальный ответ/карточки. Здесь же —
память сессии (сжатое состояние) и уточнения «подешевле / только чёрные»,
которые применяются к прошлому поиску, а не начинают его заново (PRD §5).

Если дневной лимит LLM исчерпан — выбрасывается BudgetExceeded, бот
показывает честное сообщение с цифрами (PRD раздел 7).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import config
from llm.gateway import BudgetExceeded, LLMGateway
from llm.prompts import CONSTRAINTS_PROMPT, RANK_PROMPT
from llm.schemas import RankResult
from models import (Product, ReviewAnalysis, SearchConstraints, SessionState,
                    VisionDescription)
from review.intelligence import analyze_reviews, rank_by_verdicts

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], Awaitable[None]]


@dataclass
class SearchOutcome:
    """Результат поискового сценария: топ, анализы, все кандидаты."""

    constraints: SearchConstraints
    top: list[Product] = field(default_factory=list)
    analyses: dict[str, ReviewAnalysis] = field(default_factory=dict)
    all_candidates: list[Product] = field(default_factory=list)
    used_llm: bool = False


def _dedupe(products: list[Product]) -> list[Product]:
    seen: set[tuple[str, str]] = set()
    out: list[Product] = []
    for p in products:
        key = (p.marketplace, p.ext_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _prefilter(products: list[Product], c: SearchConstraints) -> list[Product]:
    """Формальный префильтр: цена и обязательные признаки из карточки."""
    out = []
    for p in products:
        if c.max_price is not None and p.price and p.price > c.max_price:
            continue
        if c.min_rating is not None and p.rating is not None \
                and p.rating < c.min_rating:
            continue
        out.append(p)
    return out


def _popularity(p: Product) -> float:
    return (p.rating or 0) * (1 + (p.reviews_count or 0)) ** 0.2


class Orchestrator:
    """Выполняет сценарии и ведёт состояние сессии."""

    def __init__(self, db, llm: LLMGateway, adapters: list) -> None:
        self._db = db
        self._llm = llm
        self._adapters = adapters

    # ── сценарий 2: текстовый поиск с требованиями ─────────────────
    async def search_with_constraints(
        self,
        user_id: int,
        user_text: str,
        state: SessionState,
        progress: ProgressCb | None = None,
        markets: list[str] | None = None,
    ) -> SearchOutcome:
        if progress:
            await progress("🔎 Понимаю запрос…")
        constraints = await self._extract_constraints(user_text, state)
        outcome = await self._execute_search(constraints, progress, markets)
        outcome.used_llm = True

        # память сессии (PRD §5): сжатое состояние для уточнений
        state.mode = "search"
        state.last_query = constraints.query
        state.constraints = constraints
        state.last_results = outcome.top
        state.history = (state.history + [user_text.strip()[:120]])[-6:]
        await self._db.save_session(user_id, state)
        return outcome

    async def _extract_constraints(self, user_text: str,
                                   state: SessionState) -> SearchConstraints:
        """Извлечение ограничений с учётом контекста прошлого поиска."""
        context = ""
        if state.constraints and state.mode == "search":
            prev = state.constraints
            context = (
                f"\n\nКОНТЕКСТ ПРОШЛОГО ПОИСКА (пользователь уточняет его):\n"
                f"запрос: {prev.query}; требования: {prev.must_have}; "
                f"макс. цена: {prev.max_price}"
            )
        prompt = (CONSTRAINTS_PROMPT
                  + f"\n\nПОЛЬЗОВАТЕЛЬ: {user_text}" + context)
        return await self._llm.structured(kind="constraints",
                                          prompt=prompt,
                                          schema=SearchConstraints)

    async def _execute_search(self, constraints: SearchConstraints,
                              progress: ProgressCb | None,
                              markets: list[str] | None = None) -> SearchOutcome:
        if progress:
            await progress("🔎 Ищу на Ozon, Яндексе и Wildberries…")

        adapters = [a for a in self._adapters
                    if markets is None or a.name in markets]
        # Последовательно с вежливой паузой: браузерные каналы (Ozon/Яндекс/WB)
        # при одновременном старте роняют антибот-челлендж Ozon (проверено).
        candidates: list[Product] = []
        for adapter in adapters:
            try:
                candidates.extend(
                    await self._search_market(adapter, constraints.query))
            except Exception as exc:
                logger.warning("Адаптер %s упал: %s", adapter.name, exc)
            if len(adapters) > 1:
                await asyncio.sleep(config.POLITE_DELAY)
        candidates = _dedupe(candidates)

        if not candidates:
            return SearchOutcome(constraints=constraints)

        filtered = _prefilter(candidates, constraints)
        pool = filtered or candidates
        # топ-кандидаты по «массе отзывов × рейтинг» (PRD сценарий 2, шаг 2)
        top_candidates = sorted(pool, key=_popularity, reverse=True)[:5]

        analyses: dict[str, ReviewAnalysis] = {}
        if constraints.must_have:
            if progress:
                await progress(f"📖 Читаю отзывы {len(top_candidates)} кандидатов…")

            async def _analyze_one(p: Product) -> tuple[str, ReviewAnalysis | None]:
                reviews = await self._load_reviews(p)
                if not reviews:
                    return p.ext_id, None
                analysis = await analyze_reviews(
                    self._llm, p, reviews, constraints.must_have, db=self._db)
                return p.ext_id, analysis

            # параллельно: реальный LLM медленный, суммарное время ≈ максимуму
            results = await asyncio.gather(
                *[_analyze_one(p) for p in top_candidates])
            for ext_id, analysis in results:
                if analysis is not None:
                    analyses[ext_id] = analysis

        if progress:
            await progress("🏆 Выбираю лучшие варианты…")
        if analyses:
            ranked = rank_by_verdicts(top_candidates, list(analyses.values()))
        else:
            ranked = await self._llm_rank(top_candidates, constraints)

        return SearchOutcome(constraints=constraints,
                             top=ranked[:3], analyses=analyses,
                             all_candidates=candidates)

    # ── сценарий 1: поиск по фото ──────────────────────────────────
    async def photo_search(self, user_id: int, desc: VisionDescription,
                           state: SessionState,
                           progress: ProgressCb | None = None,
                           markets: list[str] | None = None) -> SearchOutcome:
        if progress:
            await progress("📸 Понял фото, ищу похожие товары…")
        query = desc.search_queries[0] if desc.search_queries else desc.category
        constraints = SearchConstraints(query=query, must_have=desc.details)
        outcome = await self._execute_search(constraints, progress, markets)

        if outcome.top:
            if progress:
                await progress("🔍 Сверяю с фотографией…")
            outcome.top = await self._llm_rank(outcome.top, constraints)

        state.mode = "photo"
        state.last_query = query
        state.constraints = constraints
        state.last_results = outcome.top
        state.history = (state.history + [f"[фото] {desc.category} {desc.color}"])[-6:]
        await self._db.save_session(user_id, state)
        return outcome

    # ── общие инструменты ──────────────────────────────────────────
    async def _search_market(self, adapter, query: str) -> list[Product]:
        cache_key = f"search:{adapter.name}:{query.lower()}"
        cached = await self._db.cache_get_products(cache_key)
        if cached is not None:
            return cached
        try:
            products = await adapter.search(query, limit=config.CANDIDATES_PER_MARKET)
        except Exception as exc:
            logger.warning("Поиск в %s упал: %s", adapter.name, exc)
            return []
        # Пустые результаты (антибот/блокировка) не кэшируем — иначе
        # неудачный поиск «замораживает» площадку на весь TTL.
        if products:
            await self._db.cache_set_products(cache_key, products,
                                              config.CACHE_SEARCH_TTL)
        return products

    async def _load_reviews(self, p: Product) -> list:
        cache_key = f"reviews:{p.marketplace}:{p.ext_id}"
        cached = await self._db.cache_get_reviews(cache_key)
        if cached is not None:
            return cached
        adapter = next((a for a in self._adapters if a.name == p.marketplace), None)
        if adapter is None:
            return []
        try:
            reviews = await adapter.get_reviews(p.ext_id,
                                                limit=config.REVIEWS_PER_PRODUCT)
        except Exception as exc:
            logger.warning("Отзывы %s/%s упали: %s", p.marketplace, p.ext_id, exc)
            return []
        await self._db.cache_set_reviews(cache_key, reviews,
                                         config.CACHE_REVIEWS_TTL)
        return reviews

    async def _llm_rank(self, products: list[Product],
                        constraints: SearchConstraints) -> list[Product]:
        if not products:
            return []
        lines = "\n".join(f"{i}: {p.title} | {p.price} ₽ | {p.brand}"
                          for i, p in enumerate(products))
        prompt = (RANK_PROMPT
                  + f"\n\nЗАПРОС: {constraints.query} "
                    f"(требования: {constraints.must_have})\nКАНДИДАТЫ:\n{lines}")
        result: RankResult = await self._llm.structured(kind="rank",
                                                        prompt=prompt,
                                                        schema=RankResult)
        order = sorted(result.items, key=lambda it: it.match_score, reverse=True)
        ranked = [products[it.index] for it in order
                  if 0 <= it.index < len(products)]
        rest = [p for p in products if p not in ranked]
        return (ranked + rest)[:3]

    # ── уточнения и свободный чат ──────────────────────────────────
    async def apply_refinement(self, user_id: int, user_text: str,
                               state: SessionState,
                               progress: ProgressCb | None = None) -> SearchOutcome:
        """«Подешевле», «только с белой подошвой» — уточнение прошлого поиска."""
        if not state.constraints or state.mode not in ("search", "photo"):
            # нет прошлого поиска — просто обычный поиск
            return await self.search_with_constraints(user_id, user_text, state,
                                                      progress)
        outcome = await self.search_with_constraints(user_id, user_text, state,
                                                     progress)
        return outcome

    async def freeform(self, question: str, facts: str = "") -> str:
        """Свободный вопрос — ответ только на основе переданных фактов."""
        from llm.prompts import FREEFORM_PROMPT
        from llm.schemas import FreeformReply

        facts_block = f"\n\nДОСТУПНЫЕ ФАКТЫ (собраны из маркетплейсов):\n{facts}" \
            if facts else ""
        reply = await self._llm.structured(
            kind="freeform",
            prompt=FREEFORM_PROMPT + f"\n\nВОПРОС: {question}" + facts_block,
            schema=FreeformReply)
        return reply.reply

    async def search_candidates(self, query: str,
                                markets: list[str] | None = None) -> list[Product]:
        """Собирает кандидатов по запросу с обеих площадок (для сравнения цен)."""
        adapters = [a for a in self._adapters
                    if markets is None or a.name in markets]
        results = await asyncio.gather(
            *[self._search_market(adapter, query) for adapter in adapters],
            return_exceptions=True,
        )
        candidates: list[Product] = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning("Поиск кандидатов упал: %s", res)
                continue
            candidates.extend(res)
        return _dedupe(candidates)
