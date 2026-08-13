"""Клавиатуры бота: persistent-меню, кнопки карточек, отзывов, сравнения.

UX-карта из PRD (раздел 7): «красивые кнопки» = главное меню в постоянной
клавиатуре + inline-кнопки действий на каждой карточке товара.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, \
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from models import Product

def _menu() -> ReplyKeyboardMarkup:
    """Постоянное меню + web_app-кнопка Mini App (PRD: кнопка в клавиатуре)."""
    import config
    kb = [
        [KeyboardButton(text="📸 Найти по фото"),
         KeyboardButton(text="🔎 Умный поиск")],
        [KeyboardButton(text="⚖️ Сравнить цены"),
         KeyboardButton(text="⭐ Избранное")],
        [KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="🛍 Mini App",
                        web_app=WebAppInfo(url=config.MINIAPP_URL))],
    ]
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        input_field_placeholder="Напишите, что ищете…",
    )


MENU = _menu()


def product_card_keyboard(p: Product, *, favored: bool = False) -> InlineKeyboardMarkup:
    """Кнопки под карточкой товара (PRD, сценарий 1, шаг 5)."""
    fav = "⭐ Убрать" if favored else "⭐ В избранное"
    kb = [
        [InlineKeyboardButton(text=f"📝 Отзывы ({p.reviews_count})",
                              callback_data=f"reviews:{p.marketplace}:{p.ext_id}"),
         InlineKeyboardButton(text="⚖️ Ozon vs YM",
                              callback_data=f"compare:{p.marketplace}:{p.ext_id}")],
        [InlineKeyboardButton(text=fav,
                              callback_data=f"fav:{p.marketplace}:{p.ext_id}"),
         InlineKeyboardButton(text="🔁 Ещё похожие",
                              callback_data=f"more:{p.ext_id}")],
        [InlineKeyboardButton(text="🛒 Открыть на Ozon", url=p.url)]
        if p.marketplace == "ozon" else
        [InlineKeyboardButton(text="🛒 Открыть на Яндекс Маркете", url=p.url)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def reviews_keyboard(market: str, ext_id: str, count: int,
                     page: int = 0) -> InlineKeyboardMarkup:
    """Кнопки «Отзыв 1…N» + «Ещё» + «← К товару» (кликабельные отзывы)."""
    page_size = 5
    start = page * page_size
    items = [
        InlineKeyboardButton(text=str(i), callback_data=f"review_item:{market}:{ext_id}:{i}")
        for i in range(start + 1, min(start + page_size, count) + 1)
    ]
    nav = []
    if start + page_size < count:
        nav.append(InlineKeyboardButton(text="▶️ Ещё отзывы",
                                        callback_data=f"review_more:{market}:{ext_id}:{page + 1}"))
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад",
                                        callback_data=f"review_more:{market}:{ext_id}:{page - 1}"))
    kb = [items]
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="← К товару",
                                    callback_data=f"back:{market}:{ext_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def compare_keyboard(ozon_url: str = "", yandex_url: str = "",
                      wb_url: str = "") -> InlineKeyboardMarkup:
    """Кнопки-ссылки на карточки площадок, участвующих в сравнении."""
    row = []
    if ozon_url:
        row.append(InlineKeyboardButton(text="🛒 Ozon", url=ozon_url))
    if yandex_url:
        row.append(InlineKeyboardButton(text="🛒 Яндекс", url=yandex_url))
    if wb_url:
        row.append(InlineKeyboardButton(text="🛒 WB", url=wb_url))
    return InlineKeyboardMarkup(inline_keyboard=[row] if row else [])


def settings_keyboard(*, profile: str) -> InlineKeyboardMarkup:
    fast = "✅ " if profile == "fast" else ""
    quality = "✅ " if profile == "quality" else ""
    kb = [
        [InlineKeyboardButton(text=f"{fast}Быстро", callback_data="set_profile:fast"),
         InlineKeyboardButton(text=f"{quality}Качественно",
                              callback_data="set_profile:quality")],
        [InlineKeyboardButton(text="🧹 Очистить контекст", callback_data="set_clear")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
