"""Фиксация публичных JSON-эндпоинтов маркетплейсов (канал 1).

Автоматизирует процедуру из PRD §3 «Канал 1»:
  1. Открывает сайт в настоящем браузере (Playwright + системный Chrome).
  2. Перехватывает все XHR/Fetch-запросы (аналог вкладки Network → Fetch/XHR).
  3. Выполняет поиск, открывает карточку, прокручивает отзывы.
  4. Сохраняет URL, точные заголовки и cookies каждого JSON-эндпоинта
     в captured/{market}.json — «набор берётся 1-в-1 из пойманного запроса,
     ничего не придумывается» (PRD §3).

Запуск (с рабочего IP; при необходимости — через прокси):
    python tools/capture_endpoints.py --market ozon --query "маска для сна"
    python tools/capture_endpoints.py --market yandex --query "маска для сна" --proxy http://user:pass@host:port

Результат используется адаптерами (adapters/capture.py): эндпоинты,
заголовки и cookies применяются 1-в-1; если capture-файла нет —
адаптер работает на известных семействах эндпоинтов и fallback-парсерах.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

CAPTURED_DIR = BASE_DIR / "captured"

# Секретные заголовки/куки, которые не пишем в файл.
_SECRET_HEADERS = {"cookie", "authorization", "proxy-authorization"}

MARKET_URLS = {
    "ozon": "https://www.ozon.ru/",
    "yandex": "https://market.yandex.ru/",
}


def _is_json_url(url: str) -> bool:
    """JSON-эндпоинты SPA: /api/, composer-api, *.bx/*/json, /search/ у API."""
    low = url.lower()
    if any(x in low for x in ("captcha", "challenge", "recaptcha", "sentry", "analytics")):
        return False
    if "/api/" in low:
        return True
    if "composer-api" in low or "entrypoint-api" in low:
        return True
    if re.search(r"\.bx/page/json", low):
        return True
    return False


def _safe_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        if k.lower() not in _SECRET_HEADERS:
            out[k] = v
    return out


async def _capture_market(pw, market: str, query: str, proxy: str | None,
                          out: Path) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_opts = dict(
            viewport={"width": 1600, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="ru-RU",
        )
        if proxy:
            ctx_opts["proxy"] = {"server": proxy}
        ctx = await browser.new_context(**ctx_opts)
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()
        page.set_default_timeout(30000)

        async def goto_retry(url: str, attempts: int = 3) -> None:
            """goto с повторными попытками: сеть (Happ/xray TUN) может
            переключаться посреди загрузки → ERR_NETWORK_CHANGED."""
            last: Exception | None = None
            for i in range(1, attempts + 1):
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=30000)
                    return
                except Exception as exc:
                    last = exc
                    print(f"  попытка {i}/{attempts}: {str(exc)[:90]}")
                    await asyncio.sleep(3)
            if last is not None:
                raise last

        hits: dict[str, dict] = {}
        order: list[str] = []

        async def on_response(resp):
            url = resp.url
            if not _is_json_url(url) or url in hits:
                return
            try:
                body = await resp.text()
            except Exception:
                body = ""
            if not body or not body.lstrip().startswith(("{", "[")):
                return
            req = resp.request
            hits[url] = {
                "url": url,
                "method": req.method,
                "status": resp.status,
                "content_type": resp.headers.get("content-type", ""),
                "request_headers": _safe_headers(req.headers),
                "cookies": [c["name"] for c in await ctx.cookies(url)],
                "sample": body[:4000],
            }
            order.append(url)

        page.on("response", on_response)

        async def log_state(tag: str) -> None:
            try:
                print(f"  [{tag}] url={page.url[:70]} title={((await page.title()) or '')[:50]}")
            except Exception:
                pass

        # 1. главная → бутстрап cookies (с retry на сетевые сбои)
        try:
            await goto_retry(MARKET_URLS[market])
        except Exception as exc:
            print(f"Ошибка загрузки главной {MARKET_URLS[market]}: {str(exc)[:90]}")
        await asyncio.sleep(4)
        await log_state("главная")

        # 2. поиск
        search_url = ("https://www.ozon.ru/search/?text=" if market == "ozon"
                      else "https://market.yandex.ru/search?text=") + query.replace(" ", "+")
        try:
            await goto_retry(search_url)
        except Exception as exc:
            print(f"Ошибка загрузки поиска: {str(exc)[:90]}")
        await asyncio.sleep(6)
        await log_state("поиск")

        # 3. карточка: кликаем первую ссылку на товар
        card_url = None
        try:
            link = await page.query_selector(
                "a[href*='/product/']" if market == "ozon"
                else "a[href*='/product']")
            if link:
                card_url = await link.get_attribute("href")
        except Exception:
            card_url = None
        if card_url:
            full = card_url if card_url.startswith("http") else (
                "https://www.ozon.ru" + card_url if market == "ozon"
                else "https://market.yandex.ru" + card_url)
            try:
                await goto_retry(full)
                await asyncio.sleep(5)
                await log_state("карточка")
                # 4. отзывы: прокрутка вниз (пагинация подгружается)
                for _ in range(4):
                    await page.mouse.wheel(0, 2500)
                    await asyncio.sleep(2)
            except Exception:
                pass

        await browser.close()

    return {"market": market, "query": query, "endpoints": [
        hits[u] for u in order if u in hits
    ]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", choices=["ozon", "yandex"], required=True)
    ap.add_argument("--query", default="маска для сна")
    ap.add_argument("--proxy", default=None, help="http://user:pass@host:port")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    CAPTURED_DIR.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else CAPTURED_DIR / f"{args.market}.json"

    print(f"Фиксирую эндпоинты {args.market} (запрос: {args.query})...")
    data = asyncio.run(_capture_market(None, args.market, args.query,
                                       args.proxy, out))
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    n = len(data["endpoints"])
    print(f"Зафиксировано эндпоинтов: {n} → {out}")
    for e in data["endpoints"]:
        print(f"  {e['status']} {e['method']} {e['url'][:110]}")
    if n == 0:
        print("ВНИМАНИЕ: ни одного JSON-эндпоинта. Скорее всего сайт отдал "
              "капчу/челлендж (нужен чистый IP или --proxy).")


if __name__ == "__main__":
    main()
