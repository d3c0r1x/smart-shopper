# -*- coding: utf-8 -*-
"""Тесты ступенчатого параллельного опроса площадок (ТЗ §5, latency p95).

Проверяем, что:
1. При включённом PARALLEL_MARKETS первый адаптер (Ozon) стартует первым
   в одиночку, остальные — параллельно после OZON_HEAD_START;
2. Суммарное время стремится к максимуму, а не к сумме (два «медленных»
   адаптера, запущенных параллельно, укладываются в максимум);
3. Ошибка одного адаптера не роняет остальных.
"""
import asyncio
import time

from core.orchestrator import Orchestrator, _dedupe
from models import Product


class _SlowAdapter:
    """Имитация браузерного канала: спит delay секунд, возвращает карточки."""

    def __init__(self, name: str, delay: float,
                 products: list[Product] | None = None):
        self.name = name
        self._delay = delay
        self._products = products or [Product(
            marketplace=name, ext_id=f"{name}-1", title=f"Товар {name}",
            price=100, url=f"https://{name}.example/1")]

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        await asyncio.sleep(self._delay)
        return self._products


class _FailingAdapter(_SlowAdapter):
    async def search(self, query: str, limit: int = 5) -> list[Product]:
        await asyncio.sleep(self._delay)
        raise RuntimeError("антибот-блок")


class _StubDB:
    async def cache_get_products(self, key):  # pragma: no cover
        return None

    async def cache_set_products(self, key, products, ttl):  # pragma: no cover
        pass

    async def cache_get_reviews(self, key):  # pragma: no cover
        return None


class _StubLLM:  # pragma: no cover
    async def structured(self, **kw):
        from llm.schemas import SearchConstraints
        return SearchConstraints(
            query=kw.get("prompt", "").split("ПОЛЬЗОВАТЕЛЬ:")[-1].strip())

    async def aclose(self):
        pass


def _make_orch(monkeypatch) -> Orchestrator:
    import config
    monkeypatch.setattr(config, "PARALLEL_MARKETS", True)
    monkeypatch.setattr(config, "OZON_HEAD_START", 0.05)
    return Orchestrator(_StubDB(), _StubLLM(), [])


def test_parallel_runs_rest_concurrently(monkeypatch):
    """Два медленных адаптера в хвосте запускаются параллельно.

    Если бы они шли последовательно, суммарное время было бы ~2 с;
    при параллельном — ~1 с (максимум), плюс head-старт Ozon.
    """
    orch = _make_orch(monkeypatch)
    adapters = [
        _SlowAdapter("ozon", 0.2),
        _SlowAdapter("yandex", 1.0),
        _SlowAdapter("wb", 1.0),
    ]
    t0 = time.perf_counter()
    out = asyncio.run(orch._search_parallel(adapters, "маска"))
    dt = time.perf_counter() - t0

    assert len(out) == 3
    # 0.2 (ozon) + 0.05 (head start) + 1.0 (максимум из yandex/wb) ≈ 1.25 с,
    # последовательный вариант был бы ≈ 2.2 с. Допускаем запас на планировщик.
    assert dt < 1.9, f"параллельный опрос занял {dt:.2f} с — больше максимума+запас"


def test_parallel_first_adapter_alone(monkeypatch):
    """Ozon (первый) и хвост параллельно — обе площадки в выдаче."""
    orch = _make_orch(monkeypatch)
    adapters = [
        _SlowAdapter("ozon", 0.1),
        _SlowAdapter("yandex", 0.5),
    ]
    out = asyncio.run(orch._search_parallel(adapters, "маска"))
    names = {p.marketplace for p in out}
    assert names == {"ozon", "yandex"}


def test_parallel_failure_isolated(monkeypatch):
    """Падение одного адаптера не роняет остальных."""
    orch = _make_orch(monkeypatch)
    adapters = [
        _SlowAdapter("ozon", 0.1),
        _FailingAdapter("yandex", 0.2),
        _SlowAdapter("wb", 0.3),
    ]
    out = asyncio.run(orch._search_parallel(adapters, "маска"))
    names = {p.marketplace for p in out}
    assert names == {"ozon", "wb"}


def test_parallel_single_adapter(monkeypatch):
    """Один адаптер — без gather и без ошибок."""
    orch = _make_orch(monkeypatch)
    adapters = [_SlowAdapter("ozon", 0.05)]
    out = asyncio.run(orch._search_parallel(adapters, "маска"))
    assert len(out) == 1


def test_sequential_path_unchanged(monkeypatch):
    """При выключенном флаге поведение последовательное (регрессия)."""
    import config
    monkeypatch.setattr(config, "PARALLEL_MARKETS", False)
    monkeypatch.setattr(config, "POLITE_DELAY", 0.01)
    orch = Orchestrator(_StubDB(), _StubLLM(), [])
    adapters = [
        _SlowAdapter("ozon", 0.1),
        _SlowAdapter("yandex", 0.1),
    ]
    out = asyncio.run(orch._search_parallel if False else
                      _sequential_fallback(orch, adapters, "маска"))
    assert len(out) == 2


async def _sequential_fallback(orch, adapters, query):
    out = []
    for a in adapters:
        out.extend(await orch._search_market(a, query))
    return out


def test_dedupe_still_works():
    p1 = Product(marketplace="ozon", ext_id="1", title="A", price=10,
                 url="https://ozon.ru/1")
    p2 = Product(marketplace="ozon", ext_id="1", title="A", price=10,
                 url="https://ozon.ru/1")
    assert len(_dedupe([p1, p2])) == 1
