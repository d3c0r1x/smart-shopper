# Smart Shopper

> **Интересный личный проект, над которым я работал длительное время.** По мере разработки он вырос из идеи Telegram-бота в полноценный AI-продукт: общий backend, несколько маркетплейсов, поиск по тексту и фото, анализ отзывов, Mini App, локальные и облачные LLM, fallback-цепочки и большой набор тестов.
>
> **Status:** active portfolio project / MVP.
>
> [Live demo](https://d3c0r1x.github.io/smart-shopper/) · [Architecture](docs/ARCHITECTURE.md) · [Engineering decisions](docs/DECISIONS.md) · [Security](docs/SECURITY.md)

## 1. Идея

Smart Shopper — AI-ассистент покупок для Ozon, Яндекс Маркета и Wildberries.

Пользователь может написать обычным языком:

- «найди чёрную маску для сна до 1500 ₽»;
- «нужен рюкзак для ноутбука 15.6, до 5000 ₽»;
- «сравни этот товар на Ozon, WB и Яндекс Маркете»;
- отправить фотографию предмета и попросить найти похожий товар;
- спросить по отзывам, действительно ли товар соответствует конкретному требованию.

Основной принцип проекта:

**пользовательский запрос → извлечение требований → реальный поиск → фильтрация → ранжирование → проверка отзывов → рекомендация.**

LLM не выдумывает карточки товаров. Названия, цены, ссылки и тексты отзывов приходят от адаптеров источников.

## 2. Что умеет

### Поиск
Поиск по свободному тексту и по фотографии.

### Несколько маркетплейсов
Адаптеры отделяют источники друг от друга:

- Ozon;
- Яндекс Маркет;
- Wildberries;
- demo adapter для воспроизводимых запусков.

### Сравнение
Один и тот же товар можно сравнить между площадками и найти более дешёвое предложение.

### Анализ отзывов
Требования пользователя превращаются в отдельные проверяемые пункты:

- ✅ подтверждено отзывами;
- ❌ опровергнуто отзывами;
- ⚠️ недостаточно данных.

### Гибридный поиск
Используются одновременно:

- semantic similarity;
- лексическое совпадение;
- структурные фильтры цены / рейтинга / цвета и т. п.

### AI gateway
Есть профили моделей, бюджеты, rate limit, fallback между провайдерами и deterministic fallback.

### Два интерфейса
Один backend обслуживает:

1. Telegram-бот;
2. React Telegram Mini App.

Состояние сессии хранится в БД, а не в UI.

## 3. Архитектура

```
Telegram ───────────────┐
                        ├──> Orchestrator
React Mini App ─────────┘          │
                                   ├── marketplace adapters
                                   ├── hybrid matcher/ranker
                                   ├── review intelligence
                                   ├── LLM gateway
                                   ├── vision
                                   └── SQLite / session state
```

### Почему так

LLM хорошо подходит для интерпретации запроса и работы с неструктурированным текстом, но плохо подходит как источник фактов.

Поэтому:

- факты → адаптеры;
- числа → обычный код;
- жёсткие ограничения → структурные фильтры;
- семантика → embeddings;
- сложные языковые задачи → LLM;
- отсутствие LLM → fallback, а не выдуманный ответ.

Подробное обоснование решений находится в [docs/DECISIONS.md](docs/DECISIONS.md).

## 4. Структура репозитория

```
adapters/
  base.py              # общий контракт источника
  demo.py              # детерминированный каталог для demo
  ozon.py              # Ozon adapter
  ozon_browser.py      # браузерный канал Ozon
  yandex.py            # Yandex adapter
  yandex_browser.py   # браузерный канал Yandex
  wb_browser.py        # браузерный канал Wildberries
  capture.py           # фиксация реальных web-запросов
  robots.py            # robots / ограничения

core/
  orchestrator.py      # основной сценарий поиска

search/
  embeddings.py        # semantic layer
  rerank.py            # hybrid rerank
  structfilter.py      # жёсткие структурные ограничения

matcher/
  matcher.py           # сравнение / dedup / collapse

review/
  intelligence.py      # проверка требований по отзывам

llm/
  gateway.py           # routing, budgets, fallback
  providers.py         # провайдеры
  schemas.py           # структурированные ответы
  guardrails.py        # ограничения входа
  prompts.py           # prompt templates

storage/
  db.py                # SQLite/session/cache

miniapp/
  src/App.tsx          # Telegram Mini App
  src/api.ts            # запросы к backend
  src/types.ts         # типы API

tools/
  capture_endpoints.py # захват web endpoint'ов
  eval_precision.py    # evaluation
  load_test.py         # load checks

tests/
  ...                  # unit/integration/security checks
```

Также есть документация в [docs/](docs/).

## 5. Быстрый локальный запуск

### Требования

- Python 3.11+;
- Node.js + npm — только для Mini App;
- Telegram Bot Token — для полноценного Telegram-сценария;
- LLM ключи не обязательны в demo mode.

### Backend / Telegram

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python bot.py
```

Минимально нужен:

```
SHOPPER_BOT_TOKEN=123456:ABC...
SHOPPER_DEMO_MODE=1
```

В demo mode используются встроенные данные, поэтому запуск не зависит от антибот-защиты маркетплейсов.

## 6. Примеры использования Telegram-бота

После `/start` можно использовать обычный текст.

Пример 1:

```
Найди чёрную маску для сна до 1500 рублей
```

Пример 2:

```
Найди рюкзак для ноутбука 15.6 дюйма, до 5000 ₽,
желательно с отделением для зарядки
```

Пример 3:

```
Сравни цены на беспроводные наушники
```

Командный интерфейс также содержит:

| Команда | Назначение |
|---|---|
| `/start` | главное меню |
| `/search ...` | поиск |
| `/ask ...` | свободный вопрос |
| `/compare ...` | сравнение |
| `/favorites` | избранное |
| `/settings` | настройки |
| `/budget` | информация о лимите LLM |
| `/diag` | диагностика |
| `/stats` | статистика |
| `/reset` | очистка контекста |

Фото можно отправлять непосредственно в бота: сценарий Vision превращает изображение в поисковое описание и запускает тот же pipeline.

## 7. Demo mode

Для просмотра проекта без приватных ключей:

```
SHOPPER_DEMO_MODE=1
```

Это важный режим для портфолио:

- сеть маркетплейсов не нужна;
- цены и товары детерминированы;
- тесты воспроизводимы;
- поведение можно показать на чистой машине.

В demo mode проект не должен выдавать demo-карточки как реальные предложения.

## 8. Работа с реальными маркетплейсами

Основные web-каналы используют реальные публичные JSON-запросы, которые сайты делают сами.

Для фиксации endpoint'ов:

```bash
python tools/capture_endpoints.py --market ozon --query "маска для сна"
python tools/capture_endpoints.py --market yandex --query "маска для сна"
```

В репозитории поддерживается прокси:

```
SHOPPER_PROXY=http://user:pass@host:port
```

Публичные web-endpoint'ы маркетплейсов могут ограничивать автоматические запросы. Поэтому пустой результат не заменяется выдуманным товаром.

## 9. LLM

Поддерживаются локальный Ollama и облачные провайдеры.

Пример:

```
SHOPPER_LOCAL_LLM=1
SHOPPER_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
SHOPPER_LOCAL_MODEL=qwen2.5:3b-instruct-q4_K_M
```

Semantic layer:

```bash
ollama pull bge-m3
```

Облачные настройки:

```
OPENROUTER_API_KEY=...
MISTRAL_API_KEY=...
SHOPPER_LLM_PROFILE=quality
```

Логика fallback:

```
local model
   ↓
Mistral / OpenRouter
   ↓
другая модель из registry
   ↓
deterministic fallback
```

## 10. Mini App

Mini App находится в `miniapp/`.

Запуск:

```bash
cd miniapp
npm install
npm run dev
```

Сборка:

```bash
npm run build
```

Mini App использует тот же backend. Для production должен быть настроен HTTPS URL и Telegram Web App configuration.

## 11. HTTP API

Backend поднимает HTTP-слой для Mini App.

Основные направления:

- поиск;
- сравнение;
- избранное;
- session state;
- статистика;
- диагностика.

Конкретная реализация endpoint'ов находится в `web.py`, а API-тесты — в `tests/test_web_api.py` и связанных сценариях.

## 12. Конфигурация

Основные переменные описаны в [.env.example](.env.example).

Особенно важны:

| Переменная | Назначение |
|---|---|
| `SHOPPER_BOT_TOKEN` | Telegram bot token |
| `SHOPPER_DEMO_MODE` | 1 = demo, 0 = реальные источники |
| `OPENROUTER_API_KEY` | OpenRouter |
| `MISTRAL_API_KEY` | Mistral |
| `SHOPPER_LOCAL_LLM` | использовать локальную LLM |
| `SHOPPER_EMBED_MODEL` | embedding model |
| `SHOPPER_RERANK_WEIGHTS` | веса hybrid ranking |
| `SHOPPER_TOP_CANDIDATES` | сколько кандидатов передавать дальше |
| `SHOPPER_PROXY` | proxy для web endpoint'ов |
| `SHOPPER_DB_PATH` | путь к SQLite |
| `SHOPPER_MINIAPP_URL` | HTTPS URL Mini App |

## 13. Тесты

```bash
pytest -q
```

В репозитории сейчас **165 тестов**. Они покрывают:

- adapters;
- parallel search;
- matcher;
- embeddings;
- reranking;
- review intelligence;
- LLM gateway;
- budgets;
- guardrails;
- web API;
- Telegram flow;
- cache;
- delivery failures;
- security scenarios.

## 14. Что здесь интересно с инженерной точки зрения

1. **Grounding** — модель не является базой данных.
2. **Hybrid search** — semantic + lexical + structural layers.
3. **Fallbacks** — проект остаётся работоспособным при отказе одного провайдера.
4. **One backend / two interfaces** — Telegram и Mini App используют общий state.
5. **Realistic failure handling** — антибот и отсутствие данных не маскируются выдуманным ответом.
6. **Security** — Mini App проверяет Telegram initData; есть отдельные security tests.

## 15. Ограничения

- web-endpoint'ы маркетплейсов могут меняться и блокировать автоматизированный трафик;
- для production storage логичнее PostgreSQL + Redis;
- качество Vision/LLM зависит от доступных моделей;
- публичный demo не использует приватные ключи.

## 16. AI-assisted development

AI активно использовался для черновой реализации, рутинных модулей, рефакторинга и тестовых идей.

Моя зона ответственности в проекте — постановка задачи, декомпозиция, архитектурные решения, интеграции, отладка, проверка поведения, тестирование и финальная сборка.

## Лицензия

MIT.
