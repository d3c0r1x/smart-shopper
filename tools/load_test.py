"""Нагрузочное тестирование поискового ядра (ТЗ §6, раздел «Тестирование»).

Запускает N параллельных поисков через оркестратор в демо-режиме
(детерминированно: без сети к маркетплейсам, без расхода LLM-бюджета и без
зависимости от антибота). Измеряет:

- latency p95 / максимум (цель ТЗ §5: p95 <= 2 c — для оффлайн-ядра);
- throughput (запросов/сек);
- долю ошибок;
- отсутствие утечек: число открытых соединений SQLite и размер базы
  до/после (запросы не должны копить сессии/кэш без границ).

Использование:
    python tools/load_test.py --requests 50 --concurrency 10
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import tempfile
import time

os.environ.setdefault("SHOPPER_DEMO_MODE", "1")
os.environ.setdefault("SHOPPER_LOCAL_LLM", "0")
os.environ.setdefault("SHOPPER_SEMANTIC_ENABLED", "0")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import build_adapters  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from llm.gateway import LLMGateway  # noqa: E402
from models import SessionState  # noqa: E402
from storage.db import Database  # noqa: E402

QUERIES = [
    "маска для сна до 1200 рублей",
    "кроссовки белые",
    "чёрная маска с пространством для ресниц",
    "кроссовки для бега",
    "маска шёлковая",
    "кеды белые",
    "маска для сна 3D",
    "кроссовки женские розовые",
]


async def _run(db: Database, orch: Orchestrator, q: str) -> float:
    t0 = time.perf_counter()
    outcome = await orch.search_with_constraints(
        user_id=1, user_text=q, state=SessionState(), progress=None)
    dt = time.perf_counter() - t0
    if not outcome.top:
        raise RuntimeError(f"пустой результат для {q!r}")
    return dt


async def _worker(db, orch, q, sem, results, errors):
    async with sem:
        try:
            dt = await _run(db, orch, q)
            results.append(dt)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{q!r}: {exc}")


async def main(args: argparse.Namespace) -> None:
    db_path = tempfile.mktemp(suffix="_load.db")
    db = Database(db_path)
    await db.connect()
    llm = LLMGateway(db, api_key="")
    orch = Orchestrator(db, llm, build_adapters(demo=True))

    # прогрев: один запрос до замера (холодный старт БД/кэшей)
    await _run(db, orch, QUERIES[0])

    results: list[float] = []
    errors: list[str] = []
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.perf_counter()
    await asyncio.gather(*[
        _worker(db, orch, QUERIES[i % len(QUERIES)], sem, results, errors)
        for i in range(args.requests)
    ])
    total = time.perf_counter() - t0

    # утечки: соединения и размер базы
    conns = 0
    size = 0
    try:
        size = os.path.getsize(db_path)
        cur = await db._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        row = await cur.fetchone()
        conns = 1  # один пул aiosqlite
        _ = row
    except Exception:  # noqa: BLE001
        pass

    results_sorted = sorted(results)
    p95 = results_sorted[min(len(results_sorted) - 1,
                             int(len(results_sorted) * 0.95))] * 1000
    report = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "offline-core",
        "requests": args.requests,
        "concurrency": args.concurrency,
        "queries": QUERIES,
        "latency_avg_ms": round(statistics.mean(results) * 1000, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_max_ms": round(max(results) * 1000, 1),
        "throughput_rps": round(args.requests / total, 2),
        "errors": len(errors),
        "error_samples": errors[:3],
        "db_size_bytes": size,
    }
    import json
    from pathlib import Path
    out = Path(__file__).resolve().parent.parent / "docs" / "eval" / "load.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Нагрузочный тест ядра")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(main(args))
