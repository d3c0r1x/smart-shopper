"""Интеграционные тесты «Умного Шоппера» через НАСТОЯЩИЙ Dispatcher.

Апдейты Telegram прогоняются через router/хендлеры/БД бота (тот же код, что
в bot.py); исходящие вызовы Bot API перехватываются CapturingSession — сеть
не нужна. Сценарии: /start → свободный поиск → уточнение → кликабельные
отзывы → избранное → сравнение цен.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime


os.environ["SHOPPER_DB_PATH"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_test_shopper.db")
os.environ["SHOPPER_DEMO_MODE"] = "1"

import bot as botmod  # noqa: E402  (после установки env)
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.methods import TelegramMethod  # noqa: E402
from aiogram.types import (CallbackQuery, Chat, InlineKeyboardMarkup,  # noqa: E402
                           Message, Update, User)
from core.orchestrator import Orchestrator  # noqa: E402
from middlewares import ThrottlingMiddleware  # noqa: E402
from storage.db import Database  # noqa: E402

USER_ID = 777
FAKE_TOKEN = "12345:test-only-no-network"


class CapturingSession(BaseSession):
    """Перехватывает исходящие вызовы Bot API в self.calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    async def make_request(self, bot: Bot, method: TelegramMethod,
                           timeout: int | None = None):
        data = method.model_dump(exclude_none=True)
        self.calls.append({"method": type(method).__name__, "data": data})
        return _fake_result(method, data, bot)

    async def close(self) -> None:
        return None

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536,
                             raise_for_status=True):
        yield b""


def _fake_result(method: TelegramMethod, data: dict, bot: Bot = None):
    name = type(method).__name__
    if name in ("SendMessage", "EditMessageText"):
        markup = None
        if data.get("reply_markup"):
            rm = data["reply_markup"]
            if "inline_keyboard" in rm:
                markup = InlineKeyboardMarkup(
                    inline_keyboard=rm["inline_keyboard"])
            # reply-клавиатура (меню) — просто игнорируем
        msg = Message(
            message_id=1, date=datetime.now(),
            chat=Chat(id=USER_ID, type="private"), text=data.get("text", ""),
            reply_markup=markup,
        )
        return msg.as_(bot) if bot is not None else msg
    if name in ("AnswerCallbackQuery", "DeleteMessage", "GetMe"):
        return True
    return True


def _user() -> User:
    return User(id=USER_ID, is_bot=False, first_name="Live")


def _chat() -> Chat:
    return Chat(id=USER_ID, type="private")


def _msg_update(text: str, mid: int, uid: int) -> Update:
    return Update(update_id=uid, message=Message(
        message_id=mid, date=datetime.now(), chat=_chat(), from_user=_user(),
        text=text))


def _cb_update(data: str, mid: int, uid: int) -> Update:
    return Update(update_id=uid, callback_query=CallbackQuery(
        id=f"cb{uid}", from_user=_user(), chat_instance="ci",
        message=Message(message_id=mid, date=datetime.now(), chat=_chat(),
                        from_user=_user(), text="карточка"),
        data=data))


DP = Dispatcher()
DP.include_router(botmod.router)
DP.message.middleware(ThrottlingMiddleware())


def _make_bot(session: CapturingSession) -> Bot:
    return Bot(token=FAKE_TOKEN,
               default=DefaultBotProperties(parse_mode=ParseMode.HTML),
               session=session)


def _send_texts(session: CapturingSession) -> list[str]:
    return [c["data"].get("text", "") for c in session.calls
            if c["method"] == "SendMessage"]


def _edit_texts(session: CapturingSession) -> list[str]:
    return [c["data"].get("text", "") for c in session.calls
            if c["method"] == "EditMessageText"]


def _markups(session: CapturingSession):
    return [InlineKeyboardMarkup(inline_keyboard=c["data"]["reply_markup"]["inline_keyboard"])
            for c in session.calls
            if c["method"] == "SendMessage"
            and "inline_keyboard" in (c["data"].get("reply_markup") or {})]


def _button_texts(session: CapturingSession) -> list[str]:
    out = []
    for m in _markups(session):
        for row in m.inline_keyboard:
            for btn in row:
                out.append(btn.text)
    return out


def _reset(path: str) -> None:
    botmod.db = Database(path)
    botmod.orch = Orchestrator(botmod.db, botmod.llm, botmod.adapters)
    botmod.llm._db = botmod.db  # бюджет/кэш идут в свежую БД
    botmod.config.THROTTLE_MIN_INTERVAL = 0.0


def _run(coro):
    return asyncio.run(coro)


def test_inline_menu_navigation(tmp_path):
    """Главное inline-меню: menu:search → ввод → карточки с «🏠»;
    настройки → выбор площадок set_market:wb."""
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "flow_inline.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("/start", 1, 1))
            await DP.feed_update(bot, _cb_update("menu:search", 2, 2))
            await DP.feed_update(bot, _msg_update("маска для сна", 3, 3))
            await DP.feed_update(bot, _cb_update("menu:settings", 4, 4))
            await DP.feed_update(bot, _cb_update("set_market:wb", 5, 5))
        finally:
            await botmod.db.close()

    _run(run())
    texts = "\n".join(_send_texts(session) + _edit_texts(session))
    buttons = _button_texts(session)
    assert "🏠 <b>Главное меню</b>" in texts  # стартовое inline-меню
    assert "🏠 Главное меню" in buttons        # «🏠» на карточках
    assert "Маска для сна 3D чёрная" in texts  # поиск из меню работает
    assert "только WB" in texts               # выбор площадки применился


def test_start_and_smart_search(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "flow1.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("/start", 1, 1))
            await DP.feed_update(bot, _msg_update(
                "чёрная маска для сна с пространством для ресниц", 2, 2))
        finally:
            await botmod.db.close()

    _run(run())
    texts = _send_texts(session) + _edit_texts(session)
    joined = "\n".join(texts)
    assert "Умный Шоппер" in _send_texts(session)[0]
    assert "Маска для сна 3D чёрная" in joined
    assert "✅ чёрный" in joined and "✅ ресниц" in joined
    assert any("📝 Отзывы" in t for t in _button_texts(session))


def test_refinement_and_reviews_callback(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "flow2.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("маска для сна", 1, 1))
            await DP.feed_update(bot, _msg_update("а подешевле", 2, 2))
            # кликабельные отзывы
            await DP.feed_update(bot, _cb_update("reviews:wb:msk-001w", 3, 3))
            await DP.feed_update(bot, _cb_update("review_item:wb:msk-001w:1", 4, 4))
        finally:
            await botmod.db.close()

    _run(run())
    joined = "\n".join(_edit_texts(session))
    assert "Ваш запрос" in joined or "💬" in joined
    assert any("Отзыв" in t for t in _button_texts(session))  # «Отзыв 1..5»
    assert "★" in joined  # текст отзыва со звёздами


def test_favorites_and_compare(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "flow3.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("маска для сна", 1, 1))
            await DP.feed_update(bot, _cb_update("fav:wb:msk-001w", 2, 2))
            await DP.feed_update(bot, _msg_update("/favorites", 3, 3))
            await DP.feed_update(bot, _msg_update("/compare маска для сна", 4, 4))
        finally:
            await botmod.db.close()

    _run(run())
    joined = "\n".join(_send_texts(session))
    assert "Выгоднее" in joined  # строка сравнения
    assert "Маска для сна 3D" in joined  # карточка из избранного
    assert any("🛒 Открыть на Wildberries" in t for t in _button_texts(session))


def test_budget_command(tmp_path):
    session = CapturingSession()
    bot = _make_bot(session)

    async def run():
        db_path = str(tmp_path / "flow4.db")
        _reset(db_path)
        await botmod.db.connect()
        try:
            await DP.feed_update(bot, _msg_update("/budget", 1, 1))
        finally:
            await botmod.db.close()

    _run(run())
    assert any("лимит" in t.lower() for t in _send_texts(session))
