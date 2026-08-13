"""Браузерный канал Ozon (канал 1: реальные данные через настоящий браузер).

С этого IP (засвеченного в антиботах) обычные HTTP-запросы к Ozon
блокируются: web composer-api → 403-челлендж, mobile api.ozon.ru → 307
(регион-блок). Настоящий браузер (Playwright + системный Chrome) через
прокси проходит: Antibot Challenge Page решается скриптом в течение
~15–25 секунд, после чего выдача с реальными товарами отрисовывается в
DOM. Этот адаптер ждёт решения челленджа и парсит карточки из DOM.

Названия: на каждый товар в выдаче две ссылки /product/ (бейдж «Распродажа»
и название) — группируем по href и берём самую длинную подпись без
промо-слов. Цена: ищем элемент вида «1 234 ₽» в контейнере ссылки.

Если челлендж не решается (прокси недоступен / IP в блок-листе) —
честно возвращаем пустоту, без падений (fallback на демо-каталог).
"""
from __future__ import annotations

import asyncio
import logging
import re

from models import Product, Review

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.ozon.ru/search/?text="

_PROMO_WORDS = ("распродажа", "вау-цены", "скидка", "выгода", "акция",
                "суперцена", "хит", "промокод")

_PARSE_SCRIPT = r"""
() => {
    const BAD = ['распродажа','вау-цены','скидка','выгода','акция','суперцена','хит','промокод'];
    const byHref = {};
    for (const e of document.querySelectorAll("a[href*='/product/']")) {
        const href = e.getAttribute('href') || '';
        if (!href) continue;
        const al = (e.getAttribute('aria-label') || '').trim();
        const tx = (e.textContent || '').trim();
        const cands = [al, tx].filter(t => t.length > 8);
        if (!byHref[href]) byHref[href] = {href, labels: [], node: e};
        byHref[href].labels.push(...cands);
    }
    const out = [];
    for (const k in byHref) {
        const g = byHref[k];
        const labels = g.labels.filter(t => !BAD.some(b => t.toLowerCase().startsWith(b)));
        const label = labels.sort((a,b) => b.length - a.length)[0] || g.labels[0] || '';
        let price = '';
        let node = g.node;
        for (let i = 0; i < 7 && node; i++) {
            const sp = [...(node.querySelectorAll ? node.querySelectorAll('span,div,b') : [])]
                .map(s => (s.textContent||'').trim())
                .filter(t => t.length < 20 && /^\d[\d\s\u00a0]*\s?₽$/.test(t))[0];
            if (sp) { price = sp; break; }
            node = node.parentElement;
        }
        out.push({label, price, href: g.href});
    }
    return out.slice(0, LIMIT);
}
"""

# Цены приходят с тонкими/неразрывными пробелами (U+2009/U+00A0) — берём
# число до первого «₽» и удаляем все нецифровые символы.
_PRICE_RE = re.compile(r"([\d\u00a0\u2009 ]{1,})\s*₽")


class OzonBrowserAdapter:
    """Канал 1 Ozon: настоящий браузер + прокси, ожидание решения челленджа."""

    name = "ozon"

    def __init__(self, proxy: str = "", pool=None,
                 chrome_path: str | None = None,
                 timeout: float = 45.0) -> None:
        self._proxy = proxy
        self._pool = pool
        self._chrome_path = chrome_path or (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        self._timeout = timeout
        self._pw = None
        self._browser = None

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        # Челлендж Ozon не решается в headless и при запуске через
        # executable_path (эмпирически) — нужен настоящий Chrome через
        # channel="chrome" с видимым окном; иначе фолбэк на путь к браузеру.
        try:
            self._browser = await self._pw.chromium.launch(
                channel="chrome", headless=False,
                args=["--disable-blink-features=AutomationControlled"])
        except Exception:
            self._browser = await self._pw.chromium.launch(
                executable_path=self._chrome_path, headless=False,
                args=["--disable-blink-features=AutomationControlled"])
        logger.info("Ozon-браузер запущен (прокси: %s)", self._proxy or "нет")

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def _new_page(self):
        await self._ensure_browser()
        ctx_opts = dict(
            viewport={"width": 1600, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="ru-RU")
        if self._proxy:
            ctx_opts["proxy"] = {"server": self._proxy}
        ctx = await self._browser.new_context(**ctx_opts)
        # ВАЖНО: без init-скрипта navigator.webdriver! Ozon детектирует
        # подмену свойства и не решает Antibot Challenge (проверено: с
        # init-скриптом челлендж не решается, без него — за ~5 с).
        page = await ctx.new_page()
        page.set_default_timeout(30000)
        return ctx, page

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        """Поиск с ротацией прокси: пустой результат (блок IP) → retry.

        Использует пул подписки Happ (proxy_pool): каждый сервер — свой
        выходной IP; если челлендж не решился на одном — пробуем другой.
        """
        from proxy_pool import get_pool
        pool = self._pool or get_pool()
        if pool is not None:
            proxies = pool.proxies()
            tried: set[str] = set()
            for _ in range(min(3, max(len(proxies), 1))):
                proxy = pool.next()
                if proxy in tried:
                    break
                tried.add(proxy)
                self._proxy = proxy
                items = await self._search_once(query, limit)
                if items:
                    return items
                logger.info("Ozon: прокси %s вернул пусто — пробую следующий",
                            proxy)
            return []
        return await self._search_once(query, limit)

    async def _search_once(self, query: str, limit: int = 5) -> list[Product]:
        """Ждёт решения Antibot Challenge (до ~40 с), затем парсит выдачу.

        Челлендж решается скриптом страницы за 15–25 с (иногда дольше —
        флапает), надёжный признак готовности — появление ссылок на
        товары a[href*='/product/'], а не title. Ожидание ограничено 40 с,
        чтобы полный поиск по трём площадкам укладывался в лимит
        HTTP-туннеля (~100 с); при несрабатывании челленджа Ozon честно
        пропускается.
        """
        ctx, page = await self._new_page()
        try:
            url = SEARCH_URL + query.replace(" ", "+")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(8):  # до ~40 с
                await asyncio.sleep(5)
                try:
                    n = await page.eval_on_selector_all(
                        "a[href*='/product/']", "els => els.length")
                except Exception:
                    n = 0
                if n > 5:
                    await asyncio.sleep(2)  # дождаться полной отрисовки карточек
                    raw = await page.evaluate(
                        _PARSE_SCRIPT.replace("LIMIT", str(limit)))
                    return self._cards_from_raw(raw)
            logger.warning("Ozon-браузер: челлендж не решён за отведённое время")
            return []
        except Exception as exc:
            logger.warning("Ozon-браузер: сбой поиска %r: %s", query, exc)
            return []
        finally:
            await ctx.close()

    def _cards_from_raw(self, raw: list[dict]) -> list[Product]:
        out: list[Product] = []
        for r in raw:
            label = (r.get("label") or "").strip()
            price = self._parse_price(r.get("price") or "")
            href = r.get("href") or ""
            if not label or price is None or not href:
                continue
            path = href.split("?", 1)[0].rstrip("/")
            ext_id = path.rsplit("/", 1)[-1]
            out.append(Product(
                marketplace="ozon", ext_id=ext_id,
                title=label[:120], price=price,
                url=("https://www.ozon.ru" + href if href.startswith("/") else href),
            ))
        return out

    @staticmethod
    def _parse_price(text: str) -> int | None:
        m = _PRICE_RE.search(text)
        if not m:
            return None
        digits = re.sub(r"\D", "", m.group(1))
        return int(digits) if digits else None

    # ── реальные карточка и отзывы (канал 1: entrypoint JSON в браузере) ──

    async def _fetch_entrypoint(self, page, target_url: str) -> dict:
        """Вызов entrypoint-api.bx/page/json/v2 изнутри браузера (с cookies).

        Тот же эндпоинт, что вызывает сам сайт: отдаёт widgetStates с
        характеристиками (webCharacteristics), списком отзывов
        (webListReviews) и рейтингом (webReviewProductScore).
        """
        from urllib.parse import quote
        ep = ("https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url="
              + quote(target_url, safe=""))
        try:
            res = await page.evaluate(
                """async (u) => {
                    const r = await fetch(u, {headers: {'accept': 'application/json'}});
                    const t = await r.text();
                    return {status: r.status, body: t};
                }""", ep)
            if res["status"] != 200:
                logger.warning("Ozon entrypoint %s: HTTP %s", target_url[:60],
                               res["status"])
                return {}
            import json
            return json.loads(res["body"])
        except Exception as exc:
            logger.warning("Ozon entrypoint %s не получен: %s",
                           target_url[:60], exc)
            return {}

    def _widget(self, data: dict, prefix: str) -> dict:
        for k, v in data.get("widgetStates", {}).items():
            if k.startswith(prefix):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    return {}
        return {}

    async def get_card(self, ext_id: str) -> Product | None:
        """Карточка: характеристики (webCharacteristics), цена/фото из DOM,
        рейтинг и число отзывов (webReviewProductScore)."""
        from proxy_pool import get_pool
        pool = self._pool or get_pool()
        proxies = pool.proxies() if pool is not None else []
        tried: set[str] = set()
        for _ in range(min(3, max(len(proxies), 1))):
            if pool is not None:
                proxy = pool.next()
                if proxy in tried:
                    break
                tried.add(proxy)
                self._proxy = proxy
            card = await self._card_once(ext_id)
            if card is not None:
                return card
        return None

    async def _card_once(self, ext_id: str) -> Product | None:
        ctx, page = await self._new_page()
        try:
            url = f"https://www.ozon.ru/product/{ext_id}/"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            ok = False
            for _ in range(8):  # до ~40 с на челлендж
                await asyncio.sleep(5)
                try:
                    t = await page.title()
                except Exception:
                    t = ""
                if "ozon" in t.lower() and "antibot" not in t.lower():
                    ok = True
                    break
            if not ok:
                logger.warning("Ozon-карточка %s: челлендж не решён", ext_id)
                return None
            await asyncio.sleep(2)
            data = await self._fetch_entrypoint(page, url)
            if not data:
                return None
            traits = self._traits_from_entry(data)
            # цена: первый «N ₽» в DOM (как ищет сам адаптер поиска)
            price = None
            try:
                price_text = await page.eval_on_selector_all(
                    "body *",
                    r"""els => els.map(e => (e.textContent||'').trim())
                               .filter(t => t.length < 25
                                            && /^[\d\u00a0\u2009 ]{2,}\s*₽/.test(t))
                               .slice(0, 8)""")
                for t in price_text:
                    p = self._parse_price(t)
                    if p and p > 10:
                        price = p
                        break
            except Exception:
                pass
            # фото: первая картинка товара (мультимедиа CDN)
            photo = ""
            try:
                photos = await page.eval_on_selector_all(
                    "img[src*='multimedia'], img[src*='s3/multimedia']",
                    "els => els.map(e => e.src).filter(s => s && s.length > 40).slice(0, 3)")
                if photos:
                    photo = photos[0]
            except Exception:
                pass
            # заголовок из DOM (h1) или entrypoint seo
            title = ""
            try:
                title = (await page.eval_on_selector(
                    "h1", "e => (e.textContent||'').trim()")).strip()
            except Exception:
                pass
            if not title:
                seo = data.get("seo", {})
                title = (seo.get("title") or "").split(" купить")[0]
            # рейтинг и число отзывов — со страницы отзывов
            rating = None
            reviews_count = 0
            rev_data = await self._fetch_entrypoint(
                page, f"https://www.ozon.ru/product/{ext_id}/reviews/")
            score = self._widget(rev_data, "webReviewProductScore")
            if score:
                rating = score.get("totalScore")
                reviews_count = int(score.get("reviewsCount") or 0)
            if not title or price is None:
                logger.warning("Ozon-карточка %s: неполные данные", ext_id)
                return None
            return Product(
                marketplace="ozon", ext_id=ext_id,
                title=title[:120], price=price,
                url=f"https://www.ozon.ru/product/{ext_id}/",
                rating=rating, reviews_count=reviews_count,
                photo_url=photo, traits=traits,
            )
        except Exception as exc:
            logger.warning("Ozon-карточка %s упала: %s", ext_id, exc)
            return None
        finally:
            await ctx.close()

    @staticmethod
    def _traits_from_entry(data: dict) -> list[str]:
        """Характеристики из widgetStates (webCharacteristics / webShortCharacteristics).

        Две структуры: классическая (webCharacteristics: group.short[name,
        values[].text]) и компактная карточки (webShortCharacteristics:
        characteristics[title.textRs / values[].text]).
        """
        out: list[str] = []
        for k, v in data.get("widgetStates", {}).items():
            if not (k.startswith("webCharacteristics")
                    or k.startswith("webShortCharacteristics")):
                continue
            try:
                import json
                obj = json.loads(v)
            except Exception:
                continue
            for group in obj.get("characteristics", []) or []:
                # классический формат: group.short[name, values[].text]
                short = group.get("short") if isinstance(group, dict) else None
                if short:
                    for ch in short:
                        name = ch.get("name", "")
                        values = [x.get("text", "") for x in ch.get("values", [])]
                        if name and values:
                            out.append(f"{name}: {', '.join(values)}")
                    continue
                # компактный формат: title.textRs[].content, values[].text
                if isinstance(group, dict):
                    title = group.get("title") or {}
                    trs = title.get("textRs") if isinstance(title, dict) else None
                    name = ""
                    if trs:
                        name = " ".join(t.get("content", "")
                                        for t in trs if t.get("content"))
                    values = [x.get("text", "").rstrip(", ")
                              for x in group.get("values", []) if x.get("text")]
                    if name and values:
                        out.append(f"{name}: {', '.join(values)}")
        return out

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list[Review]:
        """Отзывы из widgetStates.webListReviews (30 на страницу).

        Дата — Unix-время publishedAt; текст — content.comment, плюсы/минусы
        — content.positive/negative, оценка — content.score, признак покупки
        — isItemPurchased.
        """
        from proxy_pool import get_pool
        pool = self._pool or get_pool()
        proxies = pool.proxies() if pool is not None else []
        tried: set[str] = set()
        for _ in range(min(3, max(len(proxies), 1))):
            if pool is not None:
                proxy = pool.next()
                if proxy in tried:
                    break
                tried.add(proxy)
                self._proxy = proxy
            reviews = await self._reviews_once(ext_id, limit)
            if reviews:
                return reviews
        return []

    async def _reviews_once(self, ext_id: str,
                            limit: int = 20) -> list[Review]:
        ctx, page = await self._new_page()
        try:
            url = f"https://www.ozon.ru/product/{ext_id}/"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            ok = False
            for _ in range(8):
                await asyncio.sleep(5)
                try:
                    t = await page.title()
                except Exception:
                    t = ""
                if "ozon" in t.lower() and "antibot" not in t.lower():
                    ok = True
                    break
            if not ok:
                logger.warning("Ozon-отзывы %s: челлендж не решён", ext_id)
                return []
            data = await self._fetch_entrypoint(
                page, f"https://www.ozon.ru/product/{ext_id}/reviews/")
            lst = self._widget(data, "webListReviews")
            raw = lst.get("reviews") or []
            reviews: list[Review] = []
            import datetime
            for r in raw:
                if len(reviews) >= limit:
                    break
                content = r.get("content") or {}
                text = (content.get("comment") or "").strip()
                if not text:
                    continue
                ts = r.get("publishedAt") or r.get("createdAt")
                date = ""
                if ts:
                    try:
                        date = datetime.datetime.fromtimestamp(
                            ts, tz=datetime.timezone.utc
                        ).strftime("%d.%m.%Y")
                    except Exception:
                        date = ""
                reviews.append(Review(
                    product_market="ozon", product_id=ext_id,
                    review_id=str(r.get("uuid") or len(reviews)),
                    rating=int(content.get("score") or 0),
                    author=(r.get("author") or {}).get("firstName", ""),
                    date=date,
                    text=text[:1500],
                    pros=(content.get("positive") or "").strip()[:300],
                    cons=(content.get("negative") or "").strip()[:300],
                    photos=[p.get("url") or p.get("src") or ""
                            for p in (content.get("photos") or [])][:3],
                    bought_here=bool(r.get("isItemPurchased")),
                ))
            return reviews
        except Exception as exc:
            logger.warning("Ozon-отзывы %s не получены: %s", ext_id, exc)
            return []
        finally:
            await ctx.close()

    async def get_photos(self, ext_id: str) -> list[str]:
        return []
