"""Vision-сервис: фото → VisionDescription (PRD, сценарий 1, шаг 2).

Фото скачивается с Telegram, кодируется в data URI и передаётся vision-модели
(цепочка из llm/gateway.py). Без ключа OpenRouter работает mock-провайдер
(демо-сценарий «кроссовки»), чтобы сценарий был проверяем оффлайн.
"""
from __future__ import annotations

import base64
import logging

from llm.gateway import LLMGateway
from llm.prompts import VISION_PROMPT
from models import VisionDescription

logger = logging.getLogger(__name__)

_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
         "webp": "image/webp", "gif": "image/gif"}


def build_data_uri(data: bytes, mime: str = "image/jpeg") -> str:
    """Фото в data URI — единственный способ передать картинку OpenRouter."""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def mime_for(file_name: str) -> str:
    ext = (file_name or "").rsplit(".", 1)[-1].lower()
    return _MIME.get(ext, "image/jpeg")


async def describe_photo(llm: LLMGateway, data_uri: str | None = None) -> VisionDescription:
    """Структурное описание товара по фото (None — чистый mock-режим)."""
    images = [data_uri] if data_uri else None
    return await llm.structured(kind="vision", prompt=VISION_PROMPT,
                                schema=VisionDescription, images=images)
