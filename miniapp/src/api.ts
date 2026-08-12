import { retrieveLaunchParams } from "@telegram-apps/sdk";
import { DEMO_PRODUCTS, DEMO_REVIEWS } from "./data";
import type { BudgetInfo, CompareRow, Product, Review, SearchResult } from "./types";

// Реальный бэкенд подключается переменной VITE_API_URL при сборке:
//   VITE_API_URL=https://api.example.com VITE_API_TOKEN=secret npm run build
// Без неё Mini App работает в демо-режиме на встроенном каталоге
// (тот же набор товаров, что и Python-демо бота).
const API = import.meta.env.VITE_API_URL as string | undefined;
const TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

const STOP = new Set(["для", "с", "на", "и", "в", "из", "по", "от", "не", "за", "или", "а", "но", "до", "как", "что"]);

function words(q: string): string[] {
  return q.toLowerCase().split(" ").filter((w) => w.length > 2 && !STOP.has(w));
}

function score(p: Product, q: string): number {
  const ws = words(q);
  const hay = `${p.title} ${p.traits.join(" ")}`.toLowerCase();
  return ws.filter((w) => hay.includes(w)).length;
}

// Telegram initData: сервер валидирует подпись HMAC-SHA256 и берёт user_id.
// В демо-режиме браузера initData невалиден — сервер (SHOPPER_API_ALLOW_ANON=1)
// возьмёт user_id из query.
function launchInfo(): { initData: string; userId: string } {
  try {
    const lp = retrieveLaunchParams();
    return {
      initData: String(lp.initDataRaw ?? ""),
      userId: String(
        (lp.initData as unknown as { user?: { id?: number } } | undefined)
          ?.user?.id ?? "",
      ),
    };
  } catch {
    return { initData: "", userId: "" };
  }
}

async function getJson<T>(path: string, params: Record<string, string>): Promise<T> {
  const url = new URL(`${API}${path}`);
  for (const [k, v] of Object.entries(params)) {
    if (v) url.searchParams.set(k, v);
  }
  const headers: Record<string, string> = {};
  if (TOKEN) headers["X-API-Token"] = TOKEN;
  const resp = await fetch(url.toString(), { headers });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return (await resp.json()) as T;
}

function authParams(): Record<string, string> {
  const { initData, userId } = launchInfo();
  return { initData, user_id: userId };
}

export async function searchProducts(
  query: string,
  markets: string[],
): Promise<SearchResult> {
  if (API) {
    return getJson<SearchResult>("/api/search", {
      q: query,
      markets: markets.join(","),
      ...authParams(),
    });
  }
  await delay(350); // имитация «думающего» ассистента
  const pool = DEMO_PRODUCTS.filter((p) => markets.includes(p.marketplace));
  const ranked = pool
    .map((p) => ({ p, s: score(p, query) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, 5)
    .map((x) => x.p);
  const fallback = ranked.length ? ranked : pool.slice(0, 5);
  return {
    products: fallback,
    constraints: { query, must_have: [] },
    verdicts: {},
  };
}

export async function getReviews(product: Product): Promise<Review[]> {
  if (API) {
    return getJson<{ reviews: Review[] }>("/api/reviews", {
      marketplace: product.marketplace,
      ext_id: product.ext_id,
      ...authParams(),
    }).then((r) => r.reviews);
  }
  await delay(250);
  return DEMO_REVIEWS[`${product.marketplace}:${product.ext_id}`] ?? [];
}

export async function comparePrices(query: string): Promise<CompareRow[]> {
  if (API) {
    return getJson<{ rows: CompareRow[] }>("/api/compare", {
      q: query,
      ...authParams(),
    }).then((r) => r.rows);
  }
  await delay(300);
  const ozon = DEMO_PRODUCTS.filter((p) => p.marketplace === "ozon" && score(p, query) > 0);
  const yandex = DEMO_PRODUCTS.filter((p) => p.marketplace === "yandex");
  const rows: CompareRow[] = [];
  for (const o of ozon.slice(0, 3)) {
    const ym = yandex.find((y) => y.ean && y.ean === o.ean);
    if (!ym) continue;
    const cheaper = o.price < ym.price ? "ozon" : "yandex";
    const diff = Math.round((Math.abs(o.price - ym.price) / Math.max(o.price, ym.price)) * 100);
    rows.push({
      title: o.title,
      ozon: o.price,
      yandex: ym.price,
      ozon_url: o.url,
      yandex_url: ym.url,
      cheaper,
      diff_percent: diff,
    });
  }
  return rows;
}

export async function getBudget(): Promise<BudgetInfo> {
  if (API) {
    return getJson<BudgetInfo>("/api/budget", { ...authParams() });
  }
  return { used: 0, limit: 50, remaining: 50, profile: "quality" };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
