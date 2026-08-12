"""LLM Gateway: реестр моделей, fallback-цепочки, троттлинг, бюджет.

PRD, раздел 3: бесплатные модели OpenRouter регулярно снимаются с публикации,
поэтому хардкодить одну модель запрещено. Реестр — data-driven (конфиг/код),
для каждого вида задач своя цепочка :free-моделей с суффиксом `openrouter/free`
(роутер, сам выбирает доступную бесплатную модель) в конце как страховку.

Лимиты из документации OpenRouter: 20 запросов/мин на :free ([[8]]), дневной
лимит 50 — без вложений, 1000 — после разовой покупки от $10 ([[8]]).
Соблюдение: очередь по времени вызовов + счётчик дня в БД.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Type

from pydantic import BaseModel

import config
from llm.providers import MockProvider, OpenRouterProvider, ProviderError

logger = logging.getLogger(__name__)

# Реестр :free-моделей. ID — по документации OpenRouter на 03.08.2026
# (PRD раздел 3, [[8]][[9]][[13]]); ротация моделей закрывается цепочками
# и конечным роутером openrouter/free [[3]]. 12.08.2026: порядок калиброван
# живой пробой ключа (см. README, «Ротация моделей»); inclusionai/
# ling-3.0-flash:free снят с бесплатного tier и удалён из цепочек.
# Порядок цепочек калиброван живой пробой ключа от 12.08.2026: рабочие
# модели впереди (экономия бюджета), недоступные для этого аккаунта — в
# хвосте (цепочка всё равно до них дойдёт при ротации). Роутер
# openrouter/free всегда последний — он сам выбирает доступную :free-модель.
TEXT_CHAINS = {
    "fast": [
        "openai/gpt-oss-20b:free",
        "openrouter/free",
    ],
    "quality": [
        "openai/gpt-oss-20b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",  # контекст 1M — анализ отзывов
        "nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/free",
    ],
}
VISION_CHAINS = {
    "fast": [
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "openrouter/free",
    ],
    "quality": [
        "google/gemma-4-31b-it:free",  # текст+изображения, function calling [[9]]
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "openrouter/free",
    ],
}

TEXT_KINDS = {"constraints", "review", "rank", "arbiter", "freeform"}


class BudgetExceeded(Exception):
    """Дневной лимит бесплатных LLM-запросов исчерпан."""


class LLMGateway:
    """Единая точка вызова LLM: бюджет → троттлинг → цепочка моделей.

    Если ключ OpenRouter не задан — работает только mock (демо-режим,
    бюджет не тратится). Если задан, но все модели в цепочке упали —
    честный fallback на mock с предупреждением в лог (бот не падает).
    """

    def __init__(
        self,
        db,
        api_key: str = config.OPENROUTER_API_KEY,
        profile: str = config.LLM_PROFILE,
        daily_limit: int = config.DAILY_LLM_LIMIT,
        rate_per_minute: int = config.RATE_PER_MINUTE,
        timeout: float = config.LLM_TIMEOUT_SECONDS,
    ) -> None:
        self._db = db
        self._profile = profile if profile in TEXT_CHAINS else "quality"
        self._daily_limit = daily_limit
        self._rate = rate_per_minute
        self._timeout = timeout
        self._provider = (
            OpenRouterProvider(api_key, config.OPENROUTER_BASE_URL, timeout)
            if api_key else None
        )
        self._mock = MockProvider()
        self._call_times: list[float] = []

    @property
    def real(self) -> bool:
        return self._provider is not None

    def set_profile(self, profile: str) -> None:
        """Смена профиля моделей (fast/quality) в рантайме (кнопка /settings)."""
        if profile in TEXT_CHAINS:
            self._profile = profile

    # ── публичный API ─────────────────────────────────────────────

    async def structured(self, *, kind: str, prompt: str,
                         schema: Type[BaseModel],
                         images: list[str] | None = None) -> BaseModel:
        """Один структурный вызов: вернёт валидный schema-объект."""
        if kind not in TEXT_KINDS | {"vision"}:
            raise ValueError(f"неизвестный kind: {kind}")
        if self._provider is None:
            return await self._mock.complete(model="mock", kind=kind,
                                             prompt=prompt, schema=schema,
                                             images=images)

        day = date.today().isoformat()
        if await self._db.budget_used(day) >= self._daily_limit:
            raise BudgetExceeded(self._daily_limit)

        chain = TEXT_CHAINS[self._profile] if kind in TEXT_KINDS \
            else VISION_CHAINS[self._profile]
        last_error: Exception | None = None
        deadline = time.monotonic() + config.STRUCTURED_MAX_SECONDS
        for model in chain:
            if time.monotonic() > deadline:
                logger.warning("Время вызова %s превысило потолок %.0f с — "
                               "фолбэк на mock", kind, config.STRUCTURED_MAX_SECONDS)
                break
            await self._throttle()
            await self._db.budget_increment(day)
            try:
                # потолок соблюдается и внутри вызова: остаток бюджета
                remaining = deadline - time.monotonic()
                result = await asyncio.wait_for(
                    self._provider.complete(
                        model=model, kind=kind, prompt=prompt, schema=schema,
                        images=images),
                    timeout=max(0.0, remaining))
                return result
            except (ProviderError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning("Модель %s упала (%s), пробую следующую в цепочке",
                               model, exc)
        logger.error("Все модели цепочки %s упали: %s — фолбэк на mock",
                     chain, last_error)
        return await self._mock.complete(model="mock", kind=kind,
                                         prompt=prompt, schema=schema,
                                         images=images)

    async def budget_info(self) -> dict:
        day = date.today().isoformat()
        used = await self._db.budget_used(day)
        return {
            "day": day,
            "used": used,
            "limit": self._daily_limit,
            "remaining": max(0, self._daily_limit - used),
            "real_provider": self.real,
            "profile": self._profile,
            "chains": {
                "text": TEXT_CHAINS[self._profile],
                "vision": VISION_CHAINS[self._profile],
            },
        }

    async def aclose(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()

    # ── троттлинг: не больше rate вызовов в минуту ────────────────
    async def _throttle(self) -> None:
        now = time.monotonic()
        self._call_times = [t for t in self._call_times if now - t < 60.0]
        if len(self._call_times) >= self._rate:
            wait = 60.0 - (now - self._call_times[0])
            if wait > 0:
                logger.info("Троттлинг: сплю %.1f с (лимит %d/мин)", wait, self._rate)
                await asyncio.sleep(wait)
        self._call_times.append(time.monotonic())
