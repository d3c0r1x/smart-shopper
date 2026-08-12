"""Конфигурация «Умного Шоппера» через переменные окружения (stdlib os.getenv).

Все секреты — только в .env (PRD, раздел 9): токен бота, ключ OpenRouter,
опциональные ключи Seller/Partner API. В коде секретов нет.
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Telegram ─────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("SHOPPER_BOT_TOKEN", "")

# ── OpenRouter (PRD, раздел 3: лимиты и реестр :free-моделей) ────
# Имя ключа из корневого .env портфолио — OPEN_ROUTER_KEY (вторичное).
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY",
                       os.getenv("MISTRAL_KEY", ""))
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY",
                               os.getenv("OPEN_ROUTER_KEY", ""))
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Дневной лимит бесплатных запросов: 50 — без вложений, 1000 — после
# разовой покупки кредитов от $10 (источник: документация OpenRouter,
# PRD раздел 3 [[8]]). Пользователь может переопределить.
DAILY_LLM_LIMIT = int(os.getenv("SHOPPER_DAILY_LLM_LIMIT", "50"))
# Лимит запросов в минуту на :free-модели: 20 [[8]].
RATE_PER_MINUTE = int(os.getenv("SHOPPER_RATE_PER_MINUTE", "20"))
# Потолок времени на один структурный вызов LLM (сек). При превышении —
# честный фолбэк на mock, чтобы бот оставался отзывчивым.
STRUCTURED_MAX_SECONDS = float(os.getenv("SHOPPER_STRUCTURED_MAX_SECONDS", "40"))
# Профиль моделей: fast | quality (см. llm/gateway.py — реестр).
LLM_PROFILE = os.getenv("SHOPPER_LLM_PROFILE", "quality")
LLM_TIMEOUT_SECONDS = float(os.getenv("SHOPPER_LLM_TIMEOUT_SECONDS", "60"))

# ── Маркетплейсы ─────────────────────────────────────────────────
# Демо-режим: не ходит в сеть, работает на встроенном каталоге.
DEMO_MODE = os.getenv("SHOPPER_DEMO_MODE", "0") == "1"
# Опциональные официальные ключи (PRD, раздел 9: Seller API Ozon и
# Partner API Яндекс Маркета — это API продавца, для каталога не нужны;
# основной канал данных — публичные web-эндпоинты).
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID", "")
OZON_API_KEY = os.getenv("OZON_API_KEY", os.getenv("OZON_KEY", ""))
YM_API_KEY = os.getenv("YM_API_KEY", os.getenv("YANDEX_MARKET_KEY", ""))
YM_REGION = int(os.getenv("SHOPPER_YM_REGION", "213"))

HTTP_CLIENT = os.getenv("SHOPPER_HTTP_CLIENT", "curl_cffi")
PROXY = os.getenv("SHOPPER_PROXY", "")
MAX_RETRIES = int(os.getenv("SHOPPER_MAX_RETRIES", "3"))
# Вежливая пауза между запросами к публичным эндпоинтам (секунды).
POLITE_DELAY = float(os.getenv("SHOPPER_POLITE_DELAY", "0.4"))

# ── Хранилище (MVP: SQLite; продакшн-таргет PostgreSQL+Redis — PRD §4) ──
DB_PATH = os.getenv("SHOPPER_DB_PATH", os.path.join(BASE_DIR, "shopper.db"))
# TTL кэшей, секунды (проектные значения, PRD раздел 8)
CACHE_SEARCH_TTL = int(os.getenv("SHOPPER_CACHE_SEARCH_TTL", "1800"))  # 30 мин
CACHE_CARD_TTL = int(os.getenv("SHOPPER_CACHE_CARD_TTL", "21600"))  # 6 ч
CACHE_REVIEWS_TTL = int(os.getenv("SHOPPER_CACHE_REVIEWS_TTL", "86400"))  # 24 ч

# ── HTTP-API для Mini App (один backend, два рендерера) ──────────
# Порт локального HTTP-сервера; VITE_API_URL в Mini App указывает сюда
# (через HTTPS-туннель в продакшене).
API_PORT = int(os.getenv("SHOPPER_API_PORT", "8081"))
# URL Mini App (HTTPS, обязателен для web_app-кнопки Telegram)
MINIAPP_URL = os.getenv("SHOPPER_MINIAPP_URL",
                       "https://d3c0r1x.github.io/smart-shopper/")
# Если задан — Mini App обязан слать его в заголовке X-API-Token.
API_TOKEN = os.getenv("SHOPPER_API_TOKEN", "")
# Разрешить анонимный доступ (user_id из query) при невалидном initData —
# полезно для локальной разработки Mini App вне Telegram.
API_ALLOW_ANON = os.getenv("SHOPPER_API_ALLOW_ANON", "1") == "1"

# ── UX ────────────────────────────────────────────────────────────
THROTTLE_MIN_INTERVAL = float(os.getenv("SHOPPER_THROTTLE_MIN_INTERVAL", "0.7"))
DEFAULT_MARKETPLACE = os.getenv("SHOPPER_DEFAULT_MARKETPLACE", "both")  # both|ozon|yandex

# Сколько кандидатов отбирается на каждом этапе (PRD, раздел 6)
CANDIDATES_PER_MARKET = int(os.getenv("SHOPPER_CANDIDATES_PER_MARKET", "10"))
REVIEWS_PER_PRODUCT = int(os.getenv("SHOPPER_REVIEWS_PER_PRODUCT", "40"))
