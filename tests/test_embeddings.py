"""Тесты семантического эмбеддинга (search/embeddings.py).

Клиент httpx подменяется фейком: сеть не используется, косинус и
поведение деградации проверяются детерминированно. Паттерн asyncio.run —
как в остальном наборе тестов (без pytest-asyncio).
"""
from __future__ import annotations

import asyncio

import pytest

from search.embeddings import SemanticEmbedder


class _StatusResp:
    """Ответ с кодом != 200 (тело не парсится)."""

    status_code: int
    text = "error"

    def __init__(self, status: int) -> None:
        self.status_code = status

    def json(self):
        return {}


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)


class FakeClient:
    """Возвращает фиксированные векторы; записывает запросы."""

    def __init__(self, vectors: list[list[float]], status: int = 200) -> None:
        self.vectors = vectors
        self.status = status
        self.calls: list[dict] = []

    async def post(self, url, json):
        self.calls.append({"url": url, "json": json})
        if self.status != 200:
            return _StatusResp(self.status)
        return FakeResponse({"embeddings": self.vectors})

    async def aclose(self):
        return None


async def _mk_embedder(vectors, status=200, model="bge-m3"):
    e = SemanticEmbedder("http://127.0.0.1:11434/v1", model, timeout=5)
    e._client = FakeClient(vectors, status)
    return e


def _run(coro):
    return asyncio.run(coro)


def test_cosine_identical():
    a = [1.0, 0.0]
    assert SemanticEmbedder.cosine(a, a) == pytest.approx(1.0)


def test_cosine_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert SemanticEmbedder.cosine(a, b) == pytest.approx(0.0)


def test_cosine_dim_mismatch():
    assert SemanticEmbedder.cosine([1.0], [1.0, 2.0]) == 0.0


def test_available_defaults_true():
    e = SemanticEmbedder("http://x/v1", "m")
    assert e.available


def test_embed_many_and_cache():
    async def run():
        e = await _mk_embedder([[1.0, 0.0], [0.0, 1.0]])
        out1 = await e.embed_many(["маска для сна", "кроссовки"])
        assert out1 == [[1.0, 0.0], [0.0, 1.0]]
        # повторный вызов — без нового POST (кэш)
        out2 = await e.embed_many(["маска для сна", "кроссовки"])
        assert out2 == out1
        assert len(e._client.calls) == 1
        assert e._client.calls[0]["json"]["model"] == "bge-m3"
        await e.aclose()
    _run(run())


def test_similarity_ordering():
    async def run():
        e = await _mk_embedder([[1.0, 0.0], [0.99, 0.01], [0.1, 0.9]])
        sims = await e.similarity("маска для сна",
                                  ["маска для сна 3d", "кроссовки"])
        assert sims is not None and len(sims) == 2
        assert sims[0] > sims[1]
    _run(run())


def test_http_error_disables_and_recovers_later():
    async def run():
        e = await _mk_embedder([], status=503)
        assert await e.embed_many(["тест"]) is None
        assert not e.available  # карантин
        # в карантине дальнейшие вызовы мгновенно возвращают None
        assert await e.embed_many(["другой"]) is None
        # по истечении карантина пробуем снова
        e._disabled_until = 0
        e._client.status = 200
        e._client.vectors = [[1.0]]
        assert await e.embed_many(["тест"]) == [[1.0]]
        await e.aclose()
    _run(run())


def test_transport_error_disables():
    class BoomClient:
        async def post(self, url, json):
            raise OSError("connection refused")

        async def aclose(self):
            return None

    async def run():
        e = await _mk_embedder([[1.0]])
        e._client = BoomClient()
        assert await e.embed_many(["тест"]) is None
        assert e.last_error
        await e.aclose()
    _run(run())
