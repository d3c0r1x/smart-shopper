export type Marketplace = "ozon" | "yandex";

export interface Product {
  marketplace: Marketplace;
  ext_id: string;
  title: string;
  price: number;
  old_price?: number | null;
  url: string;
  rating?: number | null;
  reviews_count: number;
  ean?: string | null;
  brand: string;
  traits: string[];
}

export interface Review {
  id: string;
  rating: number;
  author: string;
  date: string;
  text: string;
  pros?: string;
  cons?: string;
  bought_here?: boolean;
}

export interface CompareRow {
  title: string;
  ozon?: number | null;
  yandex?: number | null;
  ozon_url: string;
  yandex_url: string;
  cheaper?: Marketplace | null;
  diff_percent?: number | null;
}

export interface BudgetInfo {
  used: number;
  limit: number;
  remaining: number;
  profile: string;
}

export interface SearchResult {
  products: Product[];
  constraints: { query: string; must_have: string[]; max_price?: number | null };
  verdicts: Record<string, Array<{ requirement: string; verdict: string; mentions: number }>>;
}
