# Полный апгрейд бота: inline-навигация, локальные LLM на GPU, оптимизация — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Выкрутить идею «Умного Шоппера» на максимум: весь функционал — на inline-кнопках (главное меню, навигация, карточки, отзывы, сравнение, настройки, избранное), текстовые LLM-задачи — на локальной модели (Ollama + Qwen на GTX 1650) с фолбэком на облако, без потери качества и с рациональным расходом ресурсов ПК.

**Architecture:** (1) Вся навигация через `callback_data` — единый хендлер `menu:*`, каждый экран заканчивается «🏠 Главное меню»; (2) локальный LLM-провайдер `LocalProvider` (openai-совместимый endpoint Ollama `http://127.0.0.1:11434/v1`) встаёт в начало текстовых цепочек `llm/gateway.py` — бюджет OpenRouter/Mistral не тратится, при недоступности модели цепочка честно уходит на облако; (3) vision остаётся облачной (локальная vision на 4GB VRAM рискованна — Qwen2-VL не влезает с запасом).

**Tech Stack:** Python 3.11, aiogram v3, pydantic, aiosqlite, pytest (CapturingSession), Ollama (qwen2.5:3b-instruct-q4_K_M), NVIDIA GTX 1650 4GB + CUDA 13.0.

## Global Constraints

- Без новых Python-зависимостей: локальный провайдер использует уже стоящий `openai` SDK (openai-совместимый endpoint).
- Тексты — русский, официальный стиль.
- Обратная совместимость колбэков: `reviews:`, `review_item:`, `review_more:`, `back:`, `compare:`, `fav:`, `more:`, `set_profile:`, `set_clear` не ломаются.
- Кэш: поиск 30 мин, карточки 6 ч, отзывы 24 ч — без увеличения TTL.
- Локальная модель не тратит дневной бюджет (счётчик бюджета — только для облачных вызовов).
- Fallback: если Ollama недоступна или модель отвечает дольше `SHOPPER_LOCAL_TIMEOUT` (30 с) — провайдер пропускается, цепочка идёт на Mistral/OpenRouter.
### Task 1: Установка Ollama и локальной модели Qwen на GPU

**Files:**
- Create: `tools/ollama_setup.cmd` (одноразовый установочный скрипт)
- Test: ручная проверка `ollama run qwen2.5:3b-instruct-q4_K_M`

**Interfaces:**
- Produces: сервис Ollama на `http://127.0.0.1:11434/v1` (openai-совместимый), модель `qwen2.5:3b-instruct-q4_K_M`

- [ ] **Step 1: Проверить, что Ollama не установлена**

Run: `where ollama 2>nul || echo not-installed`
Expected: `not-installed`

- [ ] **Step 2: Скачать и установить Ollama (в %LOCALAPPDATA%, без админа)**

```bash
curl -fsSL -o "$LOCALAPPDATA/OllamaSetup.exe" https://ollama.com/download/OllamaSetup.exe
"$LOCALAPPDATA/OllamaSetup.exe" /SILENT
for i in $(seq 1 24); do sleep 5; curl -s -m 3 http://127.0.0.1:11434/api/version && break; done
```
Expected: `{"version":"0.x.x"}`

- [ ] **Step 3: Скачать модель**

```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
```
Expected: `success`; `nvidia-smi` — VRAM ~2.5 GB занято моделью.

- [ ] **Step 4: Проверить инференс**

```bash
ollama run qwen2.5:3b-instruct-q4_K_M "Ответь одним словом: 2+2?"
```
Expected: `4`.

- [ ] **Step 5: Создать `tools/ollama_setup.cmd`**

```bat
@echo off
rem Установка Ollama + локальной модели для «Умного Шоппера».
rem Требуется: NVIDIA GPU с CUDA (GTX 1650+), ~3 GB VRAM, 3 GB на диске.
setlocal
curl -fsSL -o "%LOCALAPPDATA%\OllamaSetup.exe" https://ollama.com/download/OllamaSetup.exe
"%LOCALAPPDATA%\OllamaSetup.exe" /SILENT
for /l %%i in (1,1,24) do (
  curl -s -m 3 http://127.0.0.1:11434/api/version >nul 2>&1 && goto :up
  timeout /t 5 /nobreak >nul
)
echo [ERR] Ollama не поднялась за 120 c
exit /b 1
:up
ollama pull qwen2.5:3b-instruct-q4_K_M
echo OK: ollama pull завершён
```

- [ ] **Step 6: Коммит**

```bash
git add tools/ollama_setup.cmd
git commit -m "feat: установочный скрипт Ollama + локальная Qwen на GPU"
```


### Task 2: Главное inline-меню и навигация `menu:*`

**Files:**
- Modify: `keyboards.py` (добавить `main_menu_keyboard`, `home_keyboard`)
- Modify: `bot.py` (хендлер `/start`, колбэки `menu:*`, `_send_menu`, `_set_mode_cb`, `_show_favorites_cb`, `_show_settings_cb`, `_run_diag_cb`)
- Test: `tests/test_bot_flow.py` (новый тест `test_inline_main_menu`)

**Interfaces:**
- Consumes: `SessionState`, `db.get_session/save_session`, `llm.budget_info()`
- Produces: callback-контракт `menu:<screen>` где screen ∈ `{main, search, photo, compare, favorites, settings, help, budget, diag}`

- [ ] **Step 1: Написать падающий тест**

```python
def test_inline_main_menu(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "menu.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("/start", 1, 1))
            await DP.feed_update(bot, _cb_update("menu:favorites", 2, 2))
            await DP.feed_update(bot, _cb_update("menu:main", 3, 3))
        finally:
            await botmod.db.close()

    _run(run())
    texts = _send_texts(session) + _edit_texts(session)
    assert any("Главное меню" in t for t in texts)
    assert any("Избранное" in t for t in texts)
    assert any("Главное меню" in t for t in _button_texts(session))
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `pytest tests/test_bot_flow.py::test_inline_main_menu -v`
Expected: FAIL — колбэк `menu:favorites` не обрабатывается.

- [ ] **Step 3: Клавиатуры в `keyboards.py`**

```python
def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню — всё на inline-кнопках."""
    kb = [
        [InlineKeyboardButton(text="🔎 Умный поиск", callback_data="menu:search"),
         InlineKeyboardButton(text="📸 Найти по фото", callback_data="menu:photo")],
        [InlineKeyboardButton(text="⚖️ Сравнить цены", callback_data="menu:compare"),
         InlineKeyboardButton(text="⭐ Избранное", callback_data="menu:favorites")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
         InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help")],
        [InlineKeyboardButton(text="🔋 Бюджет", callback_data="menu:budget"),
         InlineKeyboardButton(text="🩺 Диагностика", callback_data="menu:diag")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def home_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «🏠 Главное меню» на всех экранах."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")]
    ])
```

- [ ] **Step 4: Хендлеры в `bot.py`**

```python
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await _send_menu(message)


async def _send_menu(message: Message, *, edit: bool = False) -> None:
    """Главное меню: приветствие + inline-кнопки."""
    info = await llm.budget_info()
    text = (
        "🛍 <b>«Умный Шоппер»</b> — ИИ-ассистент покупок на Ozon, "
        "Яндекс Маркете и Wildberries.\n\n"
        "Выберите действие 👇\n\n"
        f"🔋 Бюджет сегодня: {info['used']}/{info['limit']} запросов"
    )
    kb = main_menu_keyboard()
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(lambda c: c.data.startswith("menu:"))
async def cb_menu(call: CallbackQuery) -> None:
    screen = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    if screen == "main":
        await call.answer()
        await _send_menu(call.message, edit=True)
        return
    if screen == "search":
        await call.answer()
        await _set_mode_cb(call, "search_wait", "🔎 Что ищете? Опишите словами.")
        return
    if screen == "photo":
        await call.answer()
        await _set_mode_cb(call, "photo_wait", "📸 Отправьте фотографию товара.")
        return
    if screen == "compare":
        await call.answer()
        await _set_mode_cb(call, "compare_wait",
                           "⚖️ Что сравнить? Напишите название товара.")
        return
    if screen == "favorites":
        await call.answer()
        await _show_favorites_cb(call, user_id)
        return
    if screen == "settings":
        await call.answer()
        await _show_settings_cb(call)
        return
    if screen == "help":
        await call.answer()
        text = (
            "❓ <b>Помощь</b>\n\n"
            "• 🔎 <b>Умный поиск</b> — «чёрная маска для сна с пространством "
            "для ресниц»: проверю требования по отзывам;\n"
            "• 📸 <b>По фото</b> — сфотографируйте товар, найду похожие;\n"
            "• ⚖️ <b>Сравнить цены</b> — тот же товар на трёх площадках;\n"
            "• ⭐ <b>Избранное</b> — сохранённые товары;\n"
            "• ⚙️ <b>Настройки</b> — площадки, профиль моделей;\n"
            "• На карточке: 📝 отзывы, ⚖️ сравнение, ⭐ избранное, "
            "🔁 похожие, 🛒 ссылка на маркетплейс."
        )
        await call.message.edit_text(text, reply_markup=home_keyboard())
        return
    if screen == "budget":
        await call.answer()
        info = await llm.budget_info()
        await call.message.edit_text(_budget_text(info), reply_markup=home_keyboard())
        return
    if screen == "diag":
        await call.answer()
        await _run_diag_cb(call)
        return
```

- [ ] **Step 5: Вспомогательные функции**

```python
async def _set_mode_cb(call: CallbackQuery, mode: str, reply: str) -> None:
    state = await db.get_session(call.from_user.id)
    state.mode = mode
    await db.save_session(call.from_user.id, state)
    await call.message.edit_text(reply, reply_markup=home_keyboard())


async def _show_favorites_cb(call: CallbackQuery, user_id: int) -> None:
    favorites = await db.list_favorites(user_id)
    if not favorites:
        await call.message.edit_text(
            "⭐ <b>Избранное</b>\n\nПока пусто. Нажимайте ⭐ на карточках товаров.",
            reply_markup=home_keyboard())
        return
    lines = ["⭐ <b>Избранное</b>\n"]
    for p in favorites[:4]:
        lines.append(f"• {p.title[:40]} — {_rub(p.price)}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"▶️ Смотреть все ({len(favorites)})",
                              callback_data="fav_list:0")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=kb)


async def _show_settings_cb(call: CallbackQuery) -> None:
    info = await llm.budget_info()
    profile = await db.get_setting("profile", config.LLM_PROFILE)
    state = await db.get_session(call.from_user.id)
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"• Профиль моделей: <b>{profile}</b>\n"
        f"• Площадки: <b>{state.default_market}</b>\n"
        f"• Бюджет: {info['remaining']} из {info['limit']} запросов\n\n"
        "Профиль «быстро» — короткая цепочка, «качественно» — глубокий "
        "анализ отзывов."
    )
    await call.message.edit_text(text, reply_markup=settings_keyboard(profile=profile))


async def _run_diag_cb(call: CallbackQuery) -> None:
    lines = ["🩺 <b>Диагностика</b>\n"]
    lines.append(f"Режим данных: <b>{'демо' if config.DEMO_MODE else 'реальный'}</b>")
    for adapter in adapters:
        try:
            found = await adapter.search("маска для сна", limit=3)
            lines.append(f"• {adapter.name}: {len(found)} товаров")
        except Exception as exc:
            lines.append(f"• {adapter.name}: ошибка {exc}")
    info = await llm.budget_info()
    lines.append(f"• LLM: {info.get('provider', '?')} · профиль {info['profile']}")
    await call.message.edit_text("\n".join(lines), reply_markup=home_keyboard())
```

- [ ] **Step 6: Прогнать тесты**

Run: `pytest tests/test_bot_flow.py -q`
Expected: PASS (новый + старые; при необходимости поправить тесты под inline-вывод).

- [ ] **Step 7: Коммит**

```bash
git add keyboards.py bot.py tests/test_bot_flow.py
git commit -m "feat: главное inline-меню и навигация menu:*"
```
- Ollama ставится в `%LOCALAPPDATA%\Ollama` (без прав администратора), модель — `qwen2.5:3b-instruct-q4_K_M` (~2.5 GB VRAM, влезает в 4 GB с запасом под KV-cache).

---
### Task 3: Результаты поиска и карточки — inline-навигация

**Files:**
- Modify: `keyboards.py` (`product_card_keyboard` — добавить «🏠» и «📋 Характеристики»)
- Modify: `bot.py` (`_send_outcome` — компактный вывод + меню после списка)
- Test: `tests/test_bot_flow.py`

**Interfaces:**
- Consumes: `Product`, `product_card_keyboard(p, favored)`, `orch._load_card`
- Produces: карточка с кнопками (отзывы, сравнение, избранное, похожие, ссылка, «🏠»)

- [ ] **Step 1: Улучшить `product_card_keyboard`** — добавить кнопки «📋 Характеристики» (если есть traits) и «🏠 Главное меню»:

```python
def product_card_keyboard(p: Product, *, favored: bool = False) -> InlineKeyboardMarkup:
    fav = "⭐ Убрать" if favored else "⭐ В избранное"
    kb = [
        [InlineKeyboardButton(text=f"📝 Отзывы ({p.reviews_count})",
                              callback_data=f"reviews:{p.marketplace}:{p.ext_id}"),
         InlineKeyboardButton(text="⚖️ Сравнить",
                              callback_data=f"compare:{p.marketplace}:{p.ext_id}")],
        [InlineKeyboardButton(text=fav,
                              callback_data=f"fav:{p.marketplace}:{p.ext_id}"),
         InlineKeyboardButton(text="🔁 Похожие",
                              callback_data=f"more:{p.ext_id}")],
        [InlineKeyboardButton(text="🛒 Открыть на маркетплейсе", url=p.url)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
```

- [ ] **Step 2: `_send_outcome` — меню после результатов** (добавить после цикла карточек):

```python
    await message.answer("Показаны лучшие варианты. Меню ниже 👇",
                         reply_markup=main_menu_keyboard())
```

- [ ] **Step 3: Тест** — в `tests/test_bot_flow.py`:

```python
def test_search_shows_menu_button(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "search_menu.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("маска для сна", 1, 1))
        finally:
            await botmod.db.close()

    _run(run())
    assert any("Главное меню" in t for t in _button_texts(session))
```

- [ ] **Step 4: Прогнать тесты** — `pytest tests/test_bot_flow.py -q` → PASS
- [ ] **Step 5: Коммит** — `git commit -m "feat: inline-навигация в карточках и результатах"`


### Task 4: Отзывы — постраничная inline-навигация с «🏠»

**Files:**
- Modify: `keyboards.py` (`reviews_keyboard` — добавить «🏠»)
- Test: `tests/test_bot_flow.py` (`test_refinement_and_reviews_callback` уже покрывает)

- [ ] **Step 1: `reviews_keyboard` — добавить строку «🏠 Главное меню»** (после «← К товару»):

```python
    kb = [items]
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="← К товару",
                                    callback_data=f"back:{market}:{ext_id}")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)
```

- [ ] **Step 2: Прогнать тест** — `pytest tests/test_bot_flow.py::test_refinement_and_reviews_callback -q` → PASS
- [ ] **Step 3: Коммит** — `git commit -m "feat: отзывы — inline-навигация с кнопкой меню"`


### Task 5: Сравнение цен и настройки — inline

**Files:**
- Modify: `bot.py` (`_run_compare` — меню после результата)
- Modify: `keyboards.py` (`settings_keyboard` — выбор площадок)
- Modify: `bot.py` (новый `cb_set_market`)

**Interfaces:**
- Consumes: `SessionState.default_market` (both|ozon|yandex|wb)
- Produces: колбэк `set_market:<value>`; `settings_keyboard(*, profile, market)`

- [ ] **Step 1: `_run_compare`** — после результата добавить:

```python
        kb = compare_keyboard(rows[0].ozon_url, rows[0].yandex_url, rows[0].wb_url)
        await message.answer(_compare_text(rows), reply_markup=kb)
        await message.answer("🏠 Меню:", reply_markup=main_menu_keyboard())
```

- [ ] **Step 2: `settings_keyboard` — площадки** (сигнатура `def settings_keyboard(*, profile: str, market: str = "both")`):

```python
def settings_keyboard(*, profile: str, market: str = "both") -> InlineKeyboardMarkup:
    fast = "✅ " if profile == "fast" else ""
    quality = "✅ " if profile == "quality" else ""

    def m_btn(val: str, label: str) -> InlineKeyboardButton:
        mark = "✅ " if market == val else ""
        return InlineKeyboardButton(text=f"{mark}{label}",
                                    callback_data=f"set_market:{val}")

    kb = [
        [InlineKeyboardButton(text=f"{fast}Быстро", callback_data="set_profile:fast"),
         InlineKeyboardButton(text=f"{quality}Качественно",
                              callback_data="set_profile:quality")],
        [m_btn("both", "Все площадки")],
        [m_btn("ozon", "Ozon"), m_btn("yandex", "Яндекс"),
         m_btn("wb", "Wildberries")],
        [InlineKeyboardButton(text="🧹 Очистить контекст", callback_data="set_clear")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
```

- [ ] **Step 3: Хендлер `set_market`** (в `bot.py`):

```python
@router.callback_query(lambda c: c.data.startswith("set_market:"))
async def cb_set_market(call: CallbackQuery) -> None:
    value = call.data.split(":", 1)[1]
    state = await db.get_session(call.from_user.id)
    state.default_market = "both" if value == "both" else value
    await db.save_session(call.from_user.id, state)
    await call.answer("Площадки обновлены")
    await _show_settings_cb(call)
```

- [ ] **Step 4: `_show_settings_cb`** — передавать market:

```python
    await call.message.edit_text(text, reply_markup=settings_keyboard(
        profile=profile, market=markets))
```

- [ ] **Step 5: Тест**

```python
def test_settings_market_switch(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "settings.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _cb_update("menu:settings", 1, 1))
            await DP.feed_update(bot, _cb_update("set_market:wb", 2, 2))
        finally:
            await botmod.db.close()

    _run(run())
    joined = "\n".join(_edit_texts(session))
    assert "wb" in joined or "Wildberries" in joined
```

- [ ] **Step 6: Прогнать тесты** — `pytest tests/test_bot_flow.py -q` → PASS
- [ ] **Step 7: Коммит** — `git commit -m "feat: настройки и сравнение — inline"`


### Task 6: Локальный LLM-провайдер (Ollama) в `llm/providers.py`

**Files:**
- Modify: `llm/providers.py` (новый `LocalProvider`)
- Modify: `config.py` (переменные `LOCAL_LLM_*`)
- Test: `tests/test_gateway.py` (новый тест на роутинг `local:`)

**Interfaces:**
- Consumes: openai SDK, `OpenAICompatProvider`
- Produces: класс `LocalProvider(OpenAICompatProvider)` с `name == "local"`; цепочки получают модели с префиксом `local:qwen2.5:3b-instruct-q4_K_M`

- [ ] **Step 1: Конфиг в `config.py`**

```python
# ── Локальная LLM (Ollama на GPU) ─────────────────────────────────
# Текстовые задачи (constraints/rank/arbiter/review/freeform) можно
# выполнять локально: бесплатно, без дневного лимита и приватно.
LOCAL_LLM = os.getenv("SHOPPER_LOCAL_LLM", "1") == "1"
LOCAL_BASE_URL = os.getenv("SHOPPER_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
LOCAL_MODEL = os.getenv("SHOPPER_LOCAL_MODEL", "qwen2.5:3b-instruct-q4_K_M")
LOCAL_TIMEOUT = float(os.getenv("SHOPPER_LOCAL_TIMEOUT", "30"))
```

- [ ] **Step 2: Провайдер в `llm/providers.py`**

```python
class LocalProvider(OpenAICompatProvider):
    """Локальная модель через Ollama (openai-совместимый endpoint).

    Работает на GPU/CPU устройства: qwen2.5:3b-instruct-q4_K_M (~2.5 GB
    VRAM). НЕ тратит дневной бюджет (gateway списывает бюджет только для
    облачных провайдеров). Если сервис недоступен — модель в цепочке
    пропускается, цепочка уходит на Mistral/OpenRouter.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        super().__init__("ollama", base_url, timeout, name="local")
```

- [ ] **Step 3: Подключить в `llm/gateway.py`**

Импорт: `from llm.providers import LocalProvider, ...`

В `__init__` (после облачных провайдеров, до `self._provider`):

```python
        self._providers: list = []
        if config.LOCAL_LLM:
            self._providers.append(LocalProvider(config.LOCAL_BASE_URL,
                                                 config.LOCAL_TIMEOUT))
        if mistral_api_key:
            self._providers.append(MistralProvider(mistral_api_key, timeout))
        if api_key:
            self._providers.append(OpenRouterProvider(api_key, timeout))
        self._provider = self._providers[0] if self._providers else None
```

Цепочки — локальная модель первой (текстовые задачи):

```python
TEXT_CHAINS = {
    "fast": [
        "local:qwen2.5:3b-instruct-q4_K_M",
        "mistral:mistral-small-latest",
        "openrouter:openrouter/free",
    ],
    "quality": [
        "local:qwen2.5:3b-instruct-q4_K_M",
        "mistral:mistral-large-latest",
        "mistral:mistral-medium-latest",
        "openrouter:openrouter/free",
    ],
}
```

Бюджет: в `structured()` списание бюджета делается ТОЛЬКО для облачных моделей — локальная идёт бесплатно. Для этого в начале цикла:

```python
        for raw in chain:
            if raw.startswith("local:"):
                provider, model = self._resolve_provider(raw)
                if provider is None:
                    continue
                try:
                    return await asyncio.wait_for(
                        provider.complete(model=model, kind=kind, prompt=prompt,
                                          schema=schema, images=images),
                        timeout=config.LOCAL_TIMEOUT)
                except Exception as exc:
                    logger.warning("Локальная модель недоступна (%s) — "
                                   "пробую облако", exc)
                    continue
            # далее — облачная цепочка с бюджетом и троттлингом (как сейчас)
            if time.monotonic() > deadline:
                break
            ...
```

И сдвинуть `budget_increment` так, чтобы он вызывался только для облачного вызова (перенести внутрь облачной ветки перед первым облачным `provider.complete`).

- [ ] **Step 4: Тест роутинга `local:` в `tests/test_gateway.py`**

```python
def test_local_prefix_resolves_to_local_provider():
    g = llmmod.LLMGateway.__new__(llmmod.LLMGateway)
    g._profile = "fast"
    g._providers = [FakeLocal(), FakeCloud()]
    provider, model = g._resolve_provider("local:qwen2.5:3b")
    assert getattr(provider, "name", "") == "local"
    assert model == "qwen2.5:3b"
```

Где `FakeLocal`/`FakeCloud` — объекты с атрибутом `name` ("local"/"mistral").

- [ ] **Step 5: Прогнать тесты** — `pytest tests/ -q` → PASS (все старые + новые; если mock-провайдер в тестах подменяется напрямую — не затрагивается)
- [ ] **Step 6: Коммит** — `git commit -m "feat: локальный LLM-провайдер Ollama (бесплатные текстовые задачи)"`


### Task 7: Оптимизация ресурсов и структуры

**Files:**
- Modify: `middlewares.py` (троттлинг колбэков)
- Modify: `bot.py` (хелперы, минимизация дублирования)

- [ ] **Step 1: Троттлинг колбэков** — расширить `ThrottlingMiddleware` (уже работает на `Message`; убедиться, что `CallbackQuery` тоже покрыт — у `CallbackQuery` есть `from_user`):

```python
class ThrottlingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user is not None:
            now = time.monotonic()
            last = _last_seen.get(user.id, 0.0)
            if now - last < config.THROTTLE_MIN_INTERVAL:
                return None
            _last_seen[user.id] = now
        return await handler(event, data)
```

Проверить, что `DP.callback_query.middleware(ThrottlingMiddleware())` зарегистрирован в `main()`.

- [ ] **Step 2: Не дублировать LLM-вызовы** — проверить, что `_run_compare` использует `search_candidates` (кэш 30 мин) и что `more:` повторно использует кэш `_execute_search` (уже так). Добавить в `_execute_search` ранний выход: если `constraints` совпадают с последним поиском и кэш свежий — не пересчитывать (проверить текущую реализацию, при необходимости добавить кэш по `(query, tuple(must_have))`).

- [ ] **Step 3: Прогнать тесты** — `pytest tests/ -q` → PASS
- [ ] **Step 4: Коммит** — `git commit -m "perf: троттлинг колбэков и кэш поиска"`


### Task 8: Финальная интеграция, живая проверка, пуш

- [ ] **Step 1: Полный прогон** — `pytest tests/ -q` → все зелёные
- [ ] **Step 2: Перезапуск бота** — `run_bot12.cmd` (реальный режим, LLM: local → Mistral)
- [ ] **Step 3: Живая проверка через туннель**: `/start` → inline-меню; `menu:search` → запрос; карточка с «🏠»; `reviews:`; `menu:settings` → `set_market:wb`; `menu:diag` — в выводе LLM: `local` (или `Mistral` при недоступности Ollama)
- [ ] **Step 4: Коммит** — `git commit -m "feat: полный inline-бот + локальная LLM"`
- [ ] **Step 5: Пуш** — `git push`
