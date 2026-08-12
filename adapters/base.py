"""Контракт адаптеров и общая HTTP-обвязка.

Адаптер обязан реализовать: search(), get_card(), get_reviews(), get_photos().
Транспорт (httpx / curl_cffi с имитацией Chrome) и retry — общие, чтобы
не дублировать код между маркетплейсами. Честный статус публичных
web-эндпоинтов — в README: они недокументированные и защищены антиботом,
поэтому предусмотрены прокси и демо-режим.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:  # pragma: no cover
    from models import Product, Review

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

_RETRY_STATUSES = {429, *range(500, 600)}


def _backoff(attempt: int, min_delay: float = 0.5) -> float:
    """Экспоненциальная задержка: 0.5, 1.0, 2.0 … (потолок 10 c)."""
    return min(min_delay * (2 ** (attempt - 1)), 10.0)


try:
    from curl_cffi.requests import AsyncSession as CurlCffiSession

    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    CurlCffiSession = None
    HAS_CURL_CFFI = False


class HttpxTransport:
    """Транспорт на httpx (без имитации отпечатка)."""

    def __init__(self, timeout: float = 20.0, proxy: str = "") -> None:
        import httpx

        self._client = httpx.AsyncClient(timeout=timeout, proxy=proxy or None)

    async def get(self, url: str, *, params=None, headers=None) -> tuple[int, str]:
        resp = await self._client.get(url, params=params, headers=headers)
        return resp.status_code, resp.text

    async def aclose(self) -> None:
        await self._client.aclose()


class CurlCffiTransport:
    """Транспорт на curl_cffi: имитация TLS/HTTP2-отпечатка Chrome."""

    def __init__(self, timeout: float = 20.0, impersonate: str = "chrome",
                 proxies: dict | None = None) -> None:
        self._session = CurlCffiSession(impersonate=impersonate, timeout=timeout,
                                        proxies=proxies)

    async def get(self, url: str, *, params=None, headers=None) -> tuple[int, str]:
        resp = await self._session.get(url, params=params, headers=headers,
                                       allow_redirects=False)
        return resp.status_code, resp.text

    async def aclose(self) -> None:
        closer = getattr(self._session, "aclose", None) or self._session.close
        await closer()


def _make_transport():
    if config.HTTP_CLIENT == "curl_cffi" and HAS_CURL_CFFI:
        proxies = None
        if config.PROXY:
            proxies = {"http": config.PROXY, "https": config.PROXY}
        return CurlCffiTransport(proxies=proxies)
    return HttpxTransport(proxy=config.PROXY)


class BaseAdapter:
    """Базовый HTTP-адаптер: GET с retry на 429/5xx и вежливой паузой."""

    name = "base"
    headers = BROWSER_HEADERS

    def __init__(self, transport=None, max_retries: int | None = None) -> None:
        self._transport = transport if transport is not None else _make_transport()
        self._max_retries = max_retries or config.MAX_RETRIES

    async def _get(self, url: str, *, params=None, extra_headers: dict | None = None,
                   retries: int | None = None) -> tuple[int, str]:
        max_retries = self._max_retries if retries is None else retries
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                if config.POLITE_DELAY and attempt > 1:
                    await asyncio.sleep(config.POLITE_DELAY)
                status, text = await self._transport.get(
                    url, params=params, headers={**self.headers, **(extra_headers or {})}
                )
            except Exception as exc:
                last_exc = exc
                if attempt == max_retries:
                    raise
                await asyncio.sleep(_backoff(attempt))
                continue
            if status in _RETRY_STATUSES and attempt < max_retries:
                await asyncio.sleep(_backoff(attempt))
                continue
            return status, text
        raise last_exc if last_exc is not None else RuntimeError("unreachable")

    # ── контракт (обязателен к реализации) ─────────────────────────
    async def search(self, query: str, limit: int = 5) -> list["Product"]:
        raise NotImplementedError

    async def get_card(self, ext_id: str) -> "Product | None":
        raise NotImplementedError

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list["Review"]:
        raise NotImplementedError

    async def get_photos(self, ext_id: str) -> list[str]:
        raise NotImplementedError

    async def aclose(self) -> None:
        await self._transport.aclose()
