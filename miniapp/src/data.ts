import type { Product, Review } from "./types";

// Демо-каталог — зеркало adapters/demo.py: те же товары и EAN, чтобы
// демо-режим Python и Mini App показывали одинаковые результаты.
export const DEMO_PRODUCTS: Product[] = [
  { marketplace: "ozon", ext_id: "snk-001", title: "Кроссовки белые унисекс Urban Runner", price: 4990, old_price: 6490, url: "https://demo.local/ozon/snk-001", rating: 4.3, reviews_count: 1540, ean: "4607000000011", brand: "Urban", traits: ["белый", "кроссовки", "кожа"] },
  { marketplace: "ozon", ext_id: "snk-002", title: "Кроссовки мужские белые Air Flex", price: 7490, old_price: 8990, url: "https://demo.local/ozon/snk-002", rating: 4.7, reviews_count: 2140, ean: "4607000000012", brand: "AirFlex", traits: ["белый", "мужские", "кроссовки"] },
  { marketplace: "ozon", ext_id: "snk-003", title: "Кроссовки женские розовые Comfy Run", price: 6290, url: "https://demo.local/ozon/snk-003", rating: 4.5, reviews_count: 980, ean: "4607000000013", brand: "Comfy", traits: ["розовый", "женские", "кроссовки"] },
  { marketplace: "ozon", ext_id: "snk-004", title: "Кеды белые Classic Canvas", price: 3990, url: "https://demo.local/ozon/snk-004", rating: 4.2, reviews_count: 760, ean: "4607000000014", brand: "Classic", traits: ["белый", "кеды", "текстиль"] },
  { marketplace: "ozon", ext_id: "snk-005", title: "Кроссовки чёрные Night Runner", price: 6890, url: "https://demo.local/ozon/snk-005", rating: 4.6, reviews_count: 1830, ean: "4607000000015", brand: "Night", traits: ["чёрный", "кроссовки"] },
  { marketplace: "ozon", ext_id: "snk-006", title: "Кроссовки для бега чёрные Marathon", price: 7990, old_price: 9500, url: "https://demo.local/ozon/snk-006", rating: 4.8, reviews_count: 3210, ean: "4607000000016", brand: "Marathon", traits: ["чёрный", "кроссовки", "для бега"] },
  { marketplace: "ozon", ext_id: "msk-001", title: "Маска для сна 3D чёрная с пространством для ресниц", price: 990, url: "https://demo.local/ozon/msk-001", rating: 4.7, reviews_count: 1240, ean: "4607000000021", brand: "SilkSleep", traits: ["чёрный", "3D", "для ресниц"] },
  { marketplace: "ozon", ext_id: "msk-002", title: "Маска для сна 3D серая мягкая", price: 890, url: "https://demo.local/ozon/msk-002", rating: 4.5, reviews_count: 680, ean: "4607000000022", brand: "SilkSleep", traits: ["серый", "3D"] },
  { marketplace: "ozon", ext_id: "msk-003", title: "Маска для сна шёлковая чёрная", price: 1290, url: "https://demo.local/ozon/msk-003", rating: 4.6, reviews_count: 920, ean: "4607000000023", brand: "SilkSleep", traits: ["чёрный", "шёлк"] },
  { marketplace: "ozon", ext_id: "msk-004", title: "Маска для сна 3D с охлаждающим гелем", price: 1190, url: "https://demo.local/ozon/msk-004", rating: 4.4, reviews_count: 430, ean: "4607000000024", brand: "CoolNight", traits: ["3D", "гель"] },
  { marketplace: "yandex", ext_id: "snk-001y", title: "Кроссовки белые Urban Runner", price: 5290, url: "https://demo.local/yandex/snk-001y", rating: 4.2, reviews_count: 1100, ean: "4607000000011", brand: "Urban", traits: ["белый", "кроссовки"] },
  { marketplace: "yandex", ext_id: "snk-002y", title: "Кроссовки мужские Air Flex белые", price: 7190, url: "https://demo.local/yandex/snk-002y", rating: 4.6, reviews_count: 1700, ean: "4607000000012", brand: "AirFlex", traits: ["белый", "мужские", "кроссовки"] },
  { marketplace: "yandex", ext_id: "snk-003y", title: "Кроссовки женские Comfy Run розовые", price: 6490, url: "https://demo.local/yandex/snk-003y", rating: 4.4, reviews_count: 700, ean: "4607000000013", brand: "Comfy", traits: ["розовый", "женские", "кроссовки"] },
  { marketplace: "yandex", ext_id: "msk-001y", title: "Маска для сна 3D чёрная с пространством для ресниц", price: 1050, url: "https://demo.local/yandex/msk-001y", rating: 4.6, reviews_count: 900, ean: "4607000000021", brand: "SilkSleep", traits: ["чёрный", "3D", "для ресниц"] },
  { marketplace: "yandex", ext_id: "msk-002y", title: "Маска для сна 3D серая", price: 940, url: "https://demo.local/yandex/msk-002y", rating: 4.4, reviews_count: 500, ean: "4607000000022", brand: "SilkSleep", traits: ["серый", "3D"] },
  { marketplace: "yandex", ext_id: "msk-003y", title: "Маска шёлковая чёрная для сна", price: 1350, url: "https://demo.local/yandex/msk-003y", rating: 4.5, reviews_count: 760, ean: "4607000000023", brand: "SilkSleep", traits: ["чёрный", "шёлк"] },
];

export const DEMO_REVIEWS: Record<string, Review[]> = {
  "ozon:msk-001": [
    { id: "r1", rating: 5, author: "Анна", date: "июль 2026", text: "Отлично, пространство для ресниц реально есть, ресницы не мнутся.", pros: "Не мнёт ресницы", bought_here: true },
    { id: "r2", rating: 4, author: "Михаил", date: "июль 2026", text: "Прилегает плотно, свет не пробивается. Немного давит переносицу в первые ночи.", cons: "Давит переносицу первые ночи", bought_here: true },
    { id: "r3", rating: 5, author: "Елена", date: "июнь 2026", text: "Чёрная, как на фото. Пространство для ресниц большое, спать удобно.", bought_here: true },
    { id: "r4", rating: 4, author: "Дмитрий", date: "июнь 2026", text: "Плотно прилегает к лицу, но на широком лице может давить.", cons: "Может давить на широком лице" },
    { id: "r5", rating: 5, author: "Ольга", date: "май 2026", text: "Материал приятный, не жарко. Ресницы целы, прилегание отличное.", bought_here: true },
  ],
  "ozon:snk-001": [
    { id: "r1", rating: 4, author: "Иван", date: "июль 2026", text: "Белые кроссовки, смотрятся отлично, но быстро пачкаются.", cons: "Маркие", bought_here: true },
    { id: "r2", rating: 5, author: "Сергей", date: "июнь 2026", text: "Удобные, лёгкие. Белый цвет — классика.", bought_here: true },
    { id: "r3", rating: 4, author: "Мария", date: "май 2026", text: "Размер в размер, нога дышит.", bought_here: true },
  ],
  "ozon:snk-002": [
    { id: "r1", rating: 5, author: "Павел", date: "июль 2026", text: "Кожа мягкая, белые и красивые. Носятся уже месяц, нареканий нет.", bought_here: true },
    { id: "r2", rating: 4, author: "Алексей", date: "июнь 2026", text: "Хорошие кроссовки, чуть маломерят — берите на размер больше.", cons: "Маломерят", bought_here: true },
  ],
  "ozon:msk-003": [
    { id: "r1", rating: 5, author: "Наталья", date: "июль 2026", text: "Шёлк приятный, чёрный цвет глубокий, совсем не давит на глаза.", bought_here: true },
    { id: "r2", rating: 4, author: "Кирилл", date: "июнь 2026", text: "Мягкая, прилегает хорошо. Для ресниц пространства меньше, чем у 3D.", cons: "Меньше места для ресниц" },
  ],
  "ozon:snk-006": [
    { id: "r1", rating: 5, author: "Тимур", date: "июль 2026", text: "Для бега отличные: лёгкие, дышащие, амортизация хорошая.", bought_here: true },
  ],
};
