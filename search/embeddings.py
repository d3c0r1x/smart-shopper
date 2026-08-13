"""search/embeddings.py — биэнкодер эмбеддингов через Ollama (ТЗ §2).

Модель по умолчанию — bge-m3: мультиязычная (включая русский), открытая
веса (карточка BAAI/bge-m3 на HuggingFace). Запрос и тексты кандидатов
переводятся в векторы, релевантность — косинус между ними.

Деградация (важно для стабильности, ТЗ §4): Ollama недоступна, модель не
установлена или отвечает ошибкой → эмбеддинги честно отключаются на 5 минут,
ранжирование продолжает работать на лексике (без семантической составляющей).
Кэш в памяти на max_cache векторов с TTL, чтобы повторные запросы и
похожие названия не били в сеть.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

logger = logging.getLogger(__name__)

_DISABLE_WINDOW = 60.0  # секунд «карантина» после ошибки (холодный старт модели)
_CACHE_TTL = 3600.0      # секунд жизни вектора в кэше


class SemanticEmbedder:
    """Клиент /api/embed Ollama с кэшем, деградацией и косинусом."""

    def __init__(self, base_url: str, model: str, timeout: float = 20.0,
                 max_cache: int = 2048) -> None:
        self._base = base_url.rstrip("/").replace("/v1", "") + "/api/embed"
        self._model = model
        self._timeout = timeout
        self._max_cache = max_cache
        # httpx-клиент создаётся лениво: тесты, которые не вызывают embed,
        # не открывают соединений и не требуют aclose
        self._client = None
        self._cache: dict[str, tuple[float, list[float]]] = {}
        self._disabled_until = 0.0
        self._last_error = ""

    async def _client_ensure(self):
        if self._client is None:
            import httpx

            # trust_env=False: httpx 0.28 подхватывает системный прокси,
            # который глушит localhost-запросы (та же причина, что в LocalProvider)
            self._client = httpx.AsyncClient(timeout=self._timeout,
                                             trust_env=False)
        return self._client

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._disabled_until

    @property
    def last_error(self) -> str:
        return self._last_error

    async def embed_many(self, texts: list[str]) -> list[list[float]] | None:
        """Векторы для списка текстов (в том же порядке) или None при сбое."""
        if not texts:
            return []
        now = time.monotonic()
        if now < self._disabled_until:
            return None
        # кэш-промахи — только те, кого нет или чей TTL истёк
        missing = [t for t in texts
                   if t not in self._cache or now - self._cache[t][0] > _CACHE_TTL]
        for t in texts:
            if t in self._cache and now - self._cache[t][0] > _CACHE_TTL:
                del self._cache[t]
        if missing:
            try:
                client = await self._client_ensure()
                resp = await client.post(
                    self._base,
                    json={"model": self._model, "input": missing,
                          # модель остаётся в VRAM: иначе каждый холодный
                          # первый запрос платит загрузкой (~10-15 с)
                          "keep_alive": -1})
            except Exception as exc:  # транспортная ошибка
                return self._disable(f"транспорт: {exc}")
            if resp.status_code != 200:
                return self._disable(
                    f"HTTP {resp.status_code}: {resp.text[:120]}")
            try:
                data = resp.json()
                embeddings = data.get("embeddings")
            except Exception as exc:
                return self._disable(f"невалидный ответ: {exc}")
            if not embeddings:
                return self._disable("пустой ответ /api/embed")
            for t, vec in zip(missing, embeddings):
                self._cache[t] = (now, vec)
        return [self._cache[t][1] for t in texts]

    async def embed_one(self, text: str) -> list[float] | None:
        vectors = await self.embed_many([text])
        return vectors[0] if vectors else None

    async def similarity(self, query: str,
                         texts: list[str]) -> list[float] | None:
        """Косинус запроса с каждым текстом (0..1) или None при сбое."""
        if not texts:
            return []
        vectors = await self.embed_many([query] + list(texts))
        if vectors is None:
            return None
        q = vectors[0]
        return [self.cosine(q, v) for v in vectors[1:]]

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def _disable(self, why: str) -> None:
        self._last_error = why
        self._disabled_until = time.monotonic() + _DISABLE_WINDOW
        logger.warning("Эмбеддинги отключены на %.0f с (%s) — "
                       "ранжирование на лексике", _DISABLE_WINDOW, why)
        return None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def warmup(embedder: SemanticEmbedder) -> bool:
    """Проверяет доступность эмбеддингов одним запросом (для /diag)."""
    try:
        result = await asyncio.wait_for(
            embedder.embed_one("тест"),
            timeout=min(embedder._timeout, 10.0))
        return result is not None
    except Exception:
        return False
