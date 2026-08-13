"""Review Intelligence (PRD, раздел 6, сценарий 2, шаг 3).

Для каждого топ-кандидата загружается пул свежих отзывов, и длинноконтекстная
модель проверяет каждое требование пользователя: confirmed / rejected /
no_data + 1-2 дословные цитаты. Результат кэшируется (TTL 24 ч — проектное
значение, PRD раздел 8), повторное открытие отзывов — мгновенное.

Отзывы приходят из внешнего источника (маркетплейс) и перед вставкой в
промпт проходят санитизацию (llm/guardrails.py, ТЗ §4): control-символы
вырезаются, длина ограничена — защита и от prompt-инъекций, и от
перерасхода контекста.
"""
from __future__ import annotations

import hashlib
import logging

import config
from llm.gateway import LLMGateway
from llm.guardrails import sanitize_item
from llm.prompts import REVIEW_PROMPT
from models import Product, RequirementVerdict, Review, ReviewAnalysis

logger = logging.getLogger(__name__)


def build_review_prompt(product: Product, reviews: list[Review],
                        requirements: list[str]) -> str:
    """Собирает промпт: товар + требования + отзывы (каждый с маркером ОТЗЫВ).

    Требования пользователя и тексты отзывов санитизируются перед вставкой.
    """
    req_block = "\n".join(f"- {sanitize_item(r, config.PROMPT_MAX_CHARS)}"
                          for r in requirements) or "(нет требований)"
    reviews_block = "\n".join(
        f"ОТЗЫВ {i}: [{r.rating}/5] "
        f"{sanitize_item(r.text, config.REVIEW_TEXT_MAX_CHARS)}"
        for i, r in enumerate(reviews, 1)
    )
    return (
        REVIEW_PROMPT
        + f"\n\nТОВАР: {sanitize_item(product.title, 300)}\n"
        + f"ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ:\n{req_block}\n\n"
        + f"ОТЗЫВЫ:\n{reviews_block}"
    )


async def analyze_reviews(
    llm: LLMGateway,
    product: Product,
    reviews: list[Review],
    requirements: list[str],
    db=None,
) -> ReviewAnalysis:
    """Анализ отзывов с кэшем (ключ — товар + набор требований)."""
    cache_key = None
    if db is not None:
        digest = hashlib.sha256(
            "\n".join(requirements).encode("utf-8")).hexdigest()[:12]
        cache_key = f"analysis:{product.marketplace}:{product.ext_id}:{digest}"
        cached = await db.cache_get(cache_key)
        if cached is not None:
            return ReviewAnalysis.model_validate_json(cached)

    if not reviews:
        analysis = ReviewAnalysis(
            product_market=product.marketplace, product_id=product.ext_id,
            verdicts=[RequirementVerdict(requirement=r, verdict="no_data")
                      for r in requirements],
            summary="Отзывов пока нет.",
        )
    else:
        prompt = build_review_prompt(product, reviews, requirements)
        analysis = await llm.structured(kind="review", prompt=prompt,
                                        schema=ReviewAnalysis)
        analysis.product_market = product.marketplace
        analysis.product_id = product.ext_id

    if db is not None and cache_key is not None:
        await db.cache_set(cache_key, analysis.model_dump_json(),
                           config.CACHE_REVIEWS_TTL)
    return analysis


def rank_by_verdicts(products: list[Product],
                     analyses: list[ReviewAnalysis]) -> list[Product]:
    """Ранжирование по вердиктам (PRD, сценарий 2, шаг 4).

    Товары, у которых все обязательные требования confirmed, поднимаются
    наверх; rejected по обязательному требованию — в конец (не исключаются
    полностью, а помечаются — решение UX, видно пользователю).
    """
    def score(product: Product) -> int:
        analysis = next((a for a in analyses
                         if a.product_id == product.ext_id), None)
        if analysis is None:
            return 0
        s = 0
        for v in analysis.verdicts:
            if v.verdict == "confirmed":
                s += 3
            elif v.verdict == "rejected":
                s -= 4
        return s

    return sorted(products, key=score, reverse=True)
