"""Telegram-бот «Умный Шоппер» — ИИ-ассистент покупок для Ozon и Яндекс Маркета.

Стек: aiogram v3, pydantic, aiosqlite; LLM — OpenRouter :free-модели с
fallback-цепочками и mock-провайдером (без ключа работает демо-режим).

Сценарии (PRD раздел 6):
  1. «Увидел в магазине — сфотографировал»: фото → vision-описание → поиск на
     обеих площадках → ранжирование → 1-3 карточки с кнопками;
  2. «Найди чёрную маску с пространством для ресниц»: извлечение требований →
     поиск → префильтр → Review Intelligence по отзывам → вердикты по каждому
     требованию → топ-варианты.

Команды: /start, /search, /ask, /compare, /favorites, /settings, /budget,
/diag, /stats, /reset. Постоянное меню — в клавиатуре.

Запуск:  python bot.py   (предварительно задайте SHOPPER_BOT_TOKEN)
"""
from __future__ import annotations

import asyncio
import html as _html
import io
import logging
import os
from typing import Awaitable, Callable

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiogram import F
from aiogram.filters import Command, CommandStart
from aiohttp import web
from aiogram.types import (CallbackQuery, InlineKeyboardMarkup, Message)

import config
from adapters import build_adapters
from core.orchestrator import Orchestrator, SearchOutcome
from keyboards import (HOME_DATA, MENU, compare_keyboard, home_keyboard,
                       product_card_keyboard, reviews_keyboard,
                       settings_keyboard)
from llm.gateway import BudgetExceeded, LLMGateway
from llm.schemas import FreeformReply
from matcher.matcher import (compare_across_all, compare_across_markets,
                             find_counterpart)
import web as webmod
from middlewares import LoggingMiddleware, ThrottlingMiddleware
from models import Product, ReviewAnalysis, SessionState
from storage.db import Database
from vision.service import build_data_uri, describe_photo, mime_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.BASE_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(config.DB_PATH)
llm = LLMGateway(db)
adapters = build_adapters()
orch = Orchestrator(db, llm, adapters)

ProgressCb = Callable[[str], Awaitable[None]]


# ────────────────────────────── форматирование ───────────────────

def _rub(price: int | None) -> str:
    if not price or price <= 0:
        return "цена неизвестна"
    return f"<b>{price:,} ₽</b>".replace(",", " ")


def _card_text(p: Product, analysis: ReviewAnalysis | None = None) -> str:
    market = {"ozon": "🟢 Ozon", "yandex": "🔵 Яндекс Маркет",
              "wb": "🟣 Wildberries"}.get(p.marketplace, p.marketplace)
    head = f"{market} · ⭐ {p.rating or '—'} ({p.reviews_count} отзывов)"
    title = f"<b>{_html.escape(p.title, quote=False)}</b>"
    price = _rub(p.price)
    old = f" <s>{p.old_price:,} ₽</s>".replace(",", " ") if p.old_price else ""
    disc = p.discount_percent()
    disc_txt = f" (−{disc}%)" if disc else ""
    traits = " · ".join(p.traits[:4])
    lines = [head, title, f"💰 {price}{old}{disc_txt}"]
    if traits:
        lines.append(f"🏷 {traits}")
    if analysis:
        lines.append(_verdicts_line(analysis))
    if analysis and analysis.summary:
        lines.append(f"💬 {_html.escape(analysis.summary[:180], quote=False)}")
    return "\n".join(lines)


def _verdicts_line(a: ReviewAnalysis) -> str:
    if not a.verdicts:
        return ""
    parts = []
    for v in a.verdicts:
        mark = {"confirmed": "✅", "rejected": "❌", "no_data": "⚠️"}[v.verdict]
        parts.append(f"{mark} {v.requirement}")
    return f"Ваш запрос: {' · '.join(parts)}"


_MARKET_NAMES = {"ozon": "Ozon", "yandex": "Яндекс Маркете", "wb": "Wildberries"}


def _compare_text(rows) -> str:
    lines = ["⚖️ <b>Сравнение цен: Ozon · Яндекс · Wildberries</b>\n"]
    for r in rows:
        line = f"<b>{_html.escape(r.title[:60], quote=False)}</b>\n"
        line += f"🟢 Ozon: {_rub(r.ozon)}\n🔵 Яндекс: {_rub(r.yandex)}\n"
        line += f"🟣 Wildberries: {_rub(r.wb)}\n"
        if r.cheaper and r.diff_percent:
            name = _MARKET_NAMES.get(r.cheaper, r.cheaper)
            line += f"🏆 Выгоднее на {name} на {r.diff_percent}%"
        lines.append(line)
    return "\n\n".join(lines)


# ────────────────────────────── команды ───────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я <b>«Умный Шоппер»</b> — ИИ-ассистент покупок на "
        "<b>Ozon</b>, <b>Яндекс Маркете</b> и <b>Wildberries</b>.\n\n"
        "Могу:\n"
        "• 📸 найти товар по фотографии;\n"
        "• 🔎 найти по описанию: «чёрная маска для сна с пространством для "
        "ресниц» — проверю требования по отзывам;\n"
        "• ⚖️ сравнить цены на трёх площадках;\n"
        "• 📝 показать отзывы и ответить на вопросы о товарах.\n\n"
        "Просто напишите, что ищете, или выберите действие в меню.",
        reply_markup=MENU,
    )
    await message.answer(
        "🏠 <b>Главное меню</b> — выберите действие:",
        reply_markup=home_keyboard(),
    )


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    text = (message.text or "").removeprefix("/search").strip()
    if not text:
        await message.answer("Пример: /search маска для сна 3D чёрная")
        return
    await _run_search(message, text)


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    await db.save_session(message.from_user.id, SessionState())
    await message.answer("🧹 Контекст диалога очищен.")


@router.message(Command("budget"))
async def cmd_budget(message: Message) -> None:
    info = await llm.budget_info()
    await message.answer(_budget_info_text(info),
                         reply_markup=home_keyboard())


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    stats = await db.stats()
    cleaned = await db.cleanup_expired()
    await message.answer(
        "📊 Сводка по базе:\n"
        f"• сессий: {stats['sessions']}\n"
        f"• избранного: {stats['favorites']}\n"
        f"• записей кэша: {stats['cache']}\n"
        f"• очищено истёкшего кэша: {cleaned}"
    )


@router.message(Command("diag"))
async def cmd_diag(message: Message) -> None:
    await message.answer(await _diag_text())


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    await _show_settings(message)


@router.message(Command("favorites"))
async def cmd_favorites(message: Message) -> None:
    await _show_favorites(message, message.from_user.id)


@router.message(Command("ask"))
async def cmd_ask(message: Message) -> None:
    question = (message.text or "").removeprefix("/ask").strip()
    if not question:
        await message.answer("Пример: /ask стоит ли брать этот бренд?")
        return
    await _freeform(message, question)


# ────────────────────────────── текст и фото ──────────────────────

@router.message(Command("compare"))
async def cmd_compare(message: Message) -> None:
    text = (message.text or "").removeprefix("/compare").strip()
    if not text:
        await _set_mode(message, "compare_wait", "Что сравнить? Напишите название товара.")
        return
    await _run_compare(message, text)


@router.message()
async def on_text(message: Message) -> None:
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if not text:
        return
    state = await db.get_session(user_id)

    menu_actions = {
        "📸 Найти по фото": ("photo_wait", "📸 Отправьте фотографию товара."),
        "🔎 Умный поиск": ("search_wait", "🔎 Что ищете? Опишите словами."),
        "⚖️ Сравнить цены": ("compare_wait", "⚖️ Что сравнить? Напишите название товара."),
        "⭐ Избранное": None,
        "⚙️ Настройки": None,
    }
    if text in menu_actions:
        action = menu_actions[text]
        if action is None:
            if text == "⭐ Избранное":
                await _show_favorites(message, user_id)
            else:
                await _show_settings(message)
            return
        await _set_mode(message, action[0], action[1])
        return

    if state.mode == "compare_wait":
        await _run_compare(message, text)
        return
    if state.mode == "search_wait":
        await _run_search(message, text)
        return
    if state.mode == "photo_wait":
        await message.answer("Отправьте фото товара (кнопка 📎 → фотография).")
        return

    # уточнение прошлого поиска или свободный вопрос о найденном
    if _is_refinement(text) and state.constraints and state.mode in ("search", "photo"):
        await _run_search(message, text)
        return
    if text.endswith("?") and state.last_results:
        facts = "\n".join(f"- {p.title} ({p.price} ₽, {p.rating}★, {p.reviews_count} отз.)"
                          for p in state.last_results[:5])
        await _freeform(message, text, facts=facts)
        return
    await _run_search(message, text)


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    user_id = message.from_user.id
    state = await db.get_session(user_id)
    progress = await message.answer("📸 Скачиваю фотографию…")
    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, destination=buf)
        data_uri = build_data_uri(buf.getvalue(), mime_for(file.file_path or ""))

        async def progress_cb(step: str) -> None:
            await progress.edit_text(step)

        desc = await describe_photo(llm, data_uri)
        markets = _markets(state)
        outcome = await orch.photo_search(user_id, desc, state, progress_cb,
                                          markets=markets)
        await progress.delete()
        await _send_outcome(message, outcome, "похожие на фото")
    except BudgetExceeded:
        info = await llm.budget_info()
        await progress.edit_text(_budget_text(info))
    except Exception as exc:  # pragma: no cover
        logger.exception("photo flow failed")
        await progress.edit_text(f"Не получилось обработать фото: {exc}")


# ────────────────────────────── inline-колбэки ────────────────────

@router.callback_query(lambda c: c.data.startswith("menu:"))
async def cb_menu(call: CallbackQuery) -> None:
    """Главное inline-меню: навигация по всем экранам бота."""
    action = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    if action == "main":
        await _answer_home(call)
        return
    if action == "search":
        await _set_mode_from_call(
            call, "search_wait",
            "🔎 Опишите товар словами — например: «чёрная маска для сна "
            "с пространством для ресниц».")
        return
    if action == "photo":
        await _set_mode_from_call(
            call, "photo_wait",
            "📸 Отправьте фотографию товара (кнопка 📎 → фотография).")
        return
    if action == "compare":
        await _set_mode_from_call(
            call, "compare_wait",
            "⚖️ Что сравнить? Напишите название товара.")
        return
    if action == "favorites":
        await _show_favorites_inline(call, user_id)
        return
    if action == "settings":
        await _show_settings_inline(call)
        return
    if action == "budget":
        info = await llm.budget_info()
        await _edit_or_answer(call, _budget_info_text(info),
                              reply_markup=home_keyboard())
        return
    if action == "diag":
        await _edit_or_answer(call, await _diag_text(),
                              reply_markup=home_keyboard())
        return
    if action == "help":
        await _edit_or_answer(call, _help_text(),
                              reply_markup=home_keyboard())
        return
    await call.answer("Неизвестное действие", show_alert=True)


@router.callback_query(lambda c: c.data.startswith("reviews:"))
async def cb_reviews(call: CallbackQuery) -> None:
    _, market, ext_id = call.data.split(":")
    product = await _find_product(call.from_user.id, market, ext_id)
    if product is None:
        await call.answer("Товар не найден в текущем контексте", show_alert=True)
        return
    state = await db.get_session(call.from_user.id)
    requirements = (state.constraints.must_have
                    if state.constraints else [])
    progress = await call.message.answer(f"📖 Читаю отзывы на «{product.title[:40]}…»")
    reviews = await orch._load_reviews(product)
    if not reviews:
        await progress.edit_text("Отзывов пока нет.")
        return
    analysis = await _analyze(product, reviews, requirements)
    await progress.delete()
    await _send_analysis(call, product, analysis, reviews)


@router.callback_query(lambda c: c.data.startswith("review_item:"))
async def cb_review_item(call: CallbackQuery) -> None:
    _, market, ext_id, idx = call.data.split(":")
    product = await _find_product(call.from_user.id, market, ext_id)
    if product is None:
        return
    reviews = await orch._load_reviews(product)
    n = int(idx) - 1
    if n < 0 or n >= len(reviews):
        await call.answer("Такого отзыва нет", show_alert=True)
        return
    r = reviews[n]
    stars = "★" * r.rating + "☆" * (5 - r.rating)
    bought = " · 🛒 куплен на маркетплейсе" if r.bought_here else ""
    text = (
        f"{stars} <b>{r.rating}/5</b> · {_html.escape(r.author, quote=False)}"
        f" · {r.date}{bought}\n\n"
        f"{_html.escape(r.text, quote=False)}"
    )
    if r.pros:
        text += f"\n\n👍 {_html.escape(r.pros, quote=False)}"
    if r.cons:
        text += f"\n👎 {_html.escape(r.cons, quote=False)}"
    kb = reviews_keyboard(market, ext_id, len(reviews))
    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(lambda c: c.data.startswith("review_more:"))
async def cb_review_more(call: CallbackQuery) -> None:
    _, market, ext_id, page = call.data.split(":")
    product = await _find_product(call.from_user.id, market, ext_id)
    if product is None:
        return
    reviews = await orch._load_reviews(product)
    kb = reviews_keyboard(market, ext_id, len(reviews), page=int(page))
    await call.message.edit_reply_markup(reply_markup=kb)


@router.callback_query(lambda c: c.data.startswith("back:"))
async def cb_back(call: CallbackQuery) -> None:
    _, market, ext_id = call.data.split(":")
    product = await _find_product(call.from_user.id, market, ext_id)
    if product is None:
        return
    await _send_card(call.message, product)


@router.callback_query(lambda c: c.data.startswith("compare:"))
async def cb_compare(call: CallbackQuery) -> None:
    _, market, ext_id = call.data.split(":")
    product = await _find_product(call.from_user.id, market, ext_id)
    if product is None:
        await call.answer("Товар не найден", show_alert=True)
        return
    await call.answer("⚖️ Сравниваю цены…")
    await _run_compare(call.message, product.title)


@router.callback_query(lambda c: c.data.startswith("fav:"))
async def cb_fav(call: CallbackQuery) -> None:
    _, market, ext_id = call.data.split(":")
    product = await _find_product(call.from_user.id, market, ext_id)
    if product is None:
        await call.answer("Товар не найден", show_alert=True)
        return
    added = await db.add_favorite(call.from_user.id, product)
    if added:
        await call.answer("⭐ Добавлено в избранное")
    else:
        await db.remove_favorite(call.from_user.id, product)
        await call.answer("Убрано из избранного")


@router.callback_query(lambda c: c.data.startswith("more:"))
async def cb_more(call: CallbackQuery) -> None:
    state = await db.get_session(call.from_user.id)
    if not state.constraints:
        await call.answer("Сначала выполните поиск", show_alert=True)
        return
    await call.answer("🔁 Ищу ещё похожие…")
    constraints = state.constraints
    outcome = await orch._execute_search(constraints, None)
    state.last_results = outcome.top
    await db.save_session(call.from_user.id, state)
    await _send_outcome(call.message, outcome, "похожие варианты")


@router.callback_query(lambda c: c.data.startswith("set_profile:"))
async def cb_set_profile(call: CallbackQuery) -> None:
    profile = call.data.split(":")[1]
    llm.set_profile(profile)
    await db.set_setting("profile", profile)
    await call.answer(f"Профиль моделей: {profile}")
    await _show_settings_inline(call)


@router.callback_query(lambda c: c.data == "set_clear")
async def cb_set_clear(call: CallbackQuery) -> None:
    await db.save_session(call.from_user.id, SessionState())
    await call.answer("Контекст очищен")
    await _show_settings_inline(call)


@router.callback_query(lambda c: c.data.startswith("set_market:"))
async def cb_set_market(call: CallbackQuery) -> None:
    """Выбор площадок для поиска (both | ozon | yandex | wb)."""
    market = call.data.split(":", 1)[1]
    if market not in ("both", "ozon", "yandex", "wb"):
        await call.answer("Неизвестная площадка", show_alert=True)
        return
    state = await db.get_session(call.from_user.id)
    state.default_market = market
    await db.save_session(call.from_user.id, state)
    await call.answer(f"Площадки: {market}")
    await _show_settings_inline(call)


# ────────────────────────────── сценарии ──────────────────────────

async def _run_search(message: Message, user_text: str) -> None:
    user_id = message.from_user.id
    state = await db.get_session(user_id)
    progress = await message.answer("🔎 Начинаю поиск…")
    try:
        async def progress_cb(step: str) -> None:
            await progress.edit_text(step)

        outcome = await orch.search_with_constraints(
            user_id, user_text, state, progress_cb, markets=_markets(state))
        await progress.delete()
        await _send_outcome(message, outcome, f"по запросу «{user_text[:40]}»")
    except BudgetExceeded:
        info = await llm.budget_info()
        await progress.edit_text(_budget_text(info))
    except Exception as exc:  # pragma: no cover
        logger.exception("search flow failed")
        await progress.edit_text(f"Не получилось выполнить поиск: {exc}")


async def _run_compare(message: Message, text: str) -> None:
    user_id = message.from_user.id
    state = await db.get_session(user_id)
    progress = await message.answer("⚖️ Ищу на обеих площадках…")
    try:
        candidates = await orch.search_candidates(text, markets=_markets(state))
        if not candidates:
            await progress.edit_text("Не нашёл товаров по этому запросу.")
            return
        rows = []
        for target in candidates[:3]:
            # одна строка на товар со всеми площадками, где нашёлся тот же товар
            result = await compare_across_all(llm, target, candidates)
            if result is not None:
                rows.append(result)
        await progress.delete()
        if not rows:
            await message.answer("⚖️ Тот же товар на другой площадке не найден "
                                 "(проверьте написание или попробуйте другое название).")
            return
        kb = compare_keyboard(rows[0].ozon_url, rows[0].yandex_url, rows[0].wb_url)
        await message.answer(_compare_text(rows), reply_markup=kb)
    except BudgetExceeded:
        info = await llm.budget_info()
        await progress.edit_text(_budget_text(info))
    except Exception as exc:  # pragma: no cover
        logger.exception("compare flow failed")
        await progress.edit_text(f"Не получилось сравнить: {exc}")


async def _freeform(message: Message, question: str, facts: str = "") -> None:
    progress = await message.answer("💭 Думаю…")
    try:
        reply = await orch.freeform(question, facts=facts)
        await progress.edit_text(f"{_html.escape(reply, quote=False)}")
    except BudgetExceeded:
        info = await llm.budget_info()
        await progress.edit_text(_budget_text(info))


# ────────────────────────────── вывод результата ──────────────────

async def _send_outcome(message: Message, outcome: SearchOutcome, label: str) -> None:
    if not outcome.top:
        await message.answer(
            f"По запросу «{label}» ничего не нашлось. Попробуйте другие слова "
            "или добавьте маркетплейс в настройках."
        )
        return
    await message.answer(
        f"<b>Найдено {label}:</b> показываю лучшие варианты 👇"
    )
    for p in outcome.top:
        analysis = outcome.analyses.get(p.ext_id)
        await _send_card(message, p, analysis)


async def _send_card(message: Message, p: Product,
                     analysis: ReviewAnalysis | None = None) -> None:
    # Поисковая выдача не содержит характеристик и фото — обогащаем карточку
    # реальными данными (канал 1) при первом показе.
    if (not config.DEMO_MODE and (not p.photo_url or not p.traits)
            and p.marketplace in ("ozon", "wb", "yandex")):
        try:
            card = await orch._load_card(p)
            if card is not None:
                p = card
        except Exception as exc:  # pragma: no cover
            logger.warning("Карточка не обогащена: %s", exc)
    state = await db.get_session(message.from_user.id)
    favorites = await db.list_favorites(message.from_user.id)
    favored = any(f.ext_id == p.ext_id and f.marketplace == p.marketplace
                  for f in favorites)
    kb = product_card_keyboard(p, favored=favored)
    if config.DEMO_MODE or not p.photo_url:
        await message.answer(_card_text(p, analysis), reply_markup=kb)
    else:  # pragma: no cover — реальный режим
        await message.answer_photo(p.photo_url, caption=_card_text(p, analysis),
                                   reply_markup=kb)


async def _send_analysis(call: CallbackQuery, product: Product,
                         analysis: ReviewAnalysis, reviews) -> None:
    lines = [_card_text(product, analysis)]
    kb = reviews_keyboard(product.marketplace, product.ext_id, len(reviews))
    if call.message.photo or call.message.text:
        await call.message.edit_text("\n".join(lines), reply_markup=kb)


async def _settings_payload(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура экрана настроек (для сообщений и колбэков)."""
    info = await llm.budget_info()
    profile = await db.get_setting("profile", config.LLM_PROFILE)
    state = await db.get_session(user_id)
    market = state.default_market
    market_names = {"both": "Ozon + Яндекс + WB", "ozon": "только Ozon",
                    "yandex": "только Яндекс", "wb": "только WB"}
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"• Профиль моделей: <b>{info['profile']}</b>\n"
        f"• Площадки: <b>{market_names.get(market, market)}</b>\n"
        f"• Осталось запросов сегодня: <b>{info['remaining']} из {info['limit']}</b>\n\n"
        "Профиль «быстро» — короткая цепочка моделей, «качественно» — "
        "длинный контекст для анализа отзывов."
    )
    return text, settings_keyboard(profile=profile, market=market)


async def _show_settings(message: Message) -> None:
    text, kb = await _settings_payload(message.from_user.id)
    await message.answer(text, reply_markup=kb)


async def _show_settings_inline(call: CallbackQuery) -> None:
    text, kb = await _settings_payload(call.from_user.id)
    await _edit_or_answer(call, text, reply_markup=kb)


async def _show_favorites(message: Message, user_id: int) -> None:
    favorites = await db.list_favorites(user_id)
    if not favorites:
        await message.answer("⭐ Избранное пусто. Нажимайте ⭐ на карточках товаров.")
        return
    for p in favorites[:5]:
        await _send_card(message, p)
    if len(favorites) > 5:
        await message.answer(f"…и ещё {len(favorites) - 5} товаров.")


async def _show_favorites_inline(call: CallbackQuery, user_id: int) -> None:
    """Избранное из главного меню: карточки + кнопка «🏠»."""
    favorites = await db.list_favorites(user_id)
    if not favorites:
        await _edit_or_answer(
            call, "⭐ Избранное пусто. Нажимайте ⭐ на карточках товаров.",
            reply_markup=home_keyboard())
        return
    await _edit_or_answer(
        call, f"⭐ <b>Избранное</b>: {len(favorites)} товаров",
        reply_markup=home_keyboard())
    for p in favorites[:5]:
        await _send_card(call.message, p)
    if len(favorites) > 5:
        await call.message.answer(
            f"…и ещё {len(favorites) - 5} товаров.",
            reply_markup=home_keyboard())


# ────────────────────────────── помощники ─────────────────────────

async def _set_mode(message: Message, mode: str, reply: str) -> None:
    state = await db.get_session(message.from_user.id)
    state.mode = mode
    await db.save_session(message.from_user.id, state)
    await message.answer(reply, reply_markup=MENU)


async def _set_mode_from_call(call: CallbackQuery, mode: str, reply: str) -> None:
    """Вход в режим ожидания ввода из inline-меню."""
    state = await db.get_session(call.from_user.id)
    state.mode = mode
    await db.save_session(call.from_user.id, state)
    await _edit_or_answer(call, reply, reply_markup=home_keyboard())


async def _answer_home(call: CallbackQuery) -> None:
    """Экран «🏠 Главное меню» (из любого места бота)."""
    await _edit_or_answer(
        call, "🏠 <b>Главное меню</b> — выберите действие:",
        reply_markup=home_keyboard())


async def _edit_or_answer(call: CallbackQuery, text: str,
                          reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Правка текущего сообщения; если не вышло — ответ новым."""
    msg = call.message
    if msg is None:
        await call.answer(text, show_alert=True)
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup,
                            disable_web_page_preview=True)
    except Exception:
        await msg.answer(text, reply_markup=reply_markup)


def _markets(state: SessionState) -> list[str] | None:
    if state.default_market in ("ozon", "yandex", "wb"):
        return [state.default_market]
    return None


def _is_refinement(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ("подешевле", "дешевле", "дешевл", "только",
                                  "без ", "ещё", "а как насчёт", "другой цвет",
                                  "другая модель", "не дороже", "доступнее"))


async def _find_product(user_id: int, market: str, ext_id: str) -> Product | None:
    state = await db.get_session(user_id)
    for p in state.last_results:
        if p.marketplace == market and p.ext_id == ext_id:
            return p
    return None


async def _analyze(product: Product, reviews, requirements: list[str]) -> ReviewAnalysis:
    from review.intelligence import analyze_reviews
    return await analyze_reviews(llm, product, reviews, requirements, db=db)


def _budget_info_text(info: dict) -> str:
    """Экран «Бюджет»: остаток на сегодня и активная модель."""
    if info.get("local"):
        llm_line = "Модель: <b>локальная</b> (Ollama) — бесплатно и без лимита"
    elif info["real_provider"]:
        llm_line = f"Модель: облачная ({info.get('provider', 'LLM')})"
    else:
        llm_line = "Модель: демо (mock, без ключа)"
    return (
        "🔋 <b>Бюджет LLM</b>\n\n"
        f"• Сегодня использовано: <b>{info['used']} из {info['limit']}</b>\n"
        f"• Профиль моделей: <b>{info['profile']}</b>\n"
        f"• {llm_line}\n\n"
        "Локальная модель (Ollama) бесплатна и не тратит дневной лимит."
    )


def _help_text() -> str:
    return (
        "📖 <b>Помощь</b>\n\n"
        "Я — «Умный Шоппер», ИИ-ассистент покупок на Ozon, Яндекс Маркете "
        "и Wildberries.\n\n"
        "🔎 <b>Умный поиск</b> — опишите товар и требования: "
        "«чёрная маска для сна с пространством для ресниц» — и я проверю "
        "требования по отзывам.\n"
        "📸 <b>По фото</b> — отправьте фотографию товара, я найду похожие.\n"
        "⚖️ <b>Сравнение цен</b> — сравню тот же товар на трёх площадках.\n"
        "⭐ <b>Избранное</b> — сохранённые карточки.\n"
        "⚙️ <b>Настройки</b> — профиль моделей и выбор площадок.\n\n"
        "На карточках товара есть кнопки: отзывы, сравнение, избранное, "
        "похожие и «🏠 Главное меню»."
    )


def _budget_text(info: dict) -> str:
    return (
        f"🔋 <b>Дневной лимит бесплатных запросов исчерпан</b>\n\n"
        f"Сегодня использовано {info['used']} из {info['limit']} запросов "
        f"(лимит {info['limit']}/день на бесплатные модели OpenRouter).\n"
        "Лимит сбросится в полночь (UTC). Либо разово пополните аккаунт "
        "OpenRouter от $10 — дневной лимит вырастет до 1000 запросов/день.\n"
        "Источник: документация OpenRouter (лимиты :free-моделей)."
    )


# ────────────────────────────── запуск ────────────────────────────

async def _handle_update_error(event, exception: Exception) -> None:
    """Обработчик ошибок апдейтов: не даём stale-апдейтам валить бота.

    Типовой случай: пользователь заблокировал бота, а в очереди остался
    старый апдейт — ответ на него даёт TelegramForbiddenError. Поллинг
    должен продолжаться, поэтому логируем одной строкой.
    """
    from aiogram.exceptions import TelegramAPIError

    if isinstance(exception, TelegramAPIError):
        logger.warning("Апдейт %s не обработан (%s): %s",
                       getattr(event, "update_id", "?"),
                       type(exception).__name__, exception)
    else:
        logger.exception("Апдейт %s упал с ошибкой", getattr(event, "update_id", "?"))


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("SHOPPER_BOT_TOKEN не задан — проверьте .env / run_bot12.cmd")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    dp.errors.register(_handle_update_error)
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    await db.connect()
    saved_profile = await db.get_setting("profile", config.LLM_PROFILE)
    llm.set_profile(saved_profile)
    await orch._db.cleanup_expired()

    # web_app-кнопка у поля ввода (PRD: «раздаёт кнопку открытия Mini App»)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🛍 Умный Шоппер",
                                         web_app=WebAppInfo(url=config.MINIAPP_URL)))
        logger.info("Mini App привязан: %s", config.MINIAPP_URL)
    except Exception as exc:  # без прав на изменение меню — не критично
        logger.warning("set_chat_menu_button не удался: %s", exc)

    # HTTP-API для Mini App (один backend, два рендерера)
    ctx = webmod.ApiContext(db, llm, orch, adapters)
    ctx.matcher = lambda target, candidates: compare_across_markets(
        llm, target, candidates)
    app = webmod.create_app(ctx)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", config.API_PORT)
    await site.start()

    logger.info("Умный Шоппер запущен. Демо-режим: %s. LLM: %s. Профиль: %s. "
                "HTTP-API: http://127.0.0.1:%d",
                config.DEMO_MODE, llm.provider_name,
                llm._profile, config.API_PORT)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await db.close()
        await llm.aclose()
        for a in adapters:
            await a.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлен.")
