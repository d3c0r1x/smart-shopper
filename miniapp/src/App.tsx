import { useCallback, useEffect, useState } from "react";
import { comparePrices, getBudget, getReviews, searchProducts } from "./api";
import type { BudgetInfo, CompareRow, Product, Review, SearchResult } from "./types";

const SUGGESTIONS = [
  "чёрная маска для сна с пространством для ресниц",
  "кроссовки белые",
  "маска для сна до 1200 рублей",
];

type Screen =
  | { name: "home" }
  | { name: "results"; query: string; result: SearchResult }
  | { name: "product"; product: Product }
  | { name: "reviews"; product: Product }
  | { name: "compare"; rows: CompareRow[]; query: string }
  | { name: "favorites" }
  | { name: "settings" };

function rub(n: number | null | undefined): string {
  if (!n || n <= 0) return "цена неизвестна";
  return n.toLocaleString("ru-RU") + " ₽";
}

function MarketBadge({ m }: { m: "ozon" | "yandex" }) {
  return m === "ozon"
    ? <span className="badge badge-ozon">🟢 Ozon</span>
    : <span className="badge badge-ym">🔵 Яндекс</span>;
}

function VerdictMark({ v }: { v: string }) {
  return <span>{v === "confirmed" ? "✅" : v === "rejected" ? "❌" : "⚠️"}</span>;
}

function Stars({ n }: { n: number }) {
  return <span className="stars">{"★".repeat(Math.round(n)) + "☆".repeat(5 - Math.round(n))}</span>;
}

export default function App() {
  const [screen, setScreen] = useState<Screen>({ name: "home" });
  const [favorites, setFavorites] = useState<Product[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("ss_favs") ?? "[]") as Product[];
    } catch {
      return [];
    }
  });

  const saveFav = (p: Product) => {
    const next = favorites.some((f) => f.ext_id === p.ext_id && f.marketplace === p.marketplace)
      ? favorites.filter((f) => !(f.ext_id === p.ext_id && f.marketplace === p.marketplace))
      : [...favorites, p];
    setFavorites(next);
    localStorage.setItem("ss_favs", JSON.stringify(next));
  };

  const isFav = (p: Product) =>
    favorites.some((f) => f.ext_id === p.ext_id && f.marketplace === p.marketplace);

  const go = (s: Screen) => {
    setScreen(s);
    window.scrollTo(0, 0);
  };

  return (
    <div className="app">
      <Header screen={screen} onBack={() => go({ name: "home" })} />
      <main className="content">
        {screen.name === "home" && (
          <HomeScreen onOpen={(s) => go(s)} />
        )}
        {screen.name === "results" && (
          <ResultsScreen
            screen={screen}
            isFav={isFav}
            onFav={saveFav}
            onProduct={(p) => go({ name: "product", product: p })}
            onCompare={(q) => {
              comparePrices(q).then((rows) => go({ name: "compare", rows, query: q }));
            }}
            onReviews={(p) => go({ name: "reviews", product: p })}
          />
        )}
        {screen.name === "product" && (
          <ProductScreen
            product={screen.product}
            favored={isFav(screen.product)}
            onFav={() => saveFav(screen.product)}
            onReviews={() => go({ name: "reviews", product: screen.product })}
            onCompare={(q) => {
              comparePrices(q).then((rows) => go({ name: "compare", rows, query: q }));
            }}
          />
        )}
        {screen.name === "reviews" && <ReviewsScreen product={screen.product} />}
        {screen.name === "compare" && (
          <CompareScreen rows={screen.rows} query={screen.query} onSearch={(q) => {
            comparePrices(q).then((rows) => go({ name: "compare", rows, query: q }));
          }} />
        )}
        {screen.name === "favorites" && (
          <FavoritesScreen items={favorites} onProduct={(p) => go({ name: "product", product: p })} />
        )}
        {screen.name === "settings" && <SettingsScreen />}
      </main>
      <TabBar
        active={screen.name === "favorites" ? "favorites" : screen.name === "settings" ? "settings" : "home"}
        onTab={(t) => go(t === "favorites" ? { name: "favorites" } : t === "settings" ? { name: "settings" } : { name: "home" })}
      />
    </div>
  );
}

// ─────────────────────────── Header ───────────────────────────────

function Header({ screen, onBack }: { screen: Screen; onBack: () => void }) {
  const titles: Record<string, string> = {
    home: "Умный Шоппер",
    results: "Результаты",
    product: "Товар",
    reviews: "Отзывы",
    compare: "Сравнение цен",
    favorites: "Избранное",
    settings: "Настройки",
  };
  const canBack = screen.name !== "home";
  return (
    <header className="header">
      {canBack && <button className="btn-ghost" onClick={onBack}>←</button>}
      <h1>{titles[screen.name]}</h1>
    </header>
  );
}

// ─────────────────────────── Home ─────────────────────────────────

function HomeScreen({ onOpen }: { onOpen: (s: Screen) => void }) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<"idle" | "busy">("idle");
  const [error, setError] = useState("");

  const run = useCallback(async (q: string) => {
    if (!q.trim()) return;
    setState("busy");
    setError("");
    try {
      const markets = ["ozon", "yandex"];
      const result = await searchProducts(q, markets);
      onOpen({ name: "results", query: q, result });
    } catch (e) {
      setError(String(e));
    } finally {
      setState("idle");
    }
  }, [onOpen]);

  return (
    <div className="home">
      <div className="search-box">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run(query)}
          placeholder="Например: чёрная маска для сна…"
          autoFocus
        />
        <button className="btn-primary" onClick={() => run(query)} disabled={state === "busy"}>
          {state === "busy" ? "🔎 Ищу…" : "🔎 Найти"}
        </button>
      </div>
      <p className="hint">Опишите словами — ассистент проверит требования по отзывам.</p>
      {error && <p className="error">{error}</p>}
      <div className="chips">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chip" onClick={() => { setQuery(s); run(s); }}>
            {s}
          </button>
        ))}
      </div>
      <div className="menu-cards">
        <MenuCard emoji="📸" title="Найти по фото" desc="Сфотографировали — найдём похожее" />
        <MenuCard emoji="⚖️" title="Сравнить цены" desc="Ozon vs Яндекс Маркет" onClick={() => onOpen({ name: "compare", rows: [], query: "" })} />
        <MenuCard emoji="⭐" title="Избранное" desc="Сохранённые товары" onClick={() => onOpen({ name: "favorites" })} />
        <MenuCard emoji="⚙️" title="Настройки" desc="Профиль и лимиты запросов" onClick={() => onOpen({ name: "settings" })} />
      </div>
    </div>
  );
}

function MenuCard({ emoji, title, desc, onClick }: { emoji: string; title: string; desc: string; onClick?: () => void }) {
  return (
    <button className="menu-card" onClick={onClick}>
      <span className="menu-emoji">{emoji}</span>
      <div>
        <b>{title}</b>
        <p>{desc}</p>
      </div>
    </button>
  );
}

// ─────────────────────────── Results ──────────────────────────────

function ResultsScreen({ screen, isFav, onFav, onProduct, onCompare, onReviews }: {
  screen: Extract<Screen, { name: "results" }>;
  isFav: (p: Product) => boolean;
  onFav: (p: Product) => void;
  onProduct: (p: Product) => void;
  onCompare: (q: string) => void;
  onReviews: (p: Product) => void;
}) {
  const { query, result } = screen;
  return (
    <div className="results">
      <div className="results-head">
        <h2>По запросу «{query}»</h2>
        {result.constraints.must_have.length > 0 && (
          <p className="hint">Требования: {result.constraints.must_have.join(" · ")}</p>
        )}
        <button className="btn-outline" onClick={() => onCompare(query)}>⚖️ Сравнить цены на обеих площадках</button>
      </div>
      {result.products.map((p) => (
        <ProductCard
          key={`${p.marketplace}:${p.ext_id}`}
          p={p}
          favored={isFav(p)}
          verdicts={result.verdicts[p.ext_id]}
          onFav={() => onFav(p)}
          onOpen={() => onProduct(p)}
          onReviews={() => onReviews(p)}
        />
      ))}
    </div>
  );
}

// ─────────────────────────── ProductCard ──────────────────────────

function ProductCard({ p, favored, verdicts, onFav, onOpen, onReviews }: {
  p: Product;
  favored: boolean;
  verdicts?: Array<{ requirement: string; verdict: string; mentions: number }>;
  onFav: () => void;
  onOpen: () => void;
  onReviews: () => void;
}) {
  const disc = p.old_price && p.old_price > p.price
    ? Math.round((1 - p.price / p.old_price) * 100)
    : null;
  return (
    <article className="card" onClick={onOpen}>
      <div className="card-head">
        <MarketBadge m={p.marketplace} />
        <span className="rating">⭐ {p.rating ?? "—"} · {p.reviews_count} отз.</span>
      </div>
      <h3>{p.title}</h3>
      <p className="price">
        <b>{rub(p.price)}</b>
        {p.old_price && <s> {rub(p.old_price)}</s>}
        {disc && <span className="discount"> −{disc}%</span>}
      </p>
      {p.traits.length > 0 && <p className="traits">🏷 {p.traits.join(" · ")}</p>}
      {verdicts && verdicts.length > 0 && (
        <p className="verdicts">
          {verdicts.map((v) => (
            <span key={v.requirement}><VerdictMark v={v.verdict} /> {v.requirement}</span>
          ))}
        </p>
      )}
      <div className="card-actions" onClick={(e) => e.stopPropagation()}>
        <button className="btn-outline" onClick={onReviews}>📝 Отзывы ({p.reviews_count})</button>
        <button className="btn-outline" onClick={onFav}>{favored ? "⭐ Убрать" : "⭐ В избранное"}</button>
      </div>
    </article>
  );
}

// ─────────────────────────── Product ──────────────────────────────

function ProductScreen({ product: p, favored, onFav, onReviews, onCompare }: {
  product: Product;
  favored: boolean;
  onFav: () => void;
  onReviews: () => void;
  onCompare: (q: string) => void;
}) {
  return (
    <div className="product">
      <MarketBadge m={p.marketplace} />
      <h2>{p.title}</h2>
      <p className="price big"><b>{rub(p.price)}</b>
        {p.old_price && <s> {rub(p.old_price)}</s>}
      </p>
      <p className="hint">⭐ {p.rating ?? "—"} · {p.reviews_count} отзывов{p.ean ? ` · EAN ${p.ean}` : ""}</p>
      {p.traits.length > 0 && <p className="traits">🏷 {p.traits.join(" · ")}</p>}
      <div className="action-stack">
        <button className="btn-primary" onClick={onReviews}>📝 Отзывы ({p.reviews_count})</button>
        <button className="btn-outline" onClick={() => onCompare(p.title)}>⚖️ Сравнить цены</button>
        <button className="btn-outline" onClick={onFav}>{favored ? "⭐ Убрать из избранного" : "⭐ В избранное"}</button>
        <a className="btn-outline" href={p.url} target="_blank" rel="noreferrer">🛒 Открыть на маркетплейсе</a>
      </div>
    </div>
  );
}

// ─────────────────────────── Reviews ──────────────────────────────

function ReviewsScreen({ product: p }: { product: Product }) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [open, setOpen] = useState<Review | null>(null);

  useEffect(() => {
    getReviews(p).then(setReviews);
  }, [p]);

  return (
    <div className="reviews">
      <h2>{p.title}</h2>
      {!reviews && <p className="hint">Читаю отзывы…</p>}
      {reviews?.length === 0 && <p className="hint">Отзывов пока нет.</p>}
      <div className="review-list">
        {reviews?.map((r) => (
          <button key={r.id} className="review-item" onClick={() => setOpen(r)}>
            <div className="review-meta">
              <Stars n={r.rating} /> <b>{r.rating}/5</b> · {r.author} · {r.date}
              {r.bought_here && <span className="bought">🛒 куплен здесь</span>}
            </div>
            <p>{r.text}</p>
          </button>
        ))}
      </div>
      {open && (
        <div className="sheet" onClick={() => setOpen(null)}>
          <div className="sheet-card" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <Stars n={open.rating} /> <b>{open.rating}/5</b>
              <button className="btn-ghost" onClick={() => setOpen(null)}>✕</button>
            </div>
            <p className="hint">{open.author} · {open.date}{open.bought_here ? " · куплен на маркетплейсе" : ""}</p>
            <p>{open.text}</p>
            {open.pros && <p className="pros">👍 {open.pros}</p>}
            {open.cons && <p className="cons">👎 {open.cons}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── Compare ──────────────────────────────

function CompareScreen({ rows, query, onSearch }: {
  rows: CompareRow[];
  query: string;
  onSearch: (q: string) => void;
}) {
  const [q, setQ] = useState(query);
  const [busy, setBusy] = useState(false);
  return (
    <div className="compare">
      <div className="search-box">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Что сравнить?" />
        <button className="btn-primary" disabled={busy} onClick={async () => { setBusy(true); await onSearch(q); setBusy(false); }}>
          {busy ? "…" : "⚖️"}
        </button>
      </div>
      {rows.length === 0 && <p className="hint">Введите название товара — сравним цены на Ozon и Яндекс Маркете.</p>}
      {rows.map((r, i) => (
        <article className="card" key={i}>
          <h3>{r.title}</h3>
          <div className="compare-row">
            <span>🟢 Ozon: <b>{rub(r.ozon)}</b></span>
            <span>🔵 Яндекс: <b>{rub(r.yandex)}</b></span>
          </div>
          {r.cheaper && r.diff_percent ? (
            <p className="winner">🏆 Выгоднее {r.cheaper === "ozon" ? "на Ozon" : "на Яндекс Маркете"} на {r.diff_percent}%</p>
          ) : (
            <p className="hint">Цены совпадают</p>
          )}
          <div className="card-actions">
            <a className="btn-outline" href={r.ozon_url} target="_blank" rel="noreferrer">🛒 Ozon</a>
            <a className="btn-outline" href={r.yandex_url} target="_blank" rel="noreferrer">🛒 Яндекс</a>
          </div>
        </article>
      ))}
    </div>
  );
}

// ─────────────────────────── Favorites ────────────────────────────

function FavoritesScreen({ items, onProduct }: { items: Product[]; onProduct: (p: Product) => void }) {
  if (items.length === 0) return <p className="hint">Избранное пусто. Добавляйте ⭐ на карточках товаров.</p>;
  return (
    <div className="results">
      {items.map((p) => (
        <article className="card" key={`${p.marketplace}:${p.ext_id}`} onClick={() => onProduct(p)}>
          <div className="card-head"><MarketBadge m={p.marketplace} /></div>
          <h3>{p.title}</h3>
          <p className="price"><b>{rub(p.price)}</b></p>
        </article>
      ))}
    </div>
  );
}

// ─────────────────────────── Settings ─────────────────────────────

function SettingsScreen() {
  const [budget, setBudget] = useState<BudgetInfo | null>(null);
  useEffect(() => { getBudget().then(setBudget); }, []);
  return (
    <div className="settings">
      <h2>Лимиты и модели</h2>
      <div className="card">
        <p className="hint">Осталось запросов сегодня</p>
        <p className="budget-big">{budget ? `${budget.remaining} из ${budget.limit}` : "…"}</p>
        <p className="hint">
          Бесплатные модели OpenRouter: 50 запросов/день, после разового
          пополнения от $10 — 1000/день. Профиль: {budget?.profile ?? "quality"}.
        </p>
      </div>
      <h2>О приложении</h2>
      <p className="hint">
        ИИ-ассистент покупок для Ozon и Яндекс Маркета. Ответы основаны только
        на реальных данных маркетплейсов — бот не выдумывает товары и цены.
      </p>
    </div>
  );
}

// ─────────────────────────── TabBar ───────────────────────────────

function TabBar({ active, onTab }: { active: string; onTab: (t: string) => void }) {
  const tabs = [
    { key: "home", emoji: "🏠", label: "Главная" },
    { key: "favorites", emoji: "⭐", label: "Избранное" },
    { key: "settings", emoji: "⚙️", label: "Настройки" },
  ];
  return (
    <nav className="tabbar">
      {tabs.map((t) => (
        <button key={t.key} className={active === t.key ? "tab active" : "tab"} onClick={() => onTab(t.key)}>
          <span>{t.emoji}</span>
          {t.label}
        </button>
      ))}
    </nav>
  );
}
