"""Провайдеры LLM: Mistral, OpenRouter и детерминированный mock.

Оба реальных провайдера работают через openai-совместимый SDK
(Mistral — https://api.mistral.ai/v1, OpenRouter — https://openrouter.ai/api/v1)
и отличаются только base_url и именем (используется роутингом цепочек
в llm/gateway.py). MockProvider — оффлайн-фолбэк: возвращает валидные по
схеме ответы на основе простых эвристик, чтобы бот работал без ключа
(демо-режим).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Type

from pydantic import BaseModel

from llm.schemas import ArbiterVerdict, FreeformReply, RankResult
from models import ReviewAnalysis, SearchConstraints, VisionDescription

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Провайдер не смог вернуть валидный структурный ответ."""


class OpenAICompatProvider:
    """Реальный LLM-провайдер через openai-совместимый SDK.

    Параметризуется base_url и именем: Mistral и OpenRouter используют один
    и тот же протокол /chat/completions и response_format (json_schema).
    Имя (self.name) используется роутингом цепочек в llm/gateway.py.
    """

    def __init__(self, api_key: str, base_url: str, timeout: float,
                 name: str) -> None:
        from openai import AsyncOpenAI

        self.name = name
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url,
                                   timeout=timeout)
        self._timeout = timeout

    async def complete(self, *, model: str, kind: str, prompt: str,
                       schema: Type[BaseModel],
                       images: list[str] | None = None) -> BaseModel:
        """Один вызов модели: возвращает валидированный schema-объект."""
        if images:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in images:
                content.append({"type": "image_url",
                                "image_url": {"url": img}})
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": prompt}]

        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model, messages=messages, temperature=0.1,
                    max_tokens=1600,
                    response_format=_json_schema(schema),
                ),
                timeout=self._timeout,
            )
        except Exception as exc:  # сеть, таймаут, 429, 5xx
            if _structured_unsupported(exc):
                # модель не умеет response_format — повторяем без него
                logger.info("Модель %s не поддерживает structured output, "
                            "пробую без response_format", model)
                try:
                    resp = await asyncio.wait_for(
                        self._client.chat.completions.create(
                            model=model, messages=messages, temperature=0.1,
                            max_tokens=1600,
                        ),
                        timeout=self._timeout,
                    )
                except Exception as exc2:
                    raise ProviderError(f"{type(exc2).__name__}: {exc2}") from exc2
            else:
                raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip()
        try:
            parsed = _extract_json(raw)
            return schema.model_validate(parsed)
        except Exception as exc:
            # пустой/битый ответ или неверная схема — ProviderError,
            # gateway пробует следующую модель в цепочке (fallback)
            raise ProviderError(f"ответ модели невалиден: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.close()


class MistralProvider(OpenAICompatProvider):
    """Mistral API (openai-совместимый, https://api.mistral.ai/v1)."""

    def __init__(self, api_key: str, timeout: float) -> None:
        from config import MISTRAL_BASE_URL

        super().__init__(api_key, MISTRAL_BASE_URL, timeout, name="mistral")


class OpenRouterProvider(OpenAICompatProvider):
    """OpenRouter :free-модели (https://openrouter.ai/api/v1)."""

    def __init__(self, api_key: str, timeout: float) -> None:
        from config import OPENROUTER_BASE_URL

        super().__init__(api_key, OPENROUTER_BASE_URL, timeout,
                         name="openrouter")


def _json_schema(schema: Type[BaseModel]) -> dict:
    """OpenAI-стиль response_format: json_schema (strict)."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }


def _structured_unsupported(exc: Exception) -> bool:
    """Похоже ли на 400 «response_format не поддерживается моделью»."""
    msg = str(exc).lower()
    return "response_format" in msg or "structured output" in msg         or "json_schema" in msg


def _extract_json(raw: str) -> dict:
    """Достаёт JSON-объект из ответа (устойчив к ```json-обёрткам)."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError(f"нет JSON в ответе модели: {raw[:200]!r}")


# ───────────────────────── mock-провайдер ─────────────────────────

_PRICE_RE = re.compile(r"(?:до|не дороже|не больше|в пределах|около|примерно|за)\s*"
                       r"(\d{3,7})(\s*(?:тыс\.?|к))?(?: рублей| руб|₽)?", re.IGNORECASE)
_STOP_WORDS = {"для", "с", "на", "и", "в", "из", "по", "от", "не", "за",
               "или", "а", "но", "до", "как", "что", "это", "такой", "мне"}

_COLOR_STEMS = {"бел": "белый", "чёрн": "чёрный", "черн": "чёрный",
                "красн": "красный", "син": "синий", "зелён": "зелёный",
                "зелен": "зелёный", "жёлт": "жёлтый", "желт": "жёлтый",
                "сер": "серый", "розов": "розовый", "голуб": "голубой",
                "коричнев": "коричневый", "бежев": "бежевый", "золот": "золотой"}

# обязательные требования-подстроки (кроме цветов)
_KEYWORD_REQS = ["пространство для ресниц", "ресниц", "прилега", "не давит",
                 "не скольз", "дышащ", "водонепрониц", "утеплён", "утеплен",
                 "3d", "гель", "шёлк", "шелк"]


def _detect_price(text: str) -> int | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    num = int(m.group(1))
    if m.group(2):
        num *= 1000
    return num


def _detect_must_have(text: str) -> list[str]:
    """Обязательные требования: цвет (по стему) и ключевые слова."""
    must: list[str] = []
    low = text.lower()
    for stem, name in _COLOR_STEMS.items():
        if stem in low:
            must.append(name)
    for kw in _KEYWORD_REQS:
        if kw in low:
            must.append("3D" if kw == "3d" else kw)
    seen: set[str] = set()
    out: list[str] = []
    for m in must:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _query_words(text: str) -> list[str]:
    return [w for w in text.lower().split() if len(w) > 2 and w not in _STOP_WORDS]


def _word_hits(title: str, traits: str, words: list[str]) -> int:
    hay = (title + " " + traits).lower()
    return sum(1 for w in words if w in hay)


class MockProvider:
    """Детерминированный оффлайн-провайдер (демо без ключа)."""

    async def complete(self, *, model: str, kind: str, prompt: str,
                       schema: Type[BaseModel],
                       images: list[str] | None = None) -> BaseModel:
        if kind == "constraints":
            return self._constraints(prompt)
        if kind == "vision":
            return self._vision(prompt)
        if kind == "review":
            return self._review(prompt)
        if kind == "rank":
            return self._rank(prompt)
        if kind == "arbiter":
            return self._arbiter(prompt)
        if kind == "freeform":
            return FreeformReply(reply="У меня пока нет личных данных о продавце. "
                                       "Могу искать товары, читать отзывы и сравнивать цены.",
                                 grounded=False)
        raise ProviderError(f"неизвестный kind: {kind}")

    # ---- извлечение ограничений (с контекстом прошлого поиска) ----

    def _constraints(self, prompt: str) -> SearchConstraints:
        user_text = _user_text(prompt)
        ctx = _context(prompt)

        prev_query, prev_max, prev_must = ctx
        low = user_text.lower()
        is_refinement = any(k in low for k in ("подешевле", "дешевле", "только",
                                               "без ", "ещё", "другой", "не дороже"))

        if is_refinement and prev_query:
            # уточнение применяется к прошлому поиску (PRD раздел 5)
            must = list(prev_must) + _detect_must_have(user_text)
            max_price = prev_max
            if any(k in low for k in ("подешевле", "дешевле", "не дороже")):
                max_price = int(prev_max * 0.8) if prev_max else _detect_price(user_text)
            return SearchConstraints(query=prev_query,
                                     must_have=_uniq(must),
                                     max_price=max_price)

        query = user_text[:200]
        return SearchConstraints(query=query,
                                 must_have=_detect_must_have(query),
                                 max_price=_detect_price(query))

    # ---- vision (демо: сценарий «кроссовки») ----

    def _vision(self, prompt: str) -> VisionDescription:
        return VisionDescription(
            category="обувь",
            gender="унисекс",
            color="белый",
            material="кожа/текстиль",
            silhouette="кроссовки",
            details=["белый"],
            search_queries=["кроссовки белые", "белые кеды", "кроссовки унисекс"],
        )

    # ---- анализ отзывов ----

    def _review(self, prompt: str) -> ReviewAnalysis:
        text = _strip_prompt(prompt)
        requirements = _requirements_from_prompt(text)
        if not requirements:
            requirements = _detect_must_have(text)
        product_id = "demo"
        m = re.search(r"ТОВАР:\s*(\S+)", text)
        if m:
            product_id = m.group(1)[:40]

        verdicts = []
        for req in requirements:
            mentions, quote = _scan_reviews_for(text, req)
            verdict = "no_data"
            if mentions > 0:
                verdict = "confirmed"
            elif mentions < 0:
                verdict = "rejected"
            verdicts.append({"requirement": req, "verdict": verdict,
                             "mentions": abs(mentions), "quote": quote})
        return ReviewAnalysis(
            product_market="ozon", product_id=product_id,
            pros=["Хорошие отзывы о качестве", "Соответствует описанию"],
            cons=["Встречаются замечания о размере"],
            verdicts=verdicts,
            summary="В целом покупатели довольны; детали см. в вердиктах.",
        )

    # ---- ранжирование кандидатов ----

    def _rank(self, prompt: str) -> RankResult:
        text = _strip_prompt(prompt)
        m = re.search(r"ЗАПРОС:\s*(.+?)(?:\s*\(требования|\n|$)", text)
        query = m.group(1).strip() if m else ""
        must = _detect_must_have(text)
        words = _query_words(query)

        scored: list[tuple[int, int, str]] = []
        for line in text.splitlines():
            lm = re.match(r"\s*(\d+):\s+(.+)", line)
            if not lm:
                continue
            idx, rest = int(lm.group(1)), lm.group(2)
            title = re.split(r"\s*\|\s*", rest)[0]
            score = 40 + 20 * _word_hits(title, "", words)
            for req in must:
                if req.lower() in title.lower():
                    score += 12
            scored.append((idx, max(5, min(98, score)), title))
        scored.sort(key=lambda x: -x[1])
        items = [{"index": i, "match_score": s,
                  "reason": "совпадение по запросу и признакам"}
                 for i, s, _ in scored[:3]]
        return RankResult(items=items)

    # ---- арбитр «один и тот же товар?» ----

    def _arbiter(self, prompt: str) -> ArbiterVerdict:
        text = _strip_prompt(prompt).lower()
        eans = re.findall(r"ean\s*[ab]?[:=]?\s*(\d{8,14})", text)
        same = bool(eans) and len(set(eans)) == 1
        if not same:
            a, b = _extract_titles(text)
            same = a and b and _similar(a, b) > 0.75
        return ArbiterVerdict(same=same, confidence=0.9 if same else 0.6,
                              reason="совпал штрихкод" if eans and same
                              else "похожие названия" if same
                              else "разные товары")


# ────────────────────────────── утилиты ───────────────────────────

def _strip_prompt(prompt: str) -> str:
    for marker in ("ПОЛЬЗОВАТЕЛЬ:", "ДАННЫЕ:", "ТОВАР:"):
        pos = prompt.find(marker)
        if pos != -1:
            return prompt[pos + len(marker):]
    return prompt


def _user_text(prompt: str) -> str:
    m = re.search(r"ПОЛЬЗОВАТЕЛЬ:\s*(.+)", prompt)
    if m:
        line = m.group(1).strip()
        if "КОНТЕКСТ ПРОШЛОГО ПОИСКА" in line:
            line = line.split("КОНТЕКСТ")[0].strip()
        return line
    return prompt.strip()[:200]


def _context(prompt: str) -> tuple[str, int | None, list[str]]:
    """Контекст прошлого поиска из промпта: (запрос, макс. цена, требования)."""
    m = re.search(r"запрос:\s*([^;]+);\s*требования:\s*\[?([^\]]*)\]?;\s*"
                  r"макс\. цена:\s*(\w+)", prompt)
    if not m:
        return "", None, []
    query = m.group(1).strip()
    reqs = [r.strip() for r in m.group(2).split(",") if r.strip()]
    raw_max = m.group(3).strip()
    max_price = int(raw_max) if raw_max.isdigit() else None
    return query, max_price, reqs


def _requirements_from_prompt(text: str) -> list[str]:
    """Требования из блока ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ (до ОТЗЫВЫ)."""
    m = re.search(r"ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ:\s*(.+?)(?:\nОТЗЫВЫ:|\n*$)", text, re.S)
    if not m:
        return []
    return [line.strip().lstrip("- ").strip()
            for line in m.group(1).splitlines() if line.strip()]


def _scan_reviews_for(block: str, req: str) -> tuple[int, str]:
    """Считает упоминания требования в отзывах (стем-матчинг по 5 символам).

    req — требование; совпадение считается по первым 5 символам токенов
    (чёрный ≈ чёрная ≈ чёрные). Отрицание («не прилегает») — только для
    однословных требований, не начинающихся с «не» (двухсловное «не давит»
    — это уже положительная формулировка).
    """
    req_low = req.lower().strip()
    negative_req = req_low.startswith("не ")
    # 4 символа: чёрный ≈ чёрная ≈ чёрные (5-й символ — окончание)
    stem = req_low.replace(" ", "")[:4]
    positive = negative = 0
    quote = ""
    review_lines = [ln for ln in block.splitlines()
                    if ln.strip().startswith("ОТЗЫВ")]
    for line in review_lines:
        parts = line.split(":", 1)
        if len(parts) < 2:
            continue
        body = parts[1].lower()
        hit = False
        if " " in req_low:
            hit = req_low in body
        else:
            tokens = re.findall(r"[а-яёa-z0-9]+", body)
            hit = any(len(t) > 4 and t[:4] == stem for t in tokens)
        if not hit:
            continue
        neg = False
        if not negative_req and len(req_low.split()) == 1:
            neg = any(w in body for w in (" не ", "нет ", "не ", "неудобн"))
        if neg:
            negative += 1
            if not quote:
                quote = parts[1].strip()[:160]
        else:
            positive += 1
            if not quote:
                quote = parts[1].strip()[:160]
    if positive:
        return positive, quote
    if negative:
        return -negative, quote
    return 0, ""


def _extract_titles(text: str) -> tuple[str, str]:
    a = re.search(r"(?:название|title)\s*[a]?[:=]\s*(.+)", text)
    b = re.search(r"(?:название|title)\s*[b]?[:=]\s*(.+)", text)
    return (a.group(1).strip() if a else "", b.group(1).strip() if b else "")


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
