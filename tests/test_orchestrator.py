"""Тесты оркестратора: сценарии 1 и 2, уточнения, лимиты."""
from __future__ import annotations

import asyncio
import tempfile

import pytest

from adapters import build_adapters
from core.orchestrator import Orchestrator
from llm.gateway import BudgetExceeded, LLMGateway
from models import SessionState
from storage.db import Database


def _env(db, **kw):
    return Orchestrator(db, LLMGateway(db, api_key="", **kw),
                        build_adapters(demo=True))


def test_scenario2_full_flow():
    """«чёрная маска с пространством для ресниц» → топ из масок с вердиктами."""
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            orch = _env(db)
            steps: list[str] = []

            async def progress(step: str):
                steps.append(step)

            outcome = await orch.search_with_constraints(
                1, "чёрная маска для сна с пространством для ресниц",
                SessionState(), progress)
            assert outcome.top, "нет результатов"
            # топ-1 — 3D-маска с пространством для ресниц
            assert outcome.top[0].ext_id == "msk-001"
            assert "чёрный" in outcome.constraints.must_have
            analysis = outcome.analyses[outcome.top[0].ext_id]
            by_req = {v.requirement: v for v in analysis.verdicts}
            assert by_req["чёрный"].verdict == "confirmed"
            assert by_req["ресниц"].verdict == "confirmed"
            # статусы прогресса показывались
            assert any("Ищу" in s for s in steps)
            assert any("отзыв" in s.lower() for s in steps)
        finally:
            await db.close()
    asyncio.run(run())


def test_refinement_cheaper_applies_to_previous_query():
    """«до 1200 рублей» → «а подешевле» = максимум 960 (×0.8)."""
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            orch = _env(db)
            state = SessionState()
            first = await orch.search_with_constraints(
                1, "маска для сна до 1200 рублей", state, None)
            assert first.constraints.max_price == 1200
            second = await orch.search_with_constraints(
                1, "а подешевле", state, None)
            assert second.constraints.query == first.constraints.query  # тот же поиск
            assert second.constraints.max_price == 960
        finally:
            await db.close()
    asyncio.run(run())


def test_photo_scenario1_ranks_white_sneakers_first():
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            orch = _env(db)
            from vision.service import describe_photo
            desc = await describe_photo(orch._llm, None)  # демо: белые кроссовки
            outcome = await orch.photo_search(1, desc, SessionState(), None)
            assert outcome.top
            # белые товары вверху, чёрные — внизу или отсутствуют
            top_titles = [p.title.lower() for p in outcome.top]
            assert any("бел" in t for t in top_titles)
            assert not outcome.top[0].title.lower().startswith("кроссовки для бега чёрные")
        finally:
            await db.close()
    asyncio.run(run())


def test_max_price_prefilter():
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            orch = _env(db)
            outcome = await orch.search_with_constraints(
                1, "маска для сна до 950 рублей", SessionState(), None)
            assert outcome.constraints.max_price == 950
            assert all(p.price <= 950 for p in outcome.top)
        finally:
            await db.close()
    asyncio.run(run())


def test_budget_exceeded_propagates():
    from tests._stub import OkProvider

    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            orch = _env(db, daily_limit=0)
            orch._llm._provider = OkProvider()  # реальный путь, лимит 0
            with pytest.raises(BudgetExceeded):
                await orch.search_with_constraints(
                    1, "маска для сна", SessionState(), None)
        finally:
            await db.close()
    asyncio.run(run())


def test_session_memory_saved():
    async def run():
        db = Database(tempfile.mktemp(suffix=".db"))
        await db.connect()
        try:
            orch = _env(db)
            state = SessionState()
            await orch.search_with_constraints(
                1, "маска для сна", state, None)
            saved = await db.get_session(1)
            assert saved.mode == "search"
            assert saved.last_query
            assert saved.history
        finally:
            await db.close()
    asyncio.run(run())
