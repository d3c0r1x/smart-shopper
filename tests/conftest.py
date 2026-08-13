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
