# -*- coding: utf-8 -*-
"""Тесты параллельного опроса площадок с неблокирующим Ozon (ТЗ §5).

Проверяем, что:
1. Быстрые площадки (Яндекс/WB) возвращаются сразу, Ozon уходит в фон;
2. Карточки Ozon доставляются отдельным колбэком, когда поиск завершён;
3. Если Ozon — единственная площадка, он опрашивается синхронно;
4. Ошибка одного адаптера не роняет остальные и не ломает фон.
"""
import asyncio
import time

from core.orchestrator import Orchestrator, _dedupe
from models import Product, SearchConstraints


class _SlowAdapter:
    """Имитация канала: спит delay секунд, возвращает карточки."""

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
        return SearchConstraints(query=kw.get("prompt", ""))

    async def aclose(self):
        pass


def _make_orch() -> Orchestrator:
    return Orchestrator(_StubDB(), _StubLLM(), [])


C = SearchConstraints(query="маска")


def test_fast_markets_return_immediately():
    """Ozon (медленный) не блокирует быстрые Яндекс/WB в основном ответе."""
    orch = _make_orch()
    adapters = [
        _SlowAdapter("ozon", 2.0),
        _SlowAdapter("yandex", 0.05),
        _SlowAdapter("wb", 0.05),
    ]
    t0 = time.perf_counter()
    out = asyncio.run(orch._search_parallel(adapters, C))
    dt = time.perf_counter() - t0

    names = {p.marketplace for p in out}
    assert names == {"yandex", "wb"}, f"основной ответ: {names}"
    # быстрые площадки вернулись сразу, не дожидаясь 2-секундного Ozon
    assert dt < 1.0, f"основной ответ занял {dt:.2f} с — Ozon блокировал"


def test_ozon_delivered_via_callback():
    """Карточки Ozon приходят в late_cb, когда фоновый поиск завершён."""
    orch = _make_orch()
    adapters = [
        _SlowAdapter("ozon", 0.2),
        _SlowAdapter("yandex", 0.05),
        _SlowAdapter("wb", 0.05),
    ]
    received: list[Product] = []

    async def cb(products):
        received.extend(products)

    async def run():
        out = await orch._search_parallel(adapters, C, late_cb=cb)
        # дождаться фоновой доставки Ozon
        for t in orch._pending:
            await asyncio.wait_for(t, timeout=5)
        return out

    out = asyncio.run(run())
    assert {p.marketplace for p in out} == {"yandex", "wb"}
    assert {p.marketplace for p in received} == {"ozon"}, \
        f"поздняя доставка: {[p.marketplace for p in received]}"


def test_ozon_only_is_synchronous():
    """Если выбран только Ozon — ждём синхронно (показать сразу нечего)."""
    orch = _make_orch()
    adapters = [_SlowAdapter("ozon", 0.05)]
    out = asyncio.run(orch._search_parallel(adapters, C))
    assert {p.marketplace for p in out} == {"ozon"}
    assert orch._pending == []


def test_failure_isolated():
    """Падение одной быстрой площадки не роняет остальные и не ломает фон."""
    orch = _make_orch()
    adapters = [
        _SlowAdapter("ozon", 0.1),
        _FailingAdapter("yandex", 0.2),
        _SlowAdapter("wb", 0.1),
    ]
    out = asyncio.run(orch._search_parallel(adapters, C))
    names = {p.marketplace for p in out}
    assert names == {"wb"}


def test_dedupe_still_works():
    p1 = Product(marketplace="ozon", ext_id="1", title="A", price=10,
                 url="https://ozon.ru/1")
    p2 = Product(marketplace="ozon", ext_id="1", title="A", price=10,
                 url="https://ozon.ru/1")
    assert len(_dedupe([p1, p2])) == 1
