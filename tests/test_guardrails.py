"""Тесты санитизации ввода LLM (llm/guardrails.py, ТЗ §4)."""
from __future__ import annotations

from llm.guardrails import sanitize_item, sanitize_user_text


def test_strips_control_characters():
    raw = "маска\0для\x01сна\n"
    out = sanitize_user_text(raw)
    assert "\x00" not in out
    assert "\x01" not in out
    assert "маска" in out


def test_collapses_whitespace():
    out = sanitize_user_text("маска   для\t\t сна\n\n\n")
    assert out == "маска для сна"


def test_empty():
    assert sanitize_user_text("") == ""
    assert sanitize_user_text(None) == ""


def test_length_cap():
    out = sanitize_user_text("а" * 5000, max_chars=100)
    assert len(out) == 100


def test_keeps_cyrillic_and_punctuation():
    out = sanitize_user_text("Купи «маску для сна» до 1000 ₽!")
    assert "«маску для сна»" in out
    assert "₽" in out


def test_item_truncation():
    out = sanitize_item("x" * 700, max_chars=500)
    assert len(out) == 500
