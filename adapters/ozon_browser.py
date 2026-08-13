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

    async def get_card(self, ext_id: str) -> Product | None:
        return None

    async def get_reviews(self, ext_id: str, limit: int = 20) -> list[Review]:
        return []

    async def get_photos(self, ext_id: str) -> list[str]:
        return []
