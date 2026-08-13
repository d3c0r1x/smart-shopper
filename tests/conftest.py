"""Делает модули проекта импортируемыми для pytest; демо-режим по умолчанию."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SHOPPER_DEMO_MODE", "1")
# Локальная LLM (Ollama) в тестах выключена: тесты должны быть
# детерминированными (mock-провайдер), а не зависеть от того,
# запущена ли Ollama на машине разработчика.
os.environ.setdefault("SHOPPER_LOCAL_LLM", "0")
# Семантический слой (эмбеддинги через Ollama) в тестах выключен:
# тесты детерминированные, без сети (Ollama может быть не запущена).
os.environ.setdefault("SHOPPER_SEMANTIC_ENABLED", "0")
# Сетевые проверки robots.txt в тестах не нужны: тесты детерминированные,
# без сети (файл может быть недоступен). Парсер покрыт unit-тестами.
os.environ.setdefault("SHOPPER_RESPECT_ROBOTS", "0")
