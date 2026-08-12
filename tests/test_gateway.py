"""Тесты LLM Gateway: структуры mock, fallback-цепочка, бюджет, лимиты."""
from __future__ import annotations

import asyncio
import tempfile

import pytest

from llm.gateway import BudgetExceeded, LLMGateway
from llm.providers import ProviderError
from llm.schemas import RankResult
from models import SearchConstraints, VisionDescription


def _gateway(db, **kw) -> LLMGateway:
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
