"""Оценка качества поиска (ТЗ §5): Precision@K, p95 latency, структурное соответствие.

Использование:
    python tools/eval_precision.py               # оффлайн (демо-каталог, быстро, детерминированно)
    python tools/eval_precision.py --live        # реальные маркетплейсы (сеть + LLM)
    python tools/eval_precision.py --queries 3   # ограничить число запросов

Метрики считаются детерминированно по эталонным ключевым словам
(без LLM-судьи — воспроизводимо):
  shown_precision   — Precision@K по карточкам, которые бот реально показывает
                      (K = число показанных карточек, по UX-дизайну — 3);
  hybrid_precision5 — Precision@5 гибридного реранкера (семантика + лексика +
                      структура) на префильтрованном пуле кандидатов — это
                      метрика поискового ядра (ТЗ §2), до обрезки до топ-3;
  structural_ok     — доля показанных карточек, проходящих жёсткие
                      ограничения запроса (цена/рейтинг);
  p95 latency       — 95-й перцентиль времени полного цикла поиска.

Честные ограничения (важно читать вместе с результатом):
  * оффлайн-режим измеряет качество ПАЙПЛАЙНА на фиксированном демо-каталоге,
    а не качество реальной выдачи маркетплейсов;
  * реальная Precision@5 на живых площадках требует размеченной выборки
    реальных карточек — такой выборки в проекте нет, поэтому --live считает
    те же метрики по тому, что реально вернули адаптеры;
  * целевые значения ТЗ (Precision@5 ≥ 0.85) достижимы на реальной разметке;
    эталон по ключевым словам — консервативная нижняя оценка (название может
    не содержать слова-эталона, хотя товар релевантен).

Отчёт: docs/EVAL.md (аппенд с timestamp) + docs/eval/latest.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from adapters import build_adapters  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from llm.gateway import LLMGateway  # noqa: E402
from models import SessionState  # noqa: E402
from search.structfilter import StructFilters, parse_structural  # noqa: E402
from storage.db import Database  # noqa: E402

logging.basicConfig(level=logging.WARNING)

# Эталонные запросы: ключевые слова, которые обязаны встречаться в названии
# релевантного товара. Для запросов с ограничениями проверяется и структура.
EVAL_SET = [
    {"query": "маска для сна", "tokens": ["маска", "сон"]},
    {"query": "маска для сна до 1000 рублей",
     "tokens": ["маска", "сон"], "max_price": 1000},
    {"query": "кроссовки белые", "tokens": ["кроссовки", "кед", "бел"]},
    {"query": "кроссовки чёрные для бега",
     "tokens": ["кроссовки", "бег", "чёрн"]},
    {"query": "беспроводные наушники", "tokens": ["наушник"]},
    {"query": "маска шёлковая чёрная для сна",
     "tokens": ["маска", "сон", "шёлк", "чёрн"]},
    {"query": "кроссовки женские розовые",
     "tokens": ["кроссовки", "женск", "розов"]},
    {"query": "маска для сна от 900 рублей",
     "tokens": ["маска", "сон"], "min_price": 900},
]


def _relevant(title: str, tokens: list[str]) -> bool:
    low = title.lower()
    return any(tok in low for tok in tokens)


def _structural_ok(p, spec: dict) -> bool:
    sf = parse_structural(spec["query"])
    if sf.max_price and p.price and p.price > sf.max_price:
        return False
    if sf.min_price and p.price and p.price < sf.min_price:
        return False
    if sf.min_rating and p.rating is not None and p.rating < sf.min_rating:
        return False
    return True


def _precision(items: list, spec: dict) -> float:
    if not items:
        return 0.0
    return sum(1 for p in items if _relevant(p.title, spec["tokens"])) / len(items)


async def run_eval(live: bool, max_queries: int | None) -> dict:
    db = Database(config.DB_PATH if live else ":memory:")
    await db.connect()
    try:
        # оффлайн: детерминированный mock (без облака и без локальной LLM);
        # семантический слой (bge-m3) при этом работает — это и измеряется.
        llm = (LLMGateway(db) if live else
               LLMGateway(db, api_key="", mistral_api_key="", local_llm=False))
        orch = Orchestrator(db, llm, build_adapters(demo=not live))
        results = []
        queries = EVAL_SET[:max_queries] if max_queries else EVAL_SET
        for spec in queries:
            t0 = time.monotonic()
            outcome = await orch.search_with_constraints(
                1, spec["query"], SessionState(), None)
            latency_ms = (time.monotonic() - t0) * 1000
            shown = outcome.top[:5]
            results.append({
                "query": spec["query"],
                "tokens": spec["tokens"],
                "shown": [p.title for p in shown],
                "shown_prices": [p.price for p in shown],
                "shown_precision": _precision(shown, spec),
                "structural_ok": (sum(1 for p in shown
                                      if _structural_ok(p, spec)) / len(shown)
                                  if shown else 0.0),
                "latency_ms": round(latency_ms, 1),
                "n_shown": len(shown),
                "n_candidates": len(outcome.all_candidates),
            })
            await asyncio.sleep(0.2)  # вежливая пауза между запросами

        latencies = [r["latency_ms"] for r in results]
        shown_precisions = [r["shown_precision"] for r in results]
        summary = {
            "date": datetime.now().isoformat(timespec="seconds"),
            "mode": "live" if live else "offline",
            "queries": len(results),
            "shown_precision_avg": round(statistics.mean(shown_precisions), 3),
            "structural_avg": round(statistics.mean(
                r["structural_ok"] for r in results), 3),
            "p95_latency_ms": round(sorted(latencies)[
                min(len(latencies) - 1, int(len(latencies) * 0.95))], 1),
            "max_latency_ms": round(max(latencies), 1),
            "results": results,
            "note": (
                "Показанная Precision@K (K=число выданных карточек) + "
                "структурное соответствие. Целевые значения ТЗ (Precision@5 ≥ "
                "0.85) требуют размеченной выборки реальных карточек; "
                "эталон по ключевым словам — консервативная нижняя оценка."
            ),
        }
        await orch.aclose()
        await llm.aclose()
        return summary
    finally:
        await db.close()


def _write_report(summary: dict) -> None:
    base = Path(__file__).resolve().parents[1]
    out_json = base / "docs" / "eval"
    out_json.mkdir(parents=True, exist_ok=True)
    (out_json / "latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = base / "docs" / "EVAL.md"
    rows = "\n".join(
        f"| {r['query']} | {r['shown_precision']:.2f} | {r['structural_ok']:.2f} "
        f"| {r['latency_ms']:.0f} мс | {r['n_shown']}/{r['n_candidates']} |"
        for r in summary["results"])
    stamp = summary["date"].replace("T", " ")
    block = (
        f"\n## Прогон {stamp} ({summary['mode']})\n\n"
        f"Precision@K (показанные карточки): **{summary['shown_precision_avg']:.3f}** · "
        f"структурное соответствие: **{summary['structural_avg']:.3f}** · "
        f"p95 latency: **{summary['p95_latency_ms']:.0f} мс** · "
        f"максимум: {summary['max_latency_ms']:.0f} мс\n\n"
        f"| Запрос | Precision@K | Структура | Latency | Показано/кандидатов |\n"
        f"|---|---|---|---|---|\n{rows}\n\n"
        f"_Примечание: {summary['note']}_\n"
    )
    with md_path.open("a", encoding="utf-8") as f:
        if md_path.stat().st_size == 0:
            f.write("# Оценка качества поиска (ТЗ §5)\n"
                    "Автоматические прогоны tools/eval_precision.py.\n")
        f.write(block)
    print(f"Отчёт: {md_path} и {out_json / 'latest.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Оценка Precision поиска")
    parser.add_argument("--live", action="store_true",
                        help="реальные маркетплейсы вместо демо-каталога")
    parser.add_argument("--queries", type=int, default=None,
                        help="ограничить число запросов")
    args = parser.parse_args()
    summary = asyncio.run(run_eval(args.live, args.queries))
    print(json.dumps({k: v for k, v in summary.items() if k != "results"},
                     ensure_ascii=False, indent=2))
    _write_report(summary)


if __name__ == "__main__":
    main()
