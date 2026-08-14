"""Оркестратор диалога («мозг», PRD раздел 4).

Поток хода: вход → определение намерения → план (инструменты) → исполнение
адаптерами → возврат фактов модели → финальный ответ/карточки. Здесь же —
память сессии (сжатое состояние) и уточнения «подешевле / только чёрные»,
которые применяются к прошлому поиску, а не начинают его заново (PRD §5).

Гибридный поиск (ТЗ §2): выдача маркетплейсов (retrieval) → детерминированные
структурные фильтры (search/structfilter) → гибридный реранк «семантика +
лексика + структура» (search/rerank) → второй этап: анализ отзывов или
LLM-ранжирование. Если LLM недоступен/бюджет исчерпан — финальный порядок
остаётся детерминированным (гибридный скор), бот не деградирует до mock.

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
from llm.guardrails import sanitize_user_text
from llm.prompts import CONSTRAINTS_PROMPT, RANK_PROMPT
from llm.schemas import RankResult
from models import (Product, ReviewAnalysis, SearchConstraints, SessionState,
                    VisionDescription)
from review.intelligence import analyze_reviews, rank_by_verdicts
from search.embeddings import SemanticEmbedder
from search.rerank import HybridReranker
from search.structfilter import StructFilters, parse_structural

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
    """Формальный префильтр: цена, бренд, рейтинг из карточки.

    Учитываются как LLM-ограничения, так и детерминированные структурные
    (search/structfilter) — они уже влиты в constraints при извлечении.
    Товар без данных по атрибуту не отбрасывается (не можем проверить).
    """
    out = []
    for p in products:
        if c.max_price is not None and p.price and p.price > c.max_price:
            continue
        if c.min_price is not None and p.price and p.price < c.min_price:
            continue
        if c.min_rating is not None and p.rating is not None \
                and p.rating < c.min_rating:
            continue
        if c.brand and p.brand and c.brand.lower() not in p.brand.lower():
            continue
        out.append(p)
    return out


def _popularity(p: Product) -> float:
    return (p.rating or 0) * (1 + (p.reviews_count or 0)) ** 0.2


def _collapse_cross_market(products: list[Product]) -> list[Product]:
    """Нормализация выдачи (ТЗ §2): один товар с нескольких площадок → дешевле.

    Поиск по EAN отдаёт один и тот же товар с Ozon/Яндекса/WB — показывать
    его трижды бессмысленно. Группируем по штрихкоду и оставляем самую
    дешёвую карточку; товары без EAN не трогаем (не можем доказать, что
    это один и тот же SKU).
    """
    by_ean: dict[str, Product] = {}
    no_ean: list[Product] = []
    for p in products:
        if p.ean:
            prev = by_ean.get(p.ean)
            if prev is None or p.price < prev.price:
                by_ean[p.ean] = p
        else:
            no_ean.append(p)
    return list(by_ean.values()) + no_ean


def _struct_filters(c: SearchConstraints) -> StructFilters:
    """StructFilters из constraints (для реранкера и префильтра)."""
    return StructFilters(query=c.query, max_price=c.max_price,
                         min_price=c.min_price, min_rating=c.min_rating,
                         brand=c.brand)


class Orchestrator:
    """Выполняет сценарии и ведёт состояние сессии."""

    def __init__(self, db, llm: LLMGateway, adapters: list) -> None:
        self._db = db
        self._llm = llm
        self._adapters = adapters
        # мониторинг успешности парсинга (ТЗ §5, Coverage >= 90%)
        self._market_stats: dict[str, dict] = {}
        # Гибридный реранкер (ТЗ §2). Строится всегда при включённом
        # семантическом слое; клиент httpx — ленивый, сети в __init__ нет.
        self._reranker: HybridReranker | None = None
        if config.SEMANTIC_ENABLED:
            embedder = SemanticEmbedder(
                base_url=config.LOCAL_BASE_URL,
                model=config.EMBED_MODEL,
                timeout=config.EMBED_TIMEOUT,
                max_cache=config.EMBED_CACHE,
            )
            self._reranker = HybridReranker(embedder, config.RERANK_WEIGHTS)
        else:
            logger.info("Семантический слой выключен (SHOPPER_SEMANTIC_ENABLED=0)")

    def market_stats(self) -> dict[str, dict]:
        """Coverage по адаптерам: успех / пусто / ошибка / таймаут / всего."""
        out = {}
        for name, st in self._market_stats.items():
            total = st.get("total", 0)
            ok = st.get("ok", 0)
            coverage = round(100.0 * ok / total, 1) if total else None
            out[name] = {
                "total": total,
                "ok": ok,
                "empty": st.get("empty", 0),
                "error": st.get("error", 0),
                "timeout": st.get("timeout", 0),
                "coverage_pct": coverage,
            }
        return out

    def _record_market(self, name: str, outcome: str) -> None:
        st = self._market_stats.setdefault(
            name, {"total": 0, "ok": 0, "empty": 0, "error": 0, "timeout": 0})
        st["total"] += 1
        st[outcome] += 1

    async def aclose(self) -> None:
        if self._reranker is not None:
            await self._reranker.aclose()

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
        """Извлечение ограничений с учётом контекста прошлого поиска.

        Два слоя: LLM (свободные требования + запрос) и детерминированный
        структурный парсер (цена/бренд/рейтинг, search/structfilter.py).
        Структурный слой закрывает пропуски LLM и чистит запрос от
        ценовых фраз — работает без сети и без расхода бюджета.
        """
        user_text = sanitize_user_text(user_text, config.PROMPT_MAX_CHARS)
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
        constraints = await self._llm.structured(kind="constraints",
                                                 prompt=prompt,
                                                 schema=SearchConstraints)

        # детерминированное дополнение (ТЗ §2): LLM мог пропустить цену,
        # бренд или рейтинг — структурный парсер закрывает пробелы
        sf = parse_structural(user_text)
        if constraints.max_price is None:
            constraints.max_price = sf.max_price
        if constraints.min_price is None:
            constraints.min_price = sf.min_price
        if constraints.min_rating is None:
            constraints.min_rating = sf.min_rating
        if constraints.brand is None:
            constraints.brand = sf.brand
        # если структурный слой нашёл ценовые ограничения — его очищенный
        # запрос точнее (без «до 3000 рублей»), берём его вместо LLM-копии
        if (sf.max_price is not None or sf.min_price is not None) and sf.query:
            constraints.query = sf.query
        return constraints

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
        # один и тот же товар с нескольких площадок → одна карточка (дешевле)
        candidates = _collapse_cross_market(candidates)

        if not candidates:
            return SearchOutcome(constraints=constraints)

        filtered = _prefilter(candidates, constraints)
        pool = filtered or candidates
        # первый этап реранка: гибридный скор (семантика+лексика+структура)
        # вместо популярности — детерминированно и бесплатно (ТЗ §2)
        top_candidates = await self._hybrid_top(pool, constraints)

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
            products = await asyncio.wait_for(
                adapter.search(query, limit=config.CANDIDATES_PER_MARKET),
                timeout=config.MARKET_SEARCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self._record_market(adapter.name, "timeout")
            logger.warning(
                "Поиск %s/%s не завершён за %.1f с; площадка пропущена",
                adapter.name, query, config.MARKET_SEARCH_TIMEOUT_SECONDS,
            )
            return []
        except Exception as exc:
            self._record_market(adapter.name, "error")
            logger.warning("Поиск в %s упал: %s", adapter.name, exc)
            return []
        # Пустые результаты (антибот/блокировка) не кэшируем — иначе
        # неудачный поиск «замораживает» площадку на весь TTL.
        if products:
            self._record_market(adapter.name, "ok")
            await self._db.cache_set_products(cache_key, products,
                                              config.CACHE_SEARCH_TTL)
        else:
            self._record_market(adapter.name, "empty")
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
            reviews = await asyncio.wait_for(
                adapter.get_reviews(p.ext_id, limit=config.REVIEWS_PER_PRODUCT),
                timeout=config.REVIEWS_FETCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Отзывы %s/%s не получены за %.1f с; продолжаю без отзывов",
                p.marketplace, p.ext_id, config.REVIEWS_FETCH_TIMEOUT_SECONDS,
            )
            return []
        except Exception as exc:
            logger.warning("Отзывы %s/%s упали: %s", p.marketplace, p.ext_id, exc)
            return []
        await self._db.cache_set_reviews(cache_key, reviews,
                                         config.CACHE_REVIEWS_TTL)
        return reviews

    async def _load_card(self, p: Product) -> Product | None:
        """Обогащает товар данными карточки: характеристики, фото, рейтинг.

        Поисковая выдача отдаёт только название/цену/ссылку; полная карточка
        (характеристики, фото, рейтинг, число отзывов) — отдельный запрос
        get_card. Кэшируется на сутки.
        """
        cache_key = f"card:{p.marketplace}:{p.ext_id}"
        cached = await self._db.cache_get_products(cache_key)
        if cached:
            return cached[0]
        adapter = next((a for a in self._adapters if a.name == p.marketplace),
                       None)
        if adapter is None or not hasattr(adapter, "get_card"):
            return None
        try:
            card = await asyncio.wait_for(
                adapter.get_card(p.ext_id),
                timeout=config.CARD_ENRICH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Карточка %s/%s не получена за %.1f с; отправляю выдачу без обогащения",
                           p.marketplace, p.ext_id,
                           config.CARD_ENRICH_TIMEOUT_SECONDS)
            return None
        except Exception as exc:
            logger.warning("Карточка %s/%s не получена: %s",
                           p.marketplace, p.ext_id, exc)
            return None
        if card is None:
            return None
        await self._db.cache_set_products(cache_key, [card],
                                          config.CACHE_CARD_TTL)
        return card

    # ── гибридный реранк (ТЗ §2) ───────────────────────────────────
    async def _hybrid_top(self, pool: list[Product],
                          constraints: SearchConstraints) -> list[Product]:
        """Первый этап: гибридный скор → топ кандидатов для второго этапа.

        При недоступности эмбеддингов/реранкера — прежний порядок по
        популярности (поведение не деградирует).
        """
        if self._reranker is None:
            # без семантического слоя — прежнее поведение (популярность, топ-5)
            return sorted(pool, key=_popularity, reverse=True)[:5]
        limit = max(1, config.TOP_CANDIDATES)
        try:
            sf = _struct_filters(constraints)
            ranked = await self._reranker.rerank(constraints.query, sf, pool)
            return [p for p, _ in ranked][:limit]
        except Exception as exc:
            logger.warning("Гибридный реранкер упал (%s) — популярность", exc)
            return sorted(pool, key=_popularity, reverse=True)[:limit]

    async def _llm_rank(self, products: list[Product],
                        constraints: SearchConstraints) -> list[Product]:
        """Второй этап: LLM-ранжирование (или детерминированный фолбэк).

        Если LLM недоступен или дневной бюджет исчерпан — финальный порядок
        остаётся осмысленным: гибридный скор (семантика/лексика/структура)
        вместо мусорного mock-ответа.
        """
        if not products:
            return []
        lines = "\n".join(f"{i}: {p.title} | {p.price} ₽ | {p.brand}"
                          for i, p in enumerate(products))
        prompt = (RANK_PROMPT
                  + f"\n\nЗАПРОС: {constraints.query} "
                    f"(требования: {constraints.must_have})\nКАНДИДАТЫ:\n{lines}")
        try:
            result: RankResult = await self._llm.structured(kind="rank",
                                                            prompt=prompt,
                                                            schema=RankResult)
        except BudgetExceeded:
            logger.info("Бюджет LLM исчерпан — ранжирование детерминированное")
            return self._hybrid_order(products, constraints)
        except Exception as exc:
            logger.warning("LLM-ранжирование упало (%s) — детерминированное", exc)
            return self._hybrid_order(products, constraints)
        order = sorted(result.items, key=lambda it: it.match_score, reverse=True)
        ranked = [products[it.index] for it in order
                  if 0 <= it.index < len(products)]
        rest = [p for p in products if p not in ranked]
        return (ranked + rest)[:3]

    def _hybrid_order(self, products: list[Product],
                      constraints: SearchConstraints) -> list[Product]:
        """Синхронный детерминированный порядок (без эмбеддингов)."""
        if self._reranker is None:
            return sorted(products, key=_popularity, reverse=True)[:3]
        sf = _struct_filters(constraints)
        scored = self._reranker.score_sync(constraints.query, sf, products)
        return [p for p, _ in scored][:3]

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

        question = sanitize_user_text(question, config.PROMPT_MAX_CHARS)
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
