# Разработка и запуск — Smart Shopper

Краткий быстрый старт — в README, здесь полная инструкция.

## 1. Установка

```bash
pip install -r requirements.txt
cp .env.example .env      # и заполнить (все секреты — только здесь)
python bot.py             # бот; HTTP-API для Mini App поднимается вместе с ним
```

На Windows: `start.ps1` (или `start.bat`) сам читает ключи из корневого `.env`
портфолио и запускает бота из `.venv`.

Ключи из корневого `.env` маппятся в переменные бота автоматически:
`TG_TOKEN` → `SHOPPER_BOT_TOKEN`, `OPEN_ROUTER_KEY` → `OPENROUTER_API_KEY`,
`OZON_KEY` → `OZON_API_KEY`, `YANDEX_MARKET_KEY` → `YM_API_KEY`,
`MISTRAL_KEY` → `MISTRAL_API_KEY`.

## 2. Режимы работы

| Режим | Как включить | Что происходит |
|---|---|---|
| Демо (оффлайн) | `SHOPPER_DEMO_MODE=1` | маркетплейсы — встроенный каталог с реалистичными товарами и отзывами |
| Без ключа LLM | не задавать `OPENROUTER_API_KEY`/`MISTRAL_API_KEY` | LLM-слой заменён детерминированным mock-провайдером с теми же JSON-схемами |
| Реальный | `SHOPPER_DEMO_MODE=0` + прокси | публичные JSON-эндпоинты маркетплейсов |
| Локальная LLM | `SHOPPER_LOCAL_LLM=1` | механические задачи (ранжирование, арбитр, свободные ответы) идут на Ollama |

В реальном режиме демо-каталог не используется никогда: пустой результат
остаётся пустым.

## 3. Прокси и фиксация эндпоинтов

```bash
python tools/capture_endpoints.py --market ozon   --query "маска для сна"
python tools/capture_endpoints.py --market yandex --query "маска для сна"
# --proxy http://user:pass@host:port — при необходимости
```

Инструмент сохраняет URL, заголовки и cookies каждого JSON-эндпоинта в
`captured/{market}.json`. Адаптеры используют этот файл автоматически; без
него работают по документированным семействам composer-api (Ozon web+mobile)
и web-версии с JSON-LD (Яндекс).

Пул прокси (`proxy_pool.py`, ~300 vless-серверов подписки Happ):

```bash
python tools/happ_proxy.py update      # кэш списка серверов (happ_servers.json, в .gitignore)
python tools/happ_proxy.py up 6        # поднять 6 инстансов с health-check
```

Переменные: `SHOPPER_SUBSCRIPTION_URL` (URL подписки), `SHOPPER_PROXY_POOL=1`
(по умолчанию), `SHOPPER_POOL_SIZE=6`, `SHOPPER_PROXY` (фиксированный прокси),
`SHOPPER_PROXY_TRIES=3`.

**Важная деталь Ozon:** адаптеру нельзя задавать init-скрипт
`navigator.webdriver=undefined` — Ozon детектирует подмену и не решает Antibot
Challenge (проверено: с init-скриптом челлендж не решается, без него — за ~5 с).

## 4. Локальная LLM (Ollama)

Установка на Windows без прав администратора:

```powershell
curl -fsSL -o "$env:LOCALAPPDATA\OllamaSetup.exe" https://ollama.com/download/OllamaSetup.exe
& "$env:LOCALAPPDATA\OllamaSetup.exe" /SILENT
ollama pull qwen2.5:3b-instruct-q4_K_M   # ~1.9 ГБ, от 4 ГБ VRAM
ollama pull bge-m3                       # эмбеддинги для семантического слоя
```

Vision остаётся облачной: локальная vision-модель в 4 ГБ VRAM не помещается
с запасом. Отключить локальную LLM: `SHOPPER_LOCAL_LLM=0`. Переменные:
`SHOPPER_LOCAL_BASE_URL`, `SHOPPER_LOCAL_MODEL`, `SHOPPER_LOCAL_TIMEOUT`,
`SHOPPER_SEMANTIC_ENABLED`.

## 5. Mini App

```bash
cd miniapp
npm install
npm run dev        # http://localhost:5173 (вне Telegram — демо-тема)
npm run build      # production-сборка в miniapp/dist
```

Развёрнутая версия: https://d3c0r1x.github.io/smart-shopper/ (GitHub Pages,
ветка `gh-pages`). В Telegram открывается web_app-кнопкой — она появляется в
постоянном меню бота и у поля ввода (`setChatMenuButton`, ставится при старте).
URL настраивается `SHOPPER_MINIAPP_URL`.

Для подключения к бэкенду задайте при сборке `VITE_API_URL` (контракт —
`miniapp/src/api.ts`).

## 6. HTTP-API

Работает вместе с ботом (`http://127.0.0.1:8081`, порт — `SHOPPER_API_PORT`)
или автономно, без поллинга Telegram:

```bash
python web.py        # http://0.0.0.0:8081, CORS включён
```

| Метод | Путь | Параметры |
|---|---|---|
| GET | `/api/search` | `q`, `markets=ozon,yandex`, `initData` |
| GET | `/api/reviews` | `marketplace`, `ext_id`, `initData` |
| GET | `/api/compare` | `q`, `initData` |
| GET | `/api/budget` | `initData` |
| GET | `/health` | — |
| GET | `/api/stats` | — (аптайм, счётчики, p95) |

Аутентификация: подпись Telegram `initData` (HMAC-SHA256). Для локальной
разработки вне Telegram — `SHOPPER_API_ALLOW_ANON=1` и `user_id` в query; для
продакшена — `SHOPPER_API_TOKEN` (Mini App шлёт его в `X-API-Token`).

```bash
curl "http://127.0.0.1:8081/api/search?q=маска%20для%20сна&user_id=1"
curl "http://127.0.0.1:8081/api/compare?q=маска%20для%20сна&user_id=1"
```

Публичный HTTPS для браузерного Mini App — туннелем без регистрации:

```bash
cloudflared tunnel --url http://127.0.0.1:8081
VITE_API_URL=https://<random>.trycloudflare.com npm run build   # в miniapp/
```

## 7. Docker

```bash
docker build -t smart-shopper .
docker run --rm -d -p 8081:8081 --env-file ../.env --name shopper smart-shopper
```

Секреты не попадают в образ (`.dockerignore`), контейнер не работает под root,
healthcheck — через `/health`.

## 8. Тесты

```bash
pytest -q
```

165 тестов: контракт демо-адаптеров, реестр моделей и fallback-цепочка (все
модели упали → mock, схема соблюдена), дневной бюджет и троттлинг, сценарии 1
и 2 end-to-end, уточнение «подешевле», matcher (EAN, нечёткие названия,
арбитр), гибридный реранк и структурный фильтр, proxy pool, guardrails
(OWASP LLM), robots, телеметрия, кэш и TTL, интеграционные — через **настоящий
`Dispatcher`** с перехватом Bot API, HTTP-API (подпись initData, эндпоинты,
анонимный `user_id`, токен-аутентификация).

Матрица метрик (Precision@K, p95, uptime, coverage, freshness) и последний
прогон — [EVAL.md](EVAL.md), скрипт — `tools/eval_precision.py`.

## 9. Ротация моделей

Бесплатные модели OpenRouter регулярно снимаются с публикации. Реестр в
`llm/gateway.py` калибруется живой пробой: рабочие модели идут первыми в
цепочках, недоступные для аккаунта (guardrail-политика, 404) — в хвосте;
роутер `openrouter/free` всегда последний. Ответы запрашиваются через
structured outputs (`response_format: json_schema`); модели без поддержки
получают тот же промпт без `response_format`, а невалидный ответ отбракует
pydantic-схема и переключит цепочку дальше.
