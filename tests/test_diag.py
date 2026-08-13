from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bot as botmod


def test_diagnostics_screen_is_available(monkeypatch):
    async def fake_budget_info():
        return {
            "used": 2,
            "limit": 50,
            "remaining": 48,
            "real_provider": True,
            "provider": "Mistral",
            "local": True,
            "profile": "quality",
        }

    monkeypatch.setattr(botmod.llm, "budget_info", fake_budget_info)
    monkeypatch.setattr(
        botmod,
        "adapters",
        [SimpleNamespace(name="ozon"), SimpleNamespace(name="yandex"),
         SimpleNamespace(name="wb")],
    )

    text = asyncio.run(botmod._diag_text())

    assert "Диагностика" in text
    assert "Ozon" in text
    assert "Яндекс" in text
    assert "Wildberries" in text
    assert "Mistral" in text
    assert "2/50" in text
