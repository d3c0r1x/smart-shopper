"""Уважение robots.txt — этичный парсинг (ТЗ §4).

Перед запросом к маркетплейсу адаптер спрашивает: разрешает ли сайт
этот путь. Правила стандарта:

- нет robots.txt (404/таймаут/сетевая ошибка) → запрос разрешён
  (fail-open, как предписывает RFC 9309);
- есть блок `User-agent: *` → применяются его Disallow (longest match);
- блоков нет → запрос разрешён;
- пути с подстановочными символами `*`/`$` (не поддерживаются нашим
  минимальным парсером) → игнорируются, как и `Allow` (мы консервативны
  только по Disallow: если запрет не распознан однозначно, путь пропускаем
  только при точном/префиксном совпадении).

Файл кэшируется на SHOPPER_ROBOTS_TTL (по умолчанию 1 час), чтобы не
дёргать сайт на каждый поиск. Отключить — SHOPPER_RESPECT_ROBOTS=0
(не рекомендуется: это пункт ТЗ §4).
"""
from __future__ import annotations

import asyncio
import logging
import time

import config

logger = logging.getLogger(__name__)

# Наш пользовательский агент в robots.txt (обычно просто "*").
_USER_AGENTS = ("smart-shopper", "*")


class RobotsCache:
    """Загружает и кэширует robots.txt по хосту."""

    def __init__(self, timeout: float | None = None, ttl: float | None = None) -> None:
        self._timeout = timeout if timeout is not None else config.ROBOTS_TIMEOUT
        self._ttl = ttl if ttl is not None else config.ROBOTS_TTL
        self._cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}

    async def _fetch(self, scheme: str, host: str) -> list[tuple[str, str]]:
        """(user_agent, path) пары Disallow. Пусто = всё разрешено."""
        import httpx

        url = f"{scheme}://{host}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=self._timeout,
                                         follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    return []
                return _parse_robots(resp.text)
        except Exception as exc:  # fail-open: сайт недоступен — не блокируем
            logger.debug("robots.txt для %s недоступен (%s) — запрос разрешён",
                         host, exc)
            return []

    async def allows(self, url: str, path: str) -> bool:
        """Разрешён ли запрос к path на хосте url.

        При недоступности robots.txt возвращает True (стандарт fail-open).
        """
        if not config.RESPECT_ROBOTS:
            return True
        scheme, host = _split_url(url)
        if host is None:
            return True
        now = time.monotonic()
        cached = self._cache.get(host)
        if cached and now - cached[0] < self._ttl:
            return _path_allowed(path, cached[1])
        rules = await self._fetch(scheme, host)
        self._cache[host] = (now, rules)
        return _path_allowed(path, rules)


def _split_url(url: str) -> tuple[str, str | None]:
    try:
        if "://" not in url:
            url = "https://" + url  # без схемы — считаем https (как браузер)
        scheme, _, rest = url.partition("://")
        host = rest.split("/", 1)[0].split(":", 1)[0]
        return scheme or "https", host or None
    except Exception:
        return "https", None


def _parse_robots(text: str) -> list[tuple[str, str]]:
    """Возвращает (user_agent, disallow_path) для всех групп."""
    rules: list[tuple[str, str]] = []
    group_ua: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            group_ua = value.lower()
        elif field == "disallow" and group_ua is not None:
            if value and ("*" in value or "$" in value):
                continue  # шаблоны не поддерживаем — пропускаем
            rules.append((group_ua, value))
    return rules


def _path_allowed(path: str, rules: list[tuple[str, str]]) -> bool:
    """Longest-match по правилам подходящего user-agent."""
    best: str | None = None
    for ua, dis in rules:
        if ua not in _USER_AGENTS:
            continue
        # только точные/префиксные запреты; "/" в конце — префикс всего
        if dis == "/" or path.startswith(dis.rstrip("/") + "/") or path == dis:
            if best is None or len(dis) > len(best):
                best = dis
    return best is None
