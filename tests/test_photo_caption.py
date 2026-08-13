from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bot as botmod
from models import Product, ReviewAnalysis


def test_photo_review_navigation_edits_caption():
    """Отзывы из фото-карточки редактируют caption, а не несуществующий text."""
    edited = []

    class PhotoMessage:
        photo = [object()]
        text = None

        async def edit_text(self, *args, **kwargs):
            raise AssertionError("photo message must not use edit_text")

        async def edit_caption(self, *, caption, reply_markup):
            edited.append((caption, reply_markup))

    call = SimpleNamespace(message=PhotoMessage())
    product = Product(
        marketplace="ozon", ext_id="photo-1", title="Маска для сна",
        price=500, url="https://www.ozon.ru/product/photo-1/",
    )
    analysis = ReviewAnalysis(product_market="ozon", product_id="photo-1")

    asyncio.run(botmod._send_analysis(call, product, analysis, [object()]))

    assert edited and "Маска для сна" in edited[0][0]
