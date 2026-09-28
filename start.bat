@echo off
rem Launch script for Smart Shopper (Project 12).
rem Reads keys from the root .env, sets SHOPPER_* vars, runs the bot.
cd /d "%~dp0"

for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do (
    if "%%a"=="TG_TOKEN" set "SHOPPER_BOT_TOKEN=%%b"
    if "%%a"=="OPEN_ROUTER_KEY" set "OPENROUTER_API_KEY=%%b"
    if "%%a"=="OZON_KEY" set "OZON_API_KEY=%%b"
    if "%%a"=="YANDEX_MARKET_KEY" set "YM_API_KEY=%%b"
    if "%%a"=="MISTRAL_KEY" set "MISTRAL_API_KEY=%%b"
    if "%%a"=="SHOPPER_PROXY" set "SHOPPER_PROXY=%%b"
    if "%%a"=="SHOPPER_SUBSCRIPTION_URL" set "SHOPPER_SUBSCRIPTION_URL=%%b"
)
if not defined SHOPPER_BOT_TOKEN (
    echo [ERROR] TG_TOKEN not found in ..\.env
    pause
    exit /b 1
)

rem ── Локальная LLM (Ollama, портативная) ────────────────────────
rem Текстовые LLM-задачи бесплатно выполняются локально на GPU; если
rem Ollama не запущена — цепочка честно уходит на Mistral/OpenRouter.
if not exist "%LOCALAPPDATA%\OllamaPortable\ollama.exe" goto :no_ollama
set "OLLAMA_MODELS=%LOCALAPPDATA%\Ollama\models"
tasklist /fi "imagename eq ollama.exe" 2>nul | findstr /i "ollama.exe" >nul
if %errorlevel%==0 goto :no_ollama
echo [i] Starting Ollama (local LLM)...
start "" /b "%LOCALAPPDATA%\OllamaPortable\ollama.exe" serve
:no_ollama

rem 0 = real marketplace endpoints (по умолчанию) | 1 = demo mode (offline)
rem Демо-каталог в реальном режиме не используется: пустой результат (блок
rem антибота) остаётся пустым, без подмены на выдуманные товары.
set "SHOPPER_DEMO_MODE=0"
set "PYTHONIOENCODING=utf-8"

..\.venv\Scripts\python.exe -u bot.py
