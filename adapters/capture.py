"""Загрузка зафиксированных JSON-эндпоинтов (канал 1).

captured/{ozon,yandex}.json создаётся инструментом
tools/capture_endpoints.py (автоматизированная процедура DevTools из PRD §3):
набор заголовков и cookies берётся 1-в-1 из реального браузерного запроса.

Адаптеры используют этот файл как основной канал: точный URL, заголовки
и (после бутстрапа) cookies. Если файла нет — работает fallback-цепочка
известных семейств эндпоинтов и парсеров.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger(__name__)

CAPTURED_DIR = Path(__file__).resolve().parent.parent / "captured"


def _classify(url: str) -> str | None:
    """Определяет назначение эндпоинта по URL (поиск/карточка/отзывы)."""
    low = unquote(url).lower()
    if "/search/" in low or "/search?" in low or "search-request" in low:
        return "search"
    if "/reviews" in low or "/otzyvy" in low or "review" in low:
        return "reviews"
    if "/product/" in low:
        return "card"
    return None


def load_captured(market: str) -> dict | None:
    """Читает captured/{market}.json → {purpose: [endpoint, ...]}."""
    path = CAPTURED_DIR / f"{market}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("captured/%s.json повреждён: %s", market, exc)
        return None
    out: dict[str, list[dict]] = {"search": [], "card": [], "reviews": [],
                                  "other": []}
    for ep in data.get("endpoints", []):
        url = ep.get("url", "")
        purpose = _classify(url) or "other"
        out[purpose].append(ep)
    return out


def pick_endpoint(captured: dict | None, purpose: str) -> dict | None:
    """Выбирает первый рабочий эндпоинт назначения (status 200 предпочтительно)."""
    if not captured:
        return None
    eps = captured.get(purpose) or []
    for ep in sorted(eps, key=lambda e: 0 if e.get("status") == 200 else 1):
        if ep.get("url"):
            return ep
    return None
