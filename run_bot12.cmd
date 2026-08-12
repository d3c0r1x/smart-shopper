@echo off
rem Launch script for Smart Shopper (Project 12).
rem Reads TG_TOKEN from the root .env, sets SHOPPER_BOT_TOKEN, runs the bot.
cd /d "%~dp0"

for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do (
    if "%%a"=="TG_TOKEN" set "SHOPPER_BOT_TOKEN=%%b"
    if "%%a"=="OPEN_ROUTER_KEY" set "OPENROUTER_API_KEY=%%b"
    if "%%a"=="OZON_KEY" set "OZON_API_KEY=%%b"
    if "%%a"=="YANDEX_MARKET_KEY" set "YM_API_KEY=%%b"
)
if not defined SHOPPER_BOT_TOKEN (
    echo [ERROR] TG_TOKEN not found in ..\.env
    pause
    exit /b 1
)

rem 0 = real marketplace endpoints (needs internet + proxy) | 1 = demo mode (offline)
set "SHOPPER_DEMO_MODE=1"
rem Без ключа OpenRouter работает mock-провайдер (демо-сценарии на эвристиках).
rem Чтобы включить реальные LLM-модели, задайте OPENROUTER_API_KEY в .env
rem и установите SHOPPER_DEMO_MODE=0.
set "PYTHONIOENCODING=utf-8"

..\.venv\Scripts\python.exe -u bot.py
