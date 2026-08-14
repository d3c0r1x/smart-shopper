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
# ── OpenTelemetry (ТЗ §5: APM мониторинг) ────────────────────────
# OTLP HTTP endpoint (напр. http://localhost:4318/v1). Если пусто —
# телеметрия выключена, метрики считает встроенная миддлварь web.py.
OTEL_ENDPOINT = os.getenv("SHOPPER_OTEL_ENDPOINT", "").rstrip("/")

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
# Partner API Яндекс Маркета — это API продавца для каталога не нужны;
# основной канал данных — публичные web-эндпоинты).
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID", "")
OZON_API_KEY = os.getenv("OZON_API_KEY", os.getenv("OZON_KEY", ""))
YM_API_KEY = os.getenv("YM_API_KEY", os.getenv("YANDEX_MARKET_KEY", ""))
YM_REGION = int(os.getenv("SHOPPER_YM_REGION", "213"))

HTTP_CLIENT = os.getenv("SHOPPER_HTTP_CLIENT", "curl_cffi")
PROXY = os.getenv("SHOPPER_PROXY", "")
# ── Пул прокси подписки Happ ──────────────────────────────────────
# Подписка = список vless-серверов (у Happ 300+): каждый — отдельный
# выходной IP. Пул запускает xray-инстансы и ротирует IP при блокировке.
PROXY_POOL = os.getenv("SHOPPER_PROXY_POOL", "1") == "1"
POOL_SIZE = int(os.getenv("SHOPPER_POOL_SIZE", "6"))
# URL подписки (из логов Happ) — в корневом .env, не в репозитории.
SUBSCRIPTION_URL = os.getenv("SHOPPER_SUBSCRIPTION_URL", "")
MAX_RETRIES = int(os.getenv("SHOPPER_MAX_RETRIES", "3"))
# Вежливая пауза между запросами к публичным эндпоинтам (секунды).
POLITE_DELAY = float(os.getenv("SHOPPER_POLITE_DELAY", "0.4"))
# Ступенчатый параллельный опрос площадок (ТЗ §5, latency p95).
# Ozon стартует первым в одиночку, остальные — через OZON_HEAD_START
# секунд параллельно: суммарное время -> максимуму, челлендж Ozon
# при этом не ломается (проверено живым A/B).
PARALLEL_MARKETS = os.getenv("SHOPPER_PARALLEL_MARKETS", "1") == "1"
OZON_HEAD_START = float(os.getenv("SHOPPER_OZON_HEAD_START", "3"))

# Этичный парсинг (ТЗ §4): уважение robots.txt + кэш файла на 1 час.
RESPECT_ROBOTS = os.getenv("SHOPPER_RESPECT_ROBOTS", "1") == "1"
ROBOTS_TIMEOUT = float(os.getenv("SHOPPER_ROBOTS_TIMEOUT", "5"))
ROBOTS_TTL = float(os.getenv("SHOPPER_ROBOTS_TTL", "3600"))

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
DEFAULT_MARKETPLACE = os.getenv("SHOPPER_DEFAULT_MARKETPLACE", "both")  # both|ozon|yandex|wb

# Сколько кандидатов отбирается на каждом этапе (PRD, раздел 6)
CANDIDATES_PER_MARKET = int(os.getenv("SHOPPER_CANDIDATES_PER_MARKET", "10"))
# Жёсткий предел одного поискового адаптера: блокировка одной площадки
# не должна задерживать остальные и готовый ответ пользователю.
MARKET_SEARCH_TIMEOUT_SECONDS = float(
    os.getenv("SHOPPER_MARKET_SEARCH_TIMEOUT_SECONDS", "45")
)
REVIEWS_PER_PRODUCT = int(os.getenv("SHOPPER_REVIEWS_PER_PRODUCT", "40"))
REVIEWS_FETCH_TIMEOUT_SECONDS = float(
    os.getenv("SHOPPER_REVIEWS_FETCH_TIMEOUT_SECONDS", "15")
)

# ── Гибридный поиск (ТЗ §2): семантика + структура + реранк ────────
# Семантический слой: эмбеддинги через Ollama (bge-m3 — мультиязычная).
# Если модель не установлена или Ollama недоступна — честная деградация
# на лексическое ранжирование (см. search/embeddings.py).
SEMANTIC_ENABLED = os.getenv("SHOPPER_SEMANTIC_ENABLED", "1") == "1"
EMBED_MODEL = os.getenv("SHOPPER_EMBED_MODEL", "bge-m3")
EMBED_TIMEOUT = float(os.getenv("SHOPPER_EMBED_TIMEOUT", "20"))
EMBED_CACHE = int(os.getenv("SHOPPER_EMBED_CACHE", "2048"))
# Веса гибридного реранка: семантика, лексика, структура.
_RERANK_WEIGHTS = os.getenv("SHOPPER_RERANK_WEIGHTS", "0.45,0.35,0.20")
RERANK_WEIGHTS = tuple(
    float(x.strip()) for x in _RERANK_WEIGHTS.split(",") if x.strip())
if len(RERANK_WEIGHTS) != 3:  # защита от битого конфига
    RERANK_WEIGHTS = (0.45, 0.35, 0.20)
# Сколько кандидатов проходит во второй этап (LLM/анализ отзывов).
TOP_CANDIDATES = int(os.getenv("SHOPPER_TOP_CANDIDATES", "8"))

# ── Guardrails (ТЗ §4): санитизация ввода LLM ─────────────────────
PROMPT_MAX_CHARS = int(os.getenv("SHOPPER_PROMPT_MAX_CHARS", "4000"))
REVIEW_TEXT_MAX_CHARS = int(os.getenv("SHOPPER_REVIEW_TEXT_MAX_CHARS", "500"))

# ── Локальная LLM (Ollama на GPU) ────────────────────────────────
# Текстовые задачи (constraints/rank/arbiter/review/freeform) можно
# выполнять локально: бесплатно, без дневного лимита и приватно.
# Если Ollama недоступна — цепочка честно уходит на облако.
LOCAL_LLM = os.getenv("SHOPPER_LOCAL_LLM", "1") == "1"
LOCAL_BASE_URL = os.getenv("SHOPPER_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
LOCAL_MODEL = os.getenv("SHOPPER_LOCAL_MODEL", "qwen2.5:3b-instruct-q4_K_M")
LOCAL_TIMEOUT = float(os.getenv("SHOPPER_LOCAL_TIMEOUT", "30"))
# Обогащение карточки (характеристики/фото) — дополнительный запрос.
# Он не должен скрывать уже найденный товар при антиботе или медленной площадке.
CARD_ENRICH_TIMEOUT_SECONDS = float(
    os.getenv("SHOPPER_CARD_ENRICH_TIMEOUT_SECONDS", "8")
)
