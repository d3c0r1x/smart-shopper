"""Строгие схемы структурных ответов LLM (валидация через pydantic).

Каждый вызов модели возвращает JSON, который обязан пройти одну из этих схем;
при провале — одна повторная попытка, затем следующий провайдер в цепочке
(проектное решение из PRD, раздел 4 — grounding и валидация).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RankItem(BaseModel):
    """Одна позиция в ранжировании кандидатов."""

    index: int = Field(..., description="индекс товара в переданном списке")
    match_score: int = Field(..., ge=0, le=100, description="0-100, насколько похож")
    reason: str = Field(..., description="почему похож / чем не подходит")


class RankResult(BaseModel):
    """Ранжирование кандидатов: топ-3 в порядке убывания схожести."""

    items: list[RankItem] = Field(..., max_length=5)


class ArbiterVerdict(BaseModel):
    """Вердикт LLM-арбитра: один и тот же ли товар на двух площадках."""

    same: bool
    confidence: float = Field(..., ge=0, le=1)
    reason: str = ""


class FreeformReply(BaseModel):
    """Ответ на свободный вопрос — только на основе переданных фактов."""

    reply: str
    grounded: bool = Field(..., description="опирался ли ответ на переданные данные")
