"""llm/guardrails.py — санитизация ввода LLM (ТЗ §4 «Защита от инъекций»).

Пользовательский текст попадает в промпты моделей; он же может прийти из
внешних источников (отзывы маркетплейсов). Перед тем как текст станет частью
промпта, он проходит санитизацию:
  * control-символы (включая null, разделители) заменяются пробелом;
  * последовательности пробелов схлопываются;
  * длина ограничена (защита от промпт-флуда и перерасхода контекста).

Поля структурных ответов валидируются pydantic-схемами (llm/schemas.py) —
второй рубеж: модель физически не может вернуть лишние ключи/типы.
"""
from __future__ import annotations

import re

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")


def sanitize_user_text(text: str, max_chars: int = 4000) -> str:
    """Чистит пользовательский ввод перед передачей в LLM-промпт."""
    if not text:
        return ""
    cleaned = _CONTROL_RE.sub(" ", text)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    return cleaned[:max_chars]


def sanitize_item(text: str, max_chars: int = 500) -> str:
    """Чистит внешний фрагмент (название, отзыв) перед вставкой в промпт."""
    if not text:
        return ""
    cleaned = _CONTROL_RE.sub(" ", text)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    return cleaned[:max_chars]
