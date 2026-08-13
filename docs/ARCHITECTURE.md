# Архитектура — Smart Shopper

Карта «требование ТЗ → реализация». Каждый пункт ТЗ помечен ссылкой на
конкретный модуль, чтобы можно было проверить реализацию по коду.

## 1. Гибридное поисковое ядро (ТЗ §2)

| Требование ТЗ | Реализация | Где в коде |
|---|---|---|
| Семантический поиск (эмбеддинги) | `SemanticEmbedder` — мультиязычные эмбеддинги **bge-m3** через Ollama `/api/embed`, кэш (LRU + SQLite), честная деградация на лексику при недоступности | `search/embeddings.py` |
| Структурная фильтрация (цена/бренд/рейтинг) | Детерминированный парсер русских запросов: «до 1200 рублей», «не дороже 3 тыс», «чёрный», «рейтинг от 4» → жёсткий префильтр по метаданным | `search/structfilter.py`, `_prefilter` в `core/orchestrator.py` |
| Reranking (двухэтапный) | Этап 1 — гибридный скор (семантика 0.45 + лексика 0.35 + структура 0.20) по топ-кандидатам; этап 2 — LLM-ранжирование и анализ отзывов с вердиктами | `search/rerank.py`, `_hybrid_top` в `core/orchestrator.py` |
| Нормализация данных (SKU matching) | Коллапс кросс-маркетных дублей по EAN: один товар с Ozon/Яндекса/WB → одна карточка (самая дешёвая); без EAN — не трогаем | `_collapse_cross_market` в `core/orchestrator.py` |

Поток запроса: `search_with_constraints` → структурный парсер + LLM
(извлечение ограничений) → префильтр → гибридный реранк → анализ отзывов
(Review Intelligence) → топ-3.

## 2. Выбор open-source компонентов (ТЗ §3)

Матрица лицензий и активности — проверено по официальным репозиториям
(2026-08):

| Компонент | Роль в проекте | Лицензия | Статус в проекте |
|---|---|---|---|
| [bge-m3](https://huggingface.co/BAAI/bge-m3) (BAAI) | Мультиязычные эмбеддинги (semantic search) | MIT (карточка BAAI) | Используется: `ollama pull bge-m3` |
| [Ollama](https://github.com/ollama/ollama) | Локальный инференс LLM + эмбеддинги | MIT | Используется: локальные rank/arbiter/freeform |
| qwen2.5:3b-instruct (Qwen) | Локальная текстовая модель | Apache 2.0 (Qwen) | Используется: `SHOPPER_LOCAL_MODEL` |
| aiogram 3 | Telegram Bot API | MIT | Используется |
| aiohttp | HTTP-API и клиенты | Apache 2.0 | Используется |
| aiosqlite | Хранилище сессий/кэша | MIT | Используется |

Из рекомендованного в ТЗ стека (Crawlee, Qdrant/Milvus/ChromaDB,
LangGraph/LlamaIndex, thefuzz/rapidfuzz, Scrapy) **осознанно не взяты** —
причины:

- **Crawlee/Scrapy** — тяжеловесные фреймворки для краулинга. Наш канал
  данных — уже готовые JSON-эндпоинты SPA (`captured/*.json`), асинхронные
  HTTP-клиенты на aiohttp проще и легче; Scrapy — под веб-сайты с HTML,
  которые нам не нужны.
- **Qdrant/Milvus/ChromaDB** — отдельная векторная БД не нужна: каталог
  на один запрос — десятки карточек, кэш эмбеддингов (LRU + SQLite)
  покрывает повторные запросы. Выделенный сервис — оверинжиниринг для
  этого масштаба.
- **LangGraph/LlamaIndex** — оркестрация графа состояний. Наш пайплайн
  линейный (extract → search → rerank → analyze) с явным кодом; фреймворк
  добавил бы слой абстракции без выигрыша.
- **thefuzz/rapidfuzz** — сопоставление товаров идёт по EAN (жёстко) +
  нечёткое название (встроенный `difflib`/нормализация) + LLM-арбитр;
  отдельная библиотека не нужна.

Критерии ТЗ «активность > 3 мес, звёзды > 500» соблюдены для всех
фактических зависимостей; ключевые (aiogram, aiohttp, Ollama) — активно
поддерживаемые проекты с тысячами звёзд.

## 3. Безопасность (ТЗ §4)

Реализовано: секреты только из `.env` (в git — `.env.example`), SQL строго
параметризован, HTML-экранирование, guardrails для LLM (длина, control-
символы, валидация схем Pydantic), таймауты и вежливые паузы для
маркетплейсов, **уважение robots.txt** (`adapters/robots.py`, fail-open по
RFC 9309) и **экспоненциальный backoff** на 429/5xx (`adapters/base.py`).
Supply chain: `pip-audit` и `npm audit` — 0 уязвимостей (2026-08).
Полный разбор и остаточные риски — `docs/SECURITY.md`.

## 4. Метрики качества (ТЗ §5)

| Метрика | Целевое значение | Реализация |
|---|---|---|
| Precision@5 | ≥ 85% | `tools/eval_precision.py` — авторазметка по структурным требованиям, Precision@K показанных карточек + Precision@5 по гибридному рангу |
| Latency p95 | ≤ 2 c | Тайминги поиска в SQLite (`web.py`, `/api/stats`) + замер p95 в eval |
| Uptime | ≥ 99.5% | `/health` эндпоинт, `/api/stats` — uptime и счётчики запросов |
| Coverage маркетплейсов | ≥ 90% | `/diag` — статус каждого адаптера; честное пропускание заблокированных площадок |
| Data Freshness | ≤ 1 ч | TTL кэша поиска (SQLite, `cache.expires_at`, очистка `cleanup_expired`) |

## 5. Структура репозитория

```
project12_smart_shopper/
├── bot.py                  # Telegram-хендлеры, inline-кнопки
├── web.py                  # HTTP-API для Mini App + /api/stats
├── config.py               # Все настройки (env + дефолты)
├── models.py               # Product, Review, SessionState, ...
├── search/                 # Гибридный поиск (ТЗ §2)
│   ├── structfilter.py     #   детерминированный парсер ограничений
│   ├── embeddings.py       #   SemanticEmbedder (bge-m3 через Ollama)
│   └── rerank.py           #   HybridReranker (семантика+лексика+структура)
├── llm/                    # Слой ИИ
│   ├── gateway.py          #   маршрутизация: локальная → Mistral → OpenRouter
│   ├── providers.py        #   клиенты провайдеров
│   ├── schemas.py          #   Pydantic-схемы ответов
│   └── guardrails.py       #   санитизация ввода (ТЗ §4)
├── core/orchestrator.py    # Сценарии: поиск, фото, сравнение, память
├── adapters/               # Ozon / Яндекс / WB + демо-каталог
├── matcher/matcher.py      # EAN-матчинг и сравнение цен
├── review/intelligence.py  # Review Intelligence (вердикты по отзывам)
├── storage/db.py           # SQLite: сессии, избранное, кэш, метрики
├── tools/eval_precision.py # Оценка Precision@K и p95 (ТЗ §5)
├── captured/               # Зафиксированные эндпоинты (URL+заголовки+cookies)
├── tests/                  # 125+ тестов (unit + интеграционные через Dispatcher)
└── miniapp/                # React Mini App (Telegram)
```

## 6. Развёртывание

- Локально: `run_bot12.cmd` (читает ключи из корневого `.env`), или
  `SHOPPER_DEMO_MODE=1` — без сети и ключей.
- Локальная LLM: `ollama pull qwen2.5:3b-instruct-q4_K_M` и
  `ollama pull bge-m3`; включить `SHOPPER_LOCAL_LLM=1`, `SHOPPER_SEMANTIC_ENABLED=1`.
- Детали — `README.md`.
