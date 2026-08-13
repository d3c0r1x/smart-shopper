"""Браузерный канал Яндекс Маркета (канал 1, работающий с чистого IP).

Web-версия market.yandex.ru защищена антиботом, но с «чистого» IP (или
через прокси, например системный xray 127.0.0.1:10809) настоящий браузер
(Playwright + системный Chrome) получает реальные данные: товары лежат в
SSR/DOM (виджеты @marketfront/SnippetConstructor), а не в JSON-XHR —
поэтому вместо эмуляции HTTP используется настоящий браузер.

Процедура фиксации (PRD §3) реализована в tools/capture_endpoints.py;
этот адаптер работает автономно: открывает поиск, скроллит, парсит DOM.
"""
from __future__ import annotations

import asyncio
import logging
import re

from models import Product, Review

logger = logging.getLogger(__name__)

SEARCH_URL = "https://market.yandex.ru/search?text="

_PRICE_RE = re.compile(r"(\d[\d\u00a0 ]{2,})\s*₽")


def _parse_price(text: str) -> int | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    return int(m.group(1).replace("\u00a0", "").replace(" ", ""))


class YandexBrowserAdapter:
    """Канал 1 Яндекса: настоящий браузер + прокси (xray/Happ)."""

    name = "yandex"

    def __init__(self, proxy: str = "", chrome_path: str | None = None,
                 timeout: float = 40.0) -> None:
        self._proxy = proxy
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
        logger.info("YM-браузер запущен (прокси: %s)", self._proxy or "нет")

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

    # ── поиск ─────────────────────────────────────────────────────
    async def search(self, query: str, limit: int = 5) -> list[Product]:
        ctx, page = await self._new_page()
        try:
            url = SEARCH_URL + query.replace(" ", "+")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(6):
                await asyncio.sleep(2.5)
                await page.mouse.wheel(0, 2500)
                if await page.query_selector("a[href*='/card/']"):
                    break
            return await self._parse_cards(page, limit)
        except Exception as exc:
            logger.warning("YM-браузер: сбой поиска %r: %s", query, exc)
            return []
        finally:
            await ctx.close()

    async def _parse_cards(self, page, limit: int) -> list[Product]:
        script = """
        () => {
            const out = [];
            const seen = new Set();
            const blocks = document.querySelectorAll(
                '[data-baobab-name*="Snippet"], [data-baobab-name*="snippet"]');
            blocks.forEach(b => {
                const a = b.querySelector('a[href*="/card/"]');
                if (!a) return;
                const href = a.getAttribute('href');
                if (seen.has(href)) return;
                const titleEl = b.querySelector('div.m4M-1, span.ds-text_group_core span.ds-text, h3');
                const title = titleEl ? titleEl.textContent.trim() : '';
                const txt = b.textContent || '';
                const m = txt.match(/(\d[\d\u00a0 ]{2,})\s*₽/);
                const price = m ? m[1].replace(/[\u00a0 ]/g, '') : '';
                if (title && price) {
                    seen.add(href);
                    out.push({href, title: title.slice(0, 120), price});
                }
            });
            return out.slice(0, LIMIT);
        }
        """
        raw = await page.evaluate(script.replace("LIMIT", str(limit)))
        out: list[Product] = []
        for r in raw:
            try:
                price = int(r["price"])
            except (TypeError, ValueError):
                continue
            ext_id = r["href"].rsplit("/", 1)[-1].split("?")[0]
            out.append(Product(
                marketplace="yandex", ext_id=ext_id,
                title=r["title"][:120], price=price,
                url=("https://market.yandex.ru" + r["href"]
                     if r["href"].startswith("/") else r["href"]),
            ))
        return out

    async def get_card(self, ext_id: str) -> Product | None:
        return None

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list[Review]:
        ctx, page = await self._new_page()
        try:
            url = f"https://market.yandex.ru/product--x/{ext_id}/reviews"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            for _ in range(4):
                await page.mouse.wheel(0, 2500)
                await asyncio.sleep(2)
            script = """
            () => {
                const out = [];
                document.querySelectorAll('[data-baobab-name*="review"], [class*="review"]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (t.length < 40) return;
                    const m = t.match(/(\d[\d\u00a0 ]{0,2})\s*из\s*5/);
                    const rating = m ? parseInt(m[1]) : null;
                    out.push({text: t.slice(0, 500), rating});
                });
                const seen = new Set();
                return out.filter(r => !seen.has(r.text.slice(0, 60)) && seen.add(r.text.slice(0, 60))).slice(0, LIMIT);
            }
            """
            raw = await page.evaluate(script.replace("LIMIT", str(limit)))
            return [Review(product_market="yandex", product_id=ext_id,
                           review_id=str(i), rating=r["rating"], text=r["text"])
                    for i, r in enumerate(raw)]
        except Exception as exc:
            logger.warning("YM-браузер: отзывы %s не извлечены: %s", ext_id, exc)
            return []
        finally:
            await ctx.close()

    async def get_photos(self, ext_id: str) -> list[str]:
        return []
