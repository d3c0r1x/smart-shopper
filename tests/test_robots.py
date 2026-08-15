"""Тесты уважения robots.txt (ТЗ §4): парсер и longest-match правила.

Сеть не нужна — тестируем чистые функции. Поведение fail-open
(нет файла → разрешено) покрыто на уровне RobotsCache.allows с
мокнутым _fetch.
"""
from __future__ import annotations


from adapters.robots import RobotsCache, _parse_robots, _path_allowed, _split_url


def test_no_robots_file_means_allowed():
    """Стандарт: отсутствие robots.txt = всё разрешено."""
    rules = _parse_robots("")  # пустой ответ
    assert _path_allowed("/api/search", rules) is True


def test_404_means_allowed():
    """HTTP 404 → fail-open."""
    cache = RobotsCache()

    async def fetch(scheme, host):
        return []  # имитация 404

    cache._fetch = fetch
    import asyncio
    assert asyncio.run(cache.allows("https://market.test/api/search",
                                     "/api/search")) is True


def test_disallow_root_blocks_everything():
    rules = _parse_robots("User-agent: *\nDisallow: /\n")
    assert _path_allowed("/", rules) is False
    assert _path_allowed("/api/search", rules) is False


def test_disallow_prefix_blocks_subpath():
    rules = _parse_robots("User-agent: *\nDisallow: /api/\n")
    assert _path_allowed("/api/search", rules) is False
    assert _path_allowed("/product/123", rules) is True


def test_longest_match_wins():
    """Disallow: /api/ перекрывается более длинным Disallow: /api/search."""
    rules = _parse_robots(
        "User-agent: *\nDisallow: /api/\nDisallow: /api/search/\n")
    assert _path_allowed("/api/search/item", rules) is False
    assert _path_allowed("/api/cart", rules) is False  # только /api/ (короткий)


def test_wildcard_patterns_ignored():
    """Паттерны с * и $ наш парсер не поддерживает — не блокируем."""
    rules = _parse_robots("User-agent: *\nDisallow: /api/*.json\n")
    assert _path_allowed("/api/products.json", rules) is True


def test_specific_user_agent_only():
    """Блок для другого UA не применяется к нашему."""
    rules = _parse_robots(
        "User-agent: googlebot\nDisallow: /api/\nUser-agent: *\nDisallow: /admin/\n")
    assert _path_allowed("/admin/x", rules) is False
    assert _path_allowed("/api/search", rules) is True


def test_allow_is_ignored_conservatively():
    """Allow не поддерживаем: если Disallow запрещает — блокируем."""
    rules = _parse_robots(
        "User-agent: *\nAllow: /api/public\nDisallow: /api/\n")
    assert _path_allowed("/api/public/x", rules) is False


def test_comments_and_empty_lines():
    text = """
# сайт
User-agent: *

Disallow: /private/   # приватное
"""
    rules = _parse_robots(text)
    assert _path_allowed("/private/data", rules) is False
    assert _path_allowed("/public", rules) is True


def test_split_url():
    assert _split_url("https://ozon.ru/api/x") == ("https", "ozon.ru")
    assert _split_url("http://127.0.0.1:8081/") == ("http", "127.0.0.1")
    assert _split_url("ozon.ru/path")[1] == "ozon.ru"
