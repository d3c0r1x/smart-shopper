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
from llm.providers import (MistralProvider, MockProvider,
                           OpenRouterProvider, ProviderError)

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
# Модели с префиксом провайдера: "mistral:..." идут через MistralProvider,
# "openrouter:..." — через OpenRouterProvider. Если у провайдера нет ключа,
# его модели пропускаются цепочкой. Текст — Mistral впереди (пользовательский
# ключ), OpenRouter :free как запасной. Vision — только OpenRouter (на этом
# аккаунте Mistral не отдаёт vision-модели: pixtral отсутствует в списке
# /v1/models, проверено живым запросом 13.08.2026).
TEXT_CHAINS = {
    "fast": [
        "mistral:mistral-small-latest",
        "openrouter:openai/gpt-oss-20b:free",
        "openrouter:openrouter/free",
    ],
    "quality": [
        "mistral:mistral-large-latest",
        "mistral:mistral-medium-latest",
        "openrouter:openai/gpt-oss-20b:free",
        "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",  # контекст 1M
        "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter:openrouter/free",
    ],
}
VISION_CHAINS = {
    "fast": [
        "openrouter:google/gemma-4-31b-it:free",
        "openrouter:nvidia/nemotron-nano-12b-v2-vl:free",
        "openrouter:openrouter/free",
    ],
    "quality": [
        "openrouter:google/gemma-4-31b-it:free",  # текст+изображения [[9]]
        "openrouter:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "openrouter:nvidia/nemotron-nano-12b-v2-vl:free",
        "openrouter:openrouter/free",
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
        mistral_api_key: str = config.MISTRAL_API_KEY,
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
        # по одному провайдеру на каждый заданный ключ; роутинг цепочек —
        # по префиксу модели (mistral:/openrouter:)
        self._providers: list = []
        if mistral_api_key:
            self._providers.append(MistralProvider(mistral_api_key, timeout))
        if api_key:
            self._providers.append(OpenRouterProvider(api_key, timeout))
        # первый доступный — «основной» (для диагностики и тестов)
        self._provider = self._providers[0] if self._providers else None
        self._mock = MockProvider()
        self._call_times: list[float] = []

    @property
    def real(self) -> bool:
        return self._provider is not None

    @property
    def provider_name(self) -> str:
        """Имя основного провайдера для диагностики (Mistral/OpenRouter)."""
        if not self._provider:
            return "mock"
        return {
            "mistral": "Mistral",
            "openrouter": "OpenRouter",
        }.get(getattr(self._provider, "name", ""), "LLM")

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
        # бюджет списывается один раз на структурный вызов (не на каждую
        # модель цепочки): иначе 4 неудачные попытки из-за 429 апстрима
        # сжигали бы лимит в 4 раза быстрее, чем реальные успешные ответы
        await self._db.budget_increment(day)
        for raw in chain:
            if time.monotonic() > deadline:
                logger.warning("Время вызова %s превысило потолок %.0f с — "
                               "фолбэк на mock", kind, config.STRUCTURED_MAX_SECONDS)
                break
            provider, model = self._resolve_provider(raw)
            if provider is None:
                logger.info("Нет ключа для %s — пропускаю модель", raw)
                continue
            await self._throttle()
            try:
                # потолок соблюдается и внутри вызова: остаток бюджета
                remaining = deadline - time.monotonic()
                result = await asyncio.wait_for(
                    provider.complete(
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

    def _resolve_provider(self, raw: str) -> tuple:
        """Модель 'mistral:mistral-small-latest' → (MistralProvider, модель).

        Без префикса — основной провайдер (совместимость с тестами, где
        провайдер подменяется напрямую). Модель провайдера без ключа —
        (None, None): цепочка пропускает её.
        """
        if ":" in raw:
            prefix, _, model = raw.partition(":")
            for p in self._providers:
                if getattr(p, "name", "") == prefix:
                    return p, model
            return None, None
        return self._provider, raw

    async def budget_info(self) -> dict:
        day = date.today().isoformat()
        used = await self._db.budget_used(day)
        return {
            "day": day,
            "used": used,
            "limit": self._daily_limit,
            "remaining": max(0, self._daily_limit - used),
            "real_provider": self.real,
            "provider": self.provider_name,
            "profile": self._profile,
            "chains": {
                "text": TEXT_CHAINS[self._profile],
                "vision": VISION_CHAINS[self._profile],
            },
        }

    async def aclose(self) -> None:
        for p in self._providers:
            await p.aclose()

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
