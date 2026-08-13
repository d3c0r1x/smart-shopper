"""search/structfilter.py — детерминированная структурная фильтрация (ТЗ §2).

В гибридной архитектуре поиска LLM извлекает свободные требования
(must_have / nice_to_have), а этот модуль вытаскивает жёсткие структурные
атрибуты — ценовой диапазон, бренд, минимальный рейтинг — детерминированно,
регулярными выражениями, без сети и без расхода бюджета LLM.

Роли модуля:
  1) закрывает пропуски LLM (модель могла не вытащить «до 3000», бренд,
     рейтинг) — работает всегда, даже при исчерпанном дневном лимите;
  2) очищает запрос от ценовых/рейтинговых фраз, чтобы поисковый эндпоинт
     маркетплейса получал чистый текст («наушники до 3000 рублей» →
     запрос «наушники», max_price=3000).

Принципы надёжности:
  * фрагмент вырезается из запроса ТОЛЬКО если число успешно распознано
    (в т.ч. прописью с родительными формами «до пяти тысяч»);
  * спаны обрезаются ровно по концу числа + валюте, не захватывая соседние
    значимые слова («чёрная», «беспроводные»);
  * невалидные совпадения («до» перед словом, «от производителя»,
    «не дороже» для нижней границы) запрос не трогают.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

# Числа прописью: именительный и родительный падежи (до тысячи),
# плюс разряды тысяч. Для «до пяти тысяч», «не дороже трёхсот рублей».
_RU_NUM = {
    "один": 1, "одна": 1, "одного": 1, "одной": 1,
    "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4, "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8,
    "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
    "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12,
    "двенадцати": 12, "тринадцать": 13, "тринадцати": 13,
    "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15,
    "пятнадцати": 15, "шестнадцать": 16, "шестнадцати": 16,
    "семнадцать": 17, "семнадцати": 17, "восемнадцать": 18,
    "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
    "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30,
    "сорок": 40, "сорока": 40, "пятьдесят": 50, "пятидесяти": 50,
    "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70,
    "семидесяти": 70, "восемьдесят": 80, "восьмидесяти": 80,
    "девяносто": 90, "девяноста": 90, "сто": 100, "ста": 100,
    "двести": 200, "двухсот": 200, "триста": 300, "трёхсот": 300,
    "четыреста": 400, "четырёхсот": 400, "пятьсот": 500, "пятисот": 500,
    "шестьсот": 600, "шестисот": 600, "семьсот": 700, "семисот": 700,
    "восемьсот": 800, "восьмисот": 800, "девятьсот": 900, "девятисот": 900,
}
_THOUSAND = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000}

# Известные бренды для детерминированного слоя (расширяемый словарь).
# Покрывает типичные запросы вида «наушники Sony» / «бренд X».
_KNOWN_BRANDS = {
    "samsung", "apple", "iphone", "xiaomi", "honor", "huawei", "sony",
    "lg", "bosch", "philips", "dyson", "nike", "adidas", "puma",
    "reebok", "asics", "anker", "logitech", "hp", "lenovo", "asus",
    "acer", "msi", "gigabyte", "canon", "nikon", "panasonic", "jbl",
    "sennheiser", "bose", "braun", "rowenta", "tefal", "vitek",
    "redmond", "polaris", "kitfort", "hansa", "electrolux", "indesit",
    "beko", "gorenje", "supra", "vivo", "oppo", "realme", "oneplus",
    "motorola", "nokia", "google", "microsoft", "intel", "amd", "nvidia",
}

_MONEY = r"(?:руб(?:лей|ля|ль|лях)?|₽|р\.|р\b)"
# Цифры с разделителями ИЛИ прописью (до «девятьсот девяносто девять тысяч»).
_NUM_TOKEN = r"\d[\d\s\u00a0\u202f]{0,8}\d|\d+|[а-яё]+"
# Денежное выражение: число (цифры или слова) + необязательная валюта.
# (?:…) вокруг {_NUM_TOKEN} — иначе альтернации | вырываются из повторения
# \s+ и многословные числа не расширяются.
_MONEY_EXPR = rf"(?P<n>(?:{_NUM_TOKEN})(?:\s+(?:{_NUM_TOKEN})){{0,3}})\s*{_MONEY}?"

# Шаблоны «верхней» границы цены.
_MAX_RE = re.compile(
    rf"(?:не\s+дороже|не\s+дорожe|не\s+больше|не\s+выше|до|меньше|"
    rf"в\s+пределах|максимум|макс)\s+{_MONEY_EXPR}")
# Шаблоны «нижней» границы цены.
_MIN_RE = re.compile(
    rf"(?:не\s+дешевле|не\s+меньше|не\s+ниже|от|дороже|больше)\s+{_MONEY_EXPR}")
# Явный диапазон: «от 1000 до 2000 рублей», «1000–2000 руб».
_RANGE_RE = re.compile(
    rf"от\s+(?P<a>(?:{_NUM_TOKEN})(?:\s+(?:{_NUM_TOKEN})){{0,3}})\s+до\s+"
    rf"(?P<b>(?:{_NUM_TOKEN})(?:\s+(?:{_NUM_TOKEN})){{0,3}})\s*{_MONEY}?")
_RANGE_DASH_RE = re.compile(
    rf"(?P<a>\d[\d\s\u00a0\u202f]{{0,8}}\d|\d+)\s*[-–—]\s*"
    rf"(?P<b>\d[\d\s\u00a0\u202f]{{0,8}}\d|\d+)\s*{_MONEY}?")

# Рейтинг: «рейтинг от 4.5», «рейтингом не ниже 4», «от 4,5 звёзд», «4.5+».
_RATING_RE = re.compile(
    r"рейтинг(?:ом)?\s+(?:не\s+ниже|не\s+меньше|от|выше|больше|"
    r"минимум)?\s*(?P<r>\d[\d,\.]*)")
_RATING_STAR_RE = re.compile(
    r"(?P<r>\d[\d,\.]*)\s*\+?\s*(?:из\s+5\s+)?звёзд?|звезд|звезды|звёзды")

# Бренд: явное «бренд/марка/фирма X».
_BRAND_EXPLICIT_RE = re.compile(
    r"(?:бренд|марка|фирма|производитель)\s+"
    r"(?P<b>[a-zа-яё0-9][a-zа-яё0-9\- ]{0,32})", re.IGNORECASE)

# Валюта сразу после числа (для обрезки спана).
_MONEY_AT = re.compile(r"\s*" + _MONEY)

# Мусорные хвосты после вырезания ценовых фраз («до», «от», «макс»…).
_TAIL_TRASH = re.compile(r"\s*(?:до|от|максимум|макс|не|дороже|дешевле|больше|меньше)\s*$")


class StructFilters(BaseModel):
    """Жёсткие структурные атрибуты запроса (детерминированный слой)."""

    query: str = Field(default="", description="запрос без ценовых/рейтинговых фраз")
    max_price: int | None = None
    min_price: int | None = None
    min_rating: float | None = None
    brand: str | None = None


# ── парсер чисел ──────────────────────────────────────────────────

def _digits_to_int(raw: str) -> int | None:
    """'3 000' / '3000' / '4.5' → 3000 / 3000 / None (нецелое)."""
    cleaned = re.sub(r"[\s\u00a0\u202f]", "", raw).replace(",", ".")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _words_to_int(raw: str) -> int | None:
    """'двадцать пять тысяч' → 25000; не число — None."""
    total, acc = 0, 0
    for w in raw.split():
        if w in _RU_NUM:
            acc += _RU_NUM[w]
        elif w in _THOUSAND:
            total += (acc or 1) * _THOUSAND[w]
            acc = 0
        else:
            return None
    return total + acc


def _to_int(raw: str) -> int | None:
    raw = raw.strip().lower()
    num = _digits_to_int(raw)
    if num is not None:
        return num
    words = raw.replace("-", " ")
    if all(w in _RU_NUM or w in _THOUSAND for w in words.split()):
        return _words_to_int(words)
    return None


def _number_prefix_len(raw: str) -> int:
    """Длина самого длинного префикса фразы, распознаваемого как число."""
    tokens = raw.strip().split()
    for i in range(len(tokens), 0, -1):
        if _to_int(" ".join(tokens[:i])) is not None:
            return len(" ".join(tokens[:i]))
    return 0


def _trim_span(lowered: str, m) -> tuple[int, int] | None:
    """Спан «предлог + число (+ валюта)» без лишних слов после.

    Регекс жадный и может захватить в число соседние слова («до 1000 рублей
    чёрная»); здесь спана обрезается ровно по концу числа + валюте.
    """
    n_len = _number_prefix_len(m.group("n"))
    if n_len == 0:
        return None
    end = m.start("n") + n_len
    money = _MONEY_AT.match(lowered[end:])
    if money:
        end += money.end()
    return (m.start(), end)


def _best_number(raw: str) -> int | None:
    """'пять тысяч рублей' → 5000 (число + отброшенный хвост)."""
    tokens = raw.strip().split()
    for i in range(len(tokens), 0, -1):
        val = _to_int(" ".join(tokens[:i]))
        if val is not None:
            return val
    return None


def _to_float(raw: str) -> float | None:
    cleaned = raw.strip().replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_query(text: str, spans: list[tuple[int, int]]) -> str:
    """Вырезает найденные ценовые/рейтинговые фрагменты из запроса."""
    out = []
    pos = 0
    for start, end in sorted(spans):
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    cleaned = " ".join(" ".join(out).split())
    cleaned = _TAIL_TRASH.sub("", cleaned).strip()
    return cleaned


# ── основной парсер ──────────────────────────────────────────────

def parse_structural(user_text: str) -> StructFilters:
    """Извлекает структурные атрибуты из свободного текста запроса.

    Никогда не бросает исключений: любой сбой парсера — пустой результат
    (запрос остаётся как есть). Чистая функция — легко тестируется.
    """
    text = (user_text or "").strip()
    if not text:
        return StructFilters(query=text)
    lowered = text.lower()
    spans: list[tuple[int, int]] = []
    filters = StructFilters(query=text)

    # 1) ценовой диапазон (приоритет над одиночными границами)
    for m in _RANGE_RE.finditer(lowered):
        a, b = _best_number(m.group("a")), _best_number(m.group("b"))
        if a is None or b is None or a > b:
            continue
        filters.min_price = a
        filters.max_price = b
        # спана покрывает «от … до …» с обрезкой по концу b
        b_len = _number_prefix_len(m.group("b"))
        end = m.start("b") + b_len
        money = _MONEY_AT.match(lowered[end:])
        if money:
            end += money.end()
        spans.append((m.start(), end))
    for m in _RANGE_DASH_RE.finditer(lowered):
        a, b = _best_number(m.group("a")), _best_number(m.group("b"))
        if (a is None or b is None or a > b
                or filters.max_price is not None or filters.min_price is not None):
            continue
        filters.min_price = a
        filters.max_price = b
        b_len = _number_prefix_len(m.group("b"))
        end = m.start("b") + b_len
        money = _MONEY_AT.match(lowered[end:])
        if money:
            end += money.end()
        spans.append((m.start(), end))

    # 2) одиночные границы (если диапазон не найден)
    if filters.max_price is None:
        for m in _MAX_RE.finditer(lowered):
            val = _best_number(m.group("n"))
            if val is None:
                continue
            span = _trim_span(lowered, m)
            if span is None:
                continue
            if filters.max_price is None or val < filters.max_price:
                filters.max_price = val
            spans.append(span)
    if filters.min_price is None:
        for m in _MIN_RE.finditer(lowered):
            # «не дороже» / «не больше» содержит «дороже»/«больше» —
            # это ВЕРХНЯЯ граница, не нижняя: пропускаем такие совпадения
            if lowered[max(0, m.start() - 3):m.start()] == "не ":
                continue
            val = _best_number(m.group("n"))
            if val is None:
                continue
            span = _trim_span(lowered, m)
            if span is None:
                continue
            if filters.min_price is None or val > filters.min_price:
                filters.min_price = val
            spans.append(span)

    # 3) рейтинг
    rating = None
    for m in _RATING_RE.finditer(lowered):
        rating = _to_float(m.group("r"))
        if rating is not None:
            spans.append(m.span())
            break
    if rating is None:
        for m in _RATING_STAR_RE.finditer(lowered):
            rating = _to_float(m.group("r"))
            if rating is not None:
                spans.append(m.span())
                break
    filters.min_rating = rating

    # 4) бренд: явный «бренд X» → иначе известный бренд словом
    brand = None
    m = _BRAND_EXPLICIT_RE.search(text)
    if m:
        brand = m.group("b").strip().split()[0].strip(":-").lower()
    else:
        for w in re.findall(r"[a-zа-яё0-9]+", lowered):
            if w in _KNOWN_BRANDS:
                brand = w
                break
    filters.brand = brand

    # 5) очистка запроса (только по валидным спанам)
    if spans:
        filters.query = _clean_query(text, spans)
    return filters


def apply_structural(products, filters: StructFilters) -> list:
    """Жёсткие фильтры по структурным атрибутам (для тестов/самостоятельно).

    В боевом контуре используется _prefilter оркестратора; эта функция —
    независимый фильтр, которым удобно пользоваться в eval-скриптах.
    Товар без данных по атрибуту не отбрасывается (не можем проверить).
    """
    out = []
    for p in products:
        if filters.max_price is not None and p.price and p.price > filters.max_price:
            continue
        if filters.min_price is not None and p.price and p.price < filters.min_price:
            continue
        if filters.min_rating is not None and p.rating is not None \
                and p.rating < filters.min_rating:
            continue
        if filters.brand and p.brand \
                and filters.brand.lower() not in p.brand.lower():
            continue
        out.append(p)
    return out
