"""Matcher: сопоставление одного товара на двух площадках (PRD, раздел 4, №8).

Каскад: штрихкод EAN (если есть в карточке) → нормализованное название
(нечёткое сравнение) → LLM-арбитр «один и тот же товар? да/нет».
Нечёткое сравнение — RapidFuzz, если установлен, иначе difflib (stdlib).
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from llm.gateway import LLMGateway
from llm.prompts import ARBITER_PROMPT
from llm.schemas import ArbiterVerdict
from models import CompareResult, Product

logger = logging.getLogger(__name__)

try:  # pragma: no cover
    from rapidfuzz import fuzz as _fuzz

    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


_STOP_WORDS = {"для", "с", "на", "и", "в", "из", "по", "от", "не", "за",
                "или", "а", "но", "до", "как", "что", "это", "также", "очень"}


def _normalize(title: str, strip_parens: bool = False) -> str:
    """Нижний регистр, убираем знаки и служебные слова для сравнения названий."""
    text = title.lower()
    if strip_parens:
        text = re.sub(r"\([^)]*\)", " ", text)  # скобки-уточнения
    text = re.sub(r"[^а-яёa-z0-9 ]", " ", text)
    words = [w for w in text.split()
             if len(w) > 1 and w not in _STOP_WORDS]
    return " ".join(words)


def _similar(a: str, b: str) -> float:
    """Максимум по двум нормализациям: целиком и без скобочных уточнений."""
    best = 0.0
    for strip in (False, True):
        a_norm, b_norm = _normalize(a, strip), _normalize(b, strip)
        if not a_norm or not b_norm:
            continue
        score = (_fuzz.ratio(a_norm, b_norm) / 100.0) if HAS_RAPIDFUZZ \
            else SequenceMatcher(None, a_norm, b_norm).ratio()
        best = max(best, score)
    return best


def find_counterpart(target: Product, candidates: list[Product]) -> Product | None:
    """Ищет тот же товар в выдаче другой площадки (EAN → нечёткое название)."""
    if target.ean:
        for c in candidates:
            if c.ean and c.ean == target.ean and c.marketplace != target.marketplace:
                return c
    best, best_score = None, 0.0
    for c in candidates:
        if c.marketplace == target.marketplace:
            continue
        score = _similar(target.title, c.title)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.55 else None


async def arbiter_confirm(llm: LLMGateway, a: Product, b: Product) -> ArbiterVerdict:
    """LLM-арбитр: финальное «да/нет» по паре кандидатов."""
    prompt = (
        ARBITER_PROMPT
        + f"\n\nТОВАР A ({a.marketplace}): {a.title} | EAN: {a.ean or '—'}\n"
        + f"ТОВАР B ({b.marketplace}): {b.title} | EAN: {b.ean or '—'}"
    )
    return await llm.structured(kind="arbiter", prompt=prompt, schema=ArbiterVerdict)


def build_compare_result(a: Product, b: Product) -> CompareResult:
    """Строка сравнения: кто дешевле и на сколько процентов.

    Пара площадок может быть любой (ozon↔yandex, ozon↔wb, yandex↔wb) —
    цены раскладываются по полям площадок, остальные остаются None.
    """
    cheaper: str | None = None
    diff: int | None = None
    if a.price and b.price and a.price != b.price:
        cheaper = a.marketplace if a.price < b.price else b.marketplace
        diff = round(abs(a.price - b.price) / max(a.price, b.price) * 100)
    prices: dict[str, int | None] = {"ozon": None, "yandex": None, "wb": None}
    urls: dict[str, str] = {"ozon": "", "yandex": "", "wb": ""}
    for p in (a, b):
        prices[p.marketplace] = p.price
        urls[p.marketplace] = p.url
    return CompareResult(
        title=a.title,
        ozon=prices["ozon"], yandex=prices["yandex"], wb=prices["wb"],
        ozon_url=urls["ozon"], yandex_url=urls["yandex"], wb_url=urls["wb"],
        cheaper=cheaper,
        diff_percent=diff,
    )


async def compare_across_markets(
    llm: LLMGateway,
    target: Product,
    candidates: list[Product],
) -> CompareResult | None:
    """Полный цикл сравнения: матчинг + арбитр + строка результата."""
    counterpart = find_counterpart(target, candidates)
    if counterpart is None:
        return None
    verdict = await arbiter_confirm(llm, target, counterpart)
    if not verdict.same:
        logger.info("Арбитр: %r и %r — разные товары", target.title, counterpart.title)
        return None
    return build_compare_result(target, counterpart)
