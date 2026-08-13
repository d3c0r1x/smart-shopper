"""Тесты LLM Gateway: структуры mock, fallback-цепочка, бюджет, лимиты."""
from __future__ import annotations

import asyncio
import tempfile

import pytest

from llm.gateway import BudgetExceeded, LLMGateway
from llm.providers import ProviderError
from llm.schemas import ArbiterVerdict, RankResult
from models import SearchConstraints, VisionDescription


def _gateway(db, **kw) -> LLMGateway:
    # локальная модель по умолчанию выключена — старые тесты проверяют
    # облачную цепочку и mock ровно так же, как раньше
    kw.setdefault("local_llm", False)
    return LLMGateway(db, api_key="", **kw)


def _mk_db():
    from storage.db import Database

    return Database(tempfile.mktemp(suffix=".db"))


class _FailingProvider:
    """Провайдер, который всегда падает — для проверки fallback-цепочек."""

    async def complete(self, **kwargs):
        raise ProviderError("имитация отказа модели")


def test_mock_structured_constraints_and_vision():
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db)
            c = await gw.structured(kind="constraints", prompt="ПОЛЬЗОВАТЕЛЬ: "
                                      "чёрная маска до 1500 рублей",
                                    schema=SearchConstraints)
            assert isinstance(c, SearchConstraints)
            assert c.query
            assert "чёрный" in c.must_have
            assert c.max_price == 1500
            v = await gw.structured(kind="vision", prompt="x",
                                    schema=VisionDescription)
            assert isinstance(v, VisionDescription)
            assert v.search_queries
        finally:
            await db.close()
    asyncio.run(run())


def test_mock_rank_valid_schema():
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db)
            prompt = ("ЗАПРОС: кроссовки белые (требования: ['белый'])\n"
                      "0: Кроссовки для бега чёрные Marathon | 7990 ₽ | Marathon\n"
                      "1: Кроссовки белые унисекс Urban Runner | 4990 ₽ | Urban\n"
                      "2: Кеды белые Classic Canvas | 3990 ₽ | Classic")
            r: RankResult = await gw.structured(kind="rank", prompt=prompt,
                                                schema=RankResult)
            assert r.items and all(0 <= it.match_score <= 100 for it in r.items)
            # белые кандидаты должны быть выше чёрных
            assert r.items[0].index != 0
        finally:
            await db.close()
    asyncio.run(run())


def test_fallback_chain_to_mock_when_all_models_fail():
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=1000)
            gw._provider = _FailingProvider()  # подменяем на падающий
            result = await gw.structured(kind="constraints",
                                         prompt="ПОЛЬЗОВАТЕЛЬ: маска для сна",
                                         schema=SearchConstraints)
            # все модели упали → честный фолбэк на mock, схема соблюдена
            assert isinstance(result, SearchConstraints)
            assert result.query
        finally:
            await db.close()
    asyncio.run(run())


def test_budget_limit_raises():
    """Реальный провайдер тратит бюджет: после N вызовов — BudgetExceeded."""
    from tests._stub import OkProvider

    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=2)
            gw._provider = OkProvider()  # реальный путь, без сети
            for _ in range(2):
                await gw.structured(kind="constraints",
                                    prompt="ПОЛЬЗОВАТЕЛЬ: маска",
                                    schema=SearchConstraints)
            with pytest.raises(BudgetExceeded):
                await gw.structured(kind="constraints",
                                    prompt="ПОЛЬЗОВАТЕЛЬ: маска",
                                    schema=SearchConstraints)
            info = await gw.budget_info()
            assert info["used"] == 2 and info["remaining"] == 0
        finally:
            await db.close()
    asyncio.run(run())


def test_mock_calls_do_not_spend_budget():
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=1)
            for _ in range(5):  # mock-вызовы бесплатны
                await gw.structured(kind="constraints",
                                    prompt="ПОЛЬЗОВАТЕЛЬ: маска",
                                    schema=SearchConstraints)
            assert (await gw.budget_info())["used"] == 0
        finally:
            await db.close()
    asyncio.run(run())


class _NamedProvider:
    """Провайдер с именем — имитация Mistral/OpenRouter для роутинга."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs.get("model", ""))
        from models import SearchConstraints
        return SearchConstraints(query="тест", max_price=None)


def test_provider_routing_by_prefix():
    """'mistral:mistral-small-latest' → Mistral-провайдер; без ключа — пропуск."""
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db)
            mistral = _NamedProvider("mistral")
            gw._providers = [mistral]
            gw._provider = mistral

            provider, model = gw._resolve_provider("mistral:mistral-small-latest")
            assert provider is mistral and model == "mistral-small-latest"

            provider, model = gw._resolve_provider("openrouter:openai/gpt-oss-20b:free")
            assert provider is None  # ключа OpenRouter нет — модель пропускается

            # без префикса — основной провайдер (совместимость с тестами)
            provider, model = gw._resolve_provider("plain-model")
            assert provider is mistral and model == "plain-model"
        finally:
            await db.close()
    asyncio.run(run())


class _OkLocal:
    """Локальный провайдер, который всегда отвечает."""

    name = "local"

    async def complete(self, **kwargs):
        return ArbiterVerdict(same=True, confidence=0.9,
                              reason="локально")


class _FailLocal:
    """Локальный провайдер, который всегда падает."""

    name = "local"

    async def complete(self, **kwargs):
        raise ProviderError("Ollama недоступна")


def test_local_prefix_resolves_to_local_provider():
    """'local:qwen2.5:3b' резолвится в локальный провайдер."""
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, local_llm=True)
            assert gw._local is not None and gw._local.name == "local"
            provider, model = gw._resolve_provider("local:qwen2.5:3b")
            assert provider is gw._local and model == "qwen2.5:3b"
        finally:
            await db.close()
    asyncio.run(run())


def test_local_is_tried_first_and_free():
    """Локальная модель отвечает первой и не тратит бюджет."""
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=1)
            gw._local = _OkLocal()
            gw._provider = _NamedProvider("mistral")
            r = await gw.structured(kind="arbiter",
                                    prompt="ТОВАР A / ТОВАР B",
                                    schema=ArbiterVerdict)
            assert r.reason == "локально"
            assert (await gw.budget_info())["used"] == 0
        finally:
            await db.close()
    asyncio.run(run())


def test_local_failure_falls_back_to_cloud_with_budget():
    """Локальная модель упала → облачная цепочка, бюджет списан."""
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=1000)
            gw._local = _FailLocal()
            mistral = _NamedProvider("mistral")
            gw._providers = [mistral]
            gw._provider = mistral
            await gw.structured(kind="arbiter",
                                prompt="ТОВАР A / ТОВАР B",
                                schema=ArbiterVerdict)
            assert mistral.calls and mistral.calls[0] == "mistral-large-latest"
            assert (await gw.budget_info())["used"] == 1
        finally:
            await db.close()
    asyncio.run(run())


def test_local_works_when_daily_budget_exhausted():
    """Локальная модель отвечает даже при исчерпанном лимите."""
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=0)  # лимит исчерпан
            gw._local = _OkLocal()
            gw._provider = _NamedProvider("mistral")
            r = await gw.structured(kind="arbiter",
                                    prompt="ТОВАР A / ТОВАР B",
                                    schema=ArbiterVerdict)
            assert r.reason == "локально"
        finally:
            await db.close()
    asyncio.run(run())


def test_mistral_chain_calls_mistral_provider():
    """Цепочка quality начинается с mistral: — вызов уходит в MistralProvider."""
    async def run():
        db = _mk_db()
        await db.connect()
        try:
            gw = _gateway(db, daily_limit=1000)
            mistral = _NamedProvider("mistral")
            gw._providers = [mistral]
            gw._provider = mistral
            await gw.structured(kind="constraints",
                                prompt="ПОЛЬЗОВАТЕЛЬ: маска",
                                schema=SearchConstraints)
            # профиль по умолчанию — quality, первая модель mistral-large-latest
            assert mistral.calls and mistral.calls[0] == "mistral-large-latest"
        finally:
            await db.close()
    asyncio.run(run())
