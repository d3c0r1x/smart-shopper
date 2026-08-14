# Песочница для «Умного Шоппера» (ТЗ §4: изоляция окружения).
#
# Бот запускается в контейнере без доступа к хостовой сети (Docker bridge),
# секреты не копируются в образ — только через --env-file / -e при запуске.
# Сеть наружу нужна только целевым маркетплейсам (Ozon/Yandex/WB) и LLM-API;
# для строгого allowlist доменов используйте внешний файрвол/прокси поверх.
#
# Сборка:   docker build -t smart-shopper .
# Запуск:   docker run --rm -d -p 8081:8081 \
#             --env-file ../.env --name shopper smart-shopper
#   (ключи в корневом .env маппятся ботом: TG_TOKEN→SHOPPER_BOT_TOKEN и т.д.)
#
# Примечание: браузерные каналы (Playwright + прокси-пул Happ) требуют
# отдельного образа с браузером и xray; базовый образ запускает бота на
# HTTP-каналах (OzonAdapter/YandexMarketAdapter) или в демо-режиме.
FROM python:3.12-slim

# не-root пользователь (принцип наименьших привилегий)
RUN groupadd --system shopper && useradd --system --gid shopper --home-dir /app shopper

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=shopper:shopper . .

ENV PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHOPPER_DEMO_MODE=0

USER shopper

EXPOSE 8081

# healthcheck: /health отвечает от aiohttp (без curl в slim-образе — urllib)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=4)"

CMD ["python", "bot.py"]
