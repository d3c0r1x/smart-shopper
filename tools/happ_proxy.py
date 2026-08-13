# -*- coding: utf-8 -*-
"""CLI для пула прокси подписки Happ (см. proxy_pool.py).

Команды:
    update                — скачать подписку (SHOPPER_SUBSCRIPTION_URL),
                            декодировать, закэшировать happ_servers.json
    list                  — список серверов из кэша
    up [N]                — запустить пул из N xray-инстансов (по умолчанию
                            SHOPPER_POOL_SIZE) и проверить выходные IP
    check                 — проверить выходной IP каждого инстанса пула
    down                  — остановить инстансы пула

Пример:
    python tools/happ_proxy.py update
    python tools/happ_proxy.py up 6
    python tools/happ_proxy.py check
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import proxy_pool  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent


def _check_ip(port: int, timeout: float = 15.0) -> str:
    import urllib.request
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({
            "http": f"socks5h://127.0.0.1:{port}",
            "https": f"socks5h://127.0.0.1:{port}",
        }))
    try:
        with opener.open("https://api.ipify.org", timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace").strip()
    except Exception as exc:
        return f"ERR:{str(exc)[:60]}"


def cmd_update() -> int:
    servers = proxy_pool.fetch_servers(config.SUBSCRIPTION_URL)
    proxy_pool.CACHE_FILE.write_text(
        __import__("json").dumps(servers, ensure_ascii=False),
        encoding="utf-8")
    hosts = sorted({s["host"] for s in servers})
    print(f"Серверов: {len(servers)}, уникальных хостов: {len(hosts)}")
    return 0


def cmd_list() -> int:
    if not proxy_pool.CACHE_FILE.exists():
        print("Кэш пуст — сначала `update`")
        return 1
    servers = __import__("json").loads(
        proxy_pool.CACHE_FILE.read_text(encoding="utf-8"))
    for i, s in enumerate(servers[:20]):
        print(f"{i:3}. {s['host']}:{s['port']}  {s['name'][:50]}")
    print(f"... всего {len(servers)}")
    return 0


def cmd_up(n: int) -> int:
    pool = proxy_pool.HappProxyPool(size=n)
    pool._ensure_launched()
    time.sleep(2)
    for proxy in pool.proxies():
        port = int(proxy.rsplit(":", 1)[1])
        print(f"{proxy} → IP={_check_ip(port)}")
    return 0


def cmd_check() -> int:
    pool = proxy_pool.HappProxyPool()
    pool._ensure_launched()
    for proxy in pool.proxies():
        port = int(proxy.rsplit(":", 1)[1])
        print(f"{proxy} → IP={_check_ip(port)}")
    return 0


def cmd_down() -> int:
    pool = proxy_pool.HappProxyPool()
    pool.down()
    print("Пул остановлен")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "update":
        return cmd_update()
    if cmd == "list":
        return cmd_list()
    if cmd == "up":
        n = int(args[1]) if len(args) > 1 else config.POOL_SIZE
        return cmd_up(n)
    if cmd == "check":
        return cmd_check()
    if cmd == "down":
        return cmd_down()
    print("Неизвестная команда:", cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
