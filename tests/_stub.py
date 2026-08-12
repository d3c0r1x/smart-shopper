"""Стаб-провайдер: возвращает валидную схему, чтобы проверять бюджет без сети."""
from __future__ import annotations

from models import SearchConstraints


class OkProvider:
    """Успешный провайдер для тестов бюджета (реальный путь gateway)."""

    async def complete(self, **kwargs):
        return SearchConstraints(query="тест", max_price=None)
