"""Браузерный канал Wildberries (канал 1: реальные данные через браузер).

Публичные JSON-эндпоинты WB (search.wb.ru, card.wb.ru) с этого IP
блокируются (429/403) даже с имитацией Chrome через curl_cffi. Как и для
Яндекса/Ozon, рабочий путь — настоящий браузер (Playwright + системный
Chrome) через прокси: поисковая страница отдаёт полный DOM выдачи.

Карточки: article.product-card → ссылка a.product-card__link (название в
aria-label, href → /catalog/{id}/detail.aspx), цена в ins/span с классом
price (текст вида «489 ₽2 940 ₽−83%» — берём первое число до «₽»).

Если страница не загрузилась (сеть/блок) — честно возвращаем пустоту.
"""
from __future__ import annotations

import asyncio
import logging
import re

from models import Product, Review

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.wildberries.ru/catalog/0/search.aspx?search="

_PARSE_SCRIPT = r"""
() => {
    const out = [];
    document.querySelectorAll("article[class*='product-card']").forEach(card => {
        const a = card.querySelector("a[class*='product-card__link']");
        if (!a) return;
        const href = a.getAttribute('href') || '';
        const label = (a.getAttribute('aria-label') || a.getAttribute('title') || '').trim();
        const priceEl = card.querySelector("ins[class*='price'], span[class*='price']");
        const price = priceEl ? (priceEl.textContent || '').trim() : '';
        if (label && price && href) out.push({href, label, price});
    });
    return out.slice(0, LIMIT);
}
"""

# Цены приходят с тонкими/неразрывными пробелами (U+2009/U+00A0) — берём
# число до первого «₽» и удаляем все нецифровые символы.
_PRICE_RE = re.compile(r"([\d\u00a0\u2009 ]{1,})\s*₽")


class WbBrowserAdapter:
    """Канал 1 WB: настоящий браузер + прокси (по образцу yandex_browser)."""

    name = "wb"

    def __init__(self, proxy: str = "", pool=None,
                 chrome_path: str | None = None,
                 timeout: float = 40.0) -> None:
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
        self._browser = await self._pw.chromium.launch(
            executable_path=self._chrome_path,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        logger.info("WB-браузер запущен (прокси: %s)", self._proxy or "нет")

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
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()
        page.set_default_timeout(30000)
        return ctx, page

    async def search(self, query: str, limit: int = 5) -> list[Product]:
        """Поиск с ротацией прокси: пустой результат (блок IP) → retry."""
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
                logger.info("WB: прокси %s вернул пусто — пробую следующий",
                            proxy)
            return []
        return await self._search_once(query, limit)

    async def _search_once(self, query: str, limit: int = 5) -> list[Product]:
        ctx, page = await self._new_page()
        try:
            url = SEARCH_URL + query.replace(" ", "+")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(6):
                await asyncio.sleep(2.5)
                await page.mouse.wheel(0, 2500)
                if await page.query_selector("article[class*='product-card']"):
                    break
            raw = await page.evaluate(_PARSE_SCRIPT.replace("LIMIT", str(limit)))
            return self._cards_from_raw(raw)
        except Exception as exc:
            logger.warning("WB-браузер: сбой поиска %r: %s", query, exc)
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
            m = re.search(r"/catalog/(\d+)/", href)
            ext_id = m.group(1) if m else href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            out.append(Product(
                marketplace="wb", ext_id=ext_id,
                title=label[:120], price=price,
                url=("https://www.wildberries.ru" + href
                     if href.startswith("/") else href),
            ))
        return out

    @staticmethod
    def _parse_price(text: str) -> int | None:
        m = _PRICE_RE.search(text)
        if not m:
            return None
        digits = re.sub(r"\D", "", m.group(1))
        return int(digits) if digits else None

    # ── реальные карточка и отзывы (парсинг DOM карточки товара) ──

    _CARD_SCRIPT = r"""
    () => {
        const out = {title: '', brand: '', price: '', rating: '',
                     count: '', photo: '', traits: [], color: ''};
        const byCls = (sel) => {
            const el = document.querySelector(sel);
            return el ? (el.textContent || '').trim().replace(/\s+/g, ' ') : '';
        };
        const titleEl = document.querySelector("[class*='productImtName']");
        out.title = titleEl ? (titleEl.textContent || '').trim() : '';
        const brandEl = document.querySelector("[class*='productNameBrand']");
        out.brand = brandEl ? (brandEl.textContent || '').trim() : '';
        const priceEl = document.querySelector("[class*='productLinePriceWallet'], [class*='price__wallet'], ins[class*='price']");
        out.price = priceEl ? (priceEl.textContent || '').trim() : '';
        const ratingEl = document.querySelector("[class*='ratingNumber']");
        out.rating = ratingEl ? (ratingEl.textContent || '').trim() : '';
        const countEl = document.querySelector("[class*='reviewCount']");
        out.count = countEl ? (countEl.textContent || '').trim() : '';
        const colorEl = document.querySelector("[class*='colorValue']");
        out.color = colorEl ? (colorEl.textContent || '').trim() : '';
        const img = document.querySelector("a[class*='productLineImg'] img, [class*='gallery'] img[src*='wbbasket']");
        out.photo = img ? (img.getAttribute('src') || '') : '';
        document.querySelectorAll("th[class*='cellKey'], td[class*='cellKey']").forEach(th => {
            const tr = th.closest('tr');
            if (!tr) return;
            const td = tr.querySelector("td[class*='cellValue']");
            if (!td) return;
            const k = (th.textContent || '').trim().replace(/\s+/g, ' ');
            const v = (td.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
            if (k && v && k.length < 40 && k.length > 1) out.traits.push(k + ': ' + v);
        });
        return out;
    }
    """

    async def get_card(self, ext_id: str) -> Product | None:
        """Карточка: название/бренд/рейтинг/цена/фото/характеристики из DOM."""
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
            url = f"https://www.wildberries.ru/catalog/{ext_id}/detail.aspx"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(6):
                await asyncio.sleep(2.5)
                await page.mouse.wheel(0, 1500)
                if await page.query_selector("[class*='productImtName']"):
                    break
            raw = await page.evaluate(self._CARD_SCRIPT)
            title = (raw.get("title") or "").strip()
            price = self._parse_price(raw.get("price") or "")
            if not title or price is None:
                logger.warning("WB-карточка %s: неполные данные", ext_id)
                return None
            rating = None
            if raw.get("rating"):
                try:
                    rating = float((raw["rating"] or "").replace(",", "."))
                except ValueError:
                    rating = None
            reviews_count = 0
            if raw.get("count"):
                import re as _re
                m = _re.search(r"(\d[\d\u00a0 ]*)", raw["count"].replace(" ", ""))
                if m:
                    try:
                        reviews_count = int(_re.sub(r"\D", "", m.group(1)))
                    except ValueError:
                        reviews_count = 0
            brand = raw.get("brand") or ""
            title_full = f"{brand} {title}".strip()
            traits = [t for t in raw.get("traits") or [] if t]
            return Product(
                marketplace="wb", ext_id=ext_id,
                title=title_full[:120], price=price,
                url=url, rating=rating, reviews_count=reviews_count,
                photo_url=(raw.get("photo") or ""),
                brand=brand, traits=traits,
            )
        except Exception as exc:
            logger.warning("WB-карточка %s упала: %s", ext_id, exc)
            return None
        finally:
            await ctx.close()

    _REVIEWS_SCRIPT = r"""
    () => {
        const out = [];
        const seen = new Set();
        document.querySelectorAll("[class*='comment-card']").forEach(card => {
            const t = (card.innerText || '').trim().replace(/\s+/g, ' ');
            if (t.length < 30) return;
            const key = t.slice(0, 60);
            if (seen.has(key)) return;
            seen.add(key);
            let rating = 0;
            const stars = card.querySelector("[class*='stars-line']");
            if (stars) {
                const m = (stars.className || '').match(/star(\d)/);
                if (m) rating = parseInt(m[1]);
            }
            const header = card.querySelector("[class*='comment-card__header']");
            const head = header ? (header.innerText || '').trim().replace(/\s+/g, ' ') : '';
            let author = head, date = '';
            const dm = head.match(/(\d+\s+[а-яёa-z]+)/i);
            if (dm && dm.index > 0) {
                author = head.slice(0, dm.index).trim();
                date = dm[1];
            } else {
                // «Покупатель 09 августа» — имя без даты в начале
                const m2 = head.match(/^(.+?)\s+(\d{1,2}\s+[а-яё]+)/i);
                if (m2) { author = m2[1]; date = m2[2]; }
            }
            let pros = '', cons = '', text = t;
            // вырезаем шапку (имя + дата) из текста
            if (head && t.startsWith(head)) text = t.slice(head.length).trim();
            const pi = text.indexOf('Плюсы товара');
            const ni = text.indexOf('Минусы товара');
            const di = text.indexOf('Достоинства');
            const ngi = text.indexOf('Недостатки');
            if (pi >= 0 && ni > pi) {
                pros = text.slice(pi + 'Плюсы товара'.length, ni).trim();
                cons = text.slice(ni + 'Минусы товара'.length).trim();
                text = text.slice(0, pi).trim();
            } else if (pi >= 0) {
                pros = text.slice(pi + 'Плюсы товара'.length).trim();
                text = text.slice(0, pi).trim();
            } else if (ni >= 0) {
                cons = text.slice(ni + 'Минусы товара'.length).trim();
                text = text.slice(0, ni).trim();
            } else if (di >= 0) {
                pros = text.slice(di + 'Достоинства'.length,
                                  ngi > di ? ngi : undefined).trim();
                if (ngi > di) cons = text.slice(ngi + 'Недостатки'.length).trim();
                text = text.slice(0, di).trim();
            }
            out.push({text: text.slice(0, 1500), rating, author,
                      date, pros: pros.slice(0, 300), cons: cons.slice(0, 300)});
        });
        return out.slice(0, LIMIT);
    }
    """

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list[Review]:
        """Отзывы: .comment-card (автор/дата в header, рейтинг в stars-line,
        плюсы/минусы в тексте)."""
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
            url = f"https://www.wildberries.ru/catalog/{ext_id}/detail.aspx"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(8):
                await asyncio.sleep(2)
                await page.mouse.wheel(0, 3000)
                n = await page.eval_on_selector_all(
                    "[class*='comment-card']", "els => els.length")
                if n > 2:
                    break
            await page.wait_for_timeout(1500)
            raw = await page.evaluate(
                self._REVIEWS_SCRIPT.replace("LIMIT", str(limit)))
            reviews: list[Review] = []
            for i, r in enumerate(raw):
                reviews.append(Review(
                    product_market="wb", product_id=ext_id,
                    review_id=str(i),
                    rating=int(r.get("rating") or 0),
                    author=(r.get("author") or "")[:40],
                    date=(r.get("date") or ""),
                    text=(r.get("text") or ""),
                    pros=(r.get("pros") or ""),
                    cons=(r.get("cons") or ""),
                ))
            return reviews
        except Exception as exc:
            logger.warning("WB-отзывы %s не получены: %s", ext_id, exc)
            return []
        finally:
            await ctx.close()

    async def get_photos(self, ext_id: str) -> list[str]:
        return []
