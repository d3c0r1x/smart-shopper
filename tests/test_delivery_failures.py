from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bot as botmod
from aiogram.types import ErrorEvent, Update
from keyboards import product_card_keyboard
from models import Product, SessionState


LONG_OZON_ID = (
    "maska-dlya-sna-zhenskaya-muzhskaya-myagkaya-maska-3d-udobnaya-"
    "dlya-doma-samoleta-i-puteshestviy-1944375083"
)


def _product(ext_id: str = LONG_OZON_ID, *, photo_url: str = "") -> Product:
    return Product(
        marketplace="ozon",
        ext_id=ext_id,
        title="Маска для сна",
        price=500,
        url="https://www.ozon.ru/product/maska/",
        photo_url=photo_url,
        traits=["3D"],
    )


def test_product_card_callback_data_fits_telegram_limit():
    markup = product_card_keyboard(_product())

    callback_data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert callback_data
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)
    assert all(LONG_OZON_ID not in value for value in callback_data)


def test_photo_delivery_falls_back_to_text(monkeypatch):
    sent = []

    class Message:
        from_user = SimpleNamespace(id=42)

        async def answer_photo(self, *args, **kwargs):
            raise RuntimeError("Telegram rejected the remote photo URL")

        async def answer(self, text, reply_markup=None):
            sent.append((text, reply_markup))

    async def no_favorites(user_id):
        return []

    async def empty_session(user_id):
        return SessionState()

    monkeypatch.setattr(botmod.config, "DEMO_MODE", False)
    monkeypatch.setattr(botmod.db, "list_favorites", no_favorites)
    monkeypatch.setattr(botmod.db, "get_session", empty_session)

    asyncio.run(botmod._send_card(Message(), _product(photo_url="https://bad.example/image.jpg")))

    assert sent and "Маска для сна" in sent[0][0]


def test_search_error_keeps_progress_message(monkeypatch):
    class Progress:
        def __init__(self):
            self.deleted = False
            self.edited = []

        async def delete(self):
            self.deleted = True

        async def edit_text(self, text):
            if self.deleted:
                raise AssertionError("deleted progress message was edited")
            self.edited.append(text)

    progress = Progress()

    class Message:
        from_user = SimpleNamespace(id=42)

        async def answer(self, text):
            return progress

    async def get_session(user_id):
        return SessionState()

    async def search_with_constraints(*args, **kwargs):
        return object()

    async def failing_send_outcome(*args, **kwargs):
        raise RuntimeError("result delivery failed")

    monkeypatch.setattr(botmod.db, "get_session", get_session)
    monkeypatch.setattr(botmod.orch, "search_with_constraints", search_with_constraints)
    monkeypatch.setattr(botmod, "_send_outcome", failing_send_outcome)

    asyncio.run(botmod._run_search(Message(), "маска для сна"))

    assert not progress.deleted
    assert progress.edited and "Не получилось выполнить поиск" in progress.edited[0]


def test_error_handler_accepts_aiogram_error_event(caplog):
    event = ErrorEvent(
        update=Update(update_id=123),
        exception=RuntimeError("test update failure"),
    )

    asyncio.run(botmod._handle_update_error(event))

    assert "Апдейт 123 упал с ошибкой" in caplog.text


def test_slow_card_enrichment_does_not_block_text_result(monkeypatch):
    """Медленный антибот не должен скрывать уже найденную карточку."""
    sent = []

    class Message:
        from_user = SimpleNamespace(id=42)

        async def answer(self, text, reply_markup=None):
            sent.append((text, reply_markup))

    async def get_session(user_id):
        return SessionState()

    async def no_favorites(user_id):
        return []

    async def never_finishes(ext_id, limit=5):
        await asyncio.Event().wait()

    adapter = SimpleNamespace(name="ozon", get_card=never_finishes)

    monkeypatch.setattr(botmod.config, "DEMO_MODE", False)
    monkeypatch.setattr(botmod.config, "CARD_ENRICH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(botmod.db, "get_session", get_session)
    monkeypatch.setattr(botmod.db, "list_favorites", no_favorites)
    monkeypatch.setattr(botmod.orch, "_adapters", [adapter])

    async def run():
        await asyncio.wait_for(
            botmod._send_card(Message(), _product(photo_url="")), timeout=0.1)

    asyncio.run(run())

    assert sent and "Маска для сна" in sent[0][0]
