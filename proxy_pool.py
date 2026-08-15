# -*- coding: utf-8 -*-
"""Пул прокси из подписки Happ (xray-серверы → локальные socks-порты).

Подписка Happ — base64-список vless/hysteria2-ссылок (у пользователя
306 серверов, 63 уникальных хоста, разные страны). Каждый сервер —
отдельный выходной IP, что позволяет обходить антибот маркетплейсов:
площадка блокирует один IP — пул переключается на следующий.

Работа:
1. `update()` — скачивает подписку по `SHOPPER_SUBSCRIPTION_URL`,
   декодирует base64, парсит vless-ссылки и кэширует в `happ_servers.json`
   (файл в .gitignore — внутри UUID и ключи reality).
2. `_ensure_launched()` — лениво запускает N инстансов xray (путь из
   `config.XRAY_BINARY_PATH`, см. SHOPPER_XRAY_PATH), каждый на
   своём socks-порту 10811+ с одним сервером подписки в outbound.
3. `next()` — круговой выбор прокси («socks5://127.0.0.1:PORT»).
4. `down()` — останавливает инстансы.

Требования: Happ установлен (xray.exe), `SHOPPER_SUBSCRIPTION_URL` в .env.
Если пул не удалось собрать (нет подписки/серверов) — адаптеры честно
пробуют работать с `SHOPPER_PROXY`, а при его отсутствии — напрямую.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import config

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
XRAY_EXE = Path(config.XRAY_BINARY_PATH)  # переопределяется SHOPPER_XRAY_PATH
CACHE_FILE = BASE_DIR / "happ_servers.json"

BASE_PORT = 10811


def _alive(port: int, timeout: float = 10.0) -> bool:
    """Жив ли socks-прокси: получает выходной IP через curl."""
    try:
        proc = subprocess.run(
            ["curl", "-s", "-m", str(int(timeout)),
             "--socks5-hostname", f"127.0.0.1:{port}",
             "https://api.ipify.org"],
            capture_output=True, timeout=timeout + 5)
        return bool(proc.stdout.strip())
    except Exception:
        return False


def parse_vless(url: str) -> dict:
    """Разбирает vless://uuid@host:port?params#name → параметры xray."""
    m = re.match(r"vless://([^@]+)@([^:]+):(\d+)\?(.*?)(#.*)?$", url)
    if not m:
        raise ValueError(f"не удалось разобрать vless: {url[:60]}…")
    uuid, host, port, params, frag = m.groups()
    q = dict(urllib.parse.parse_qsl(params))
    return {
        "uuid": uuid, "host": host, "port": int(port),
        "flow": q.get("flow", ""),
        "network": q.get("type", "tcp"),
        "security": q.get("security", "none"),
        "sni": q.get("sni", ""),
        "fp": q.get("fp", "chrome"),
        "pbk": q.get("pbk", ""),
        "sid": q.get("sid", ""),
        "name": urllib.parse.unquote(frag.lstrip("#")) if frag else host,
    }


def build_config(srv: dict, port: int) -> dict:
    """xray-конфиг: socks-инбаунд 127.0.0.1:{port} → vless-аутбаунд."""
    user = {"id": srv["uuid"], "encryption": "none"}
    if srv.get("flow"):
        user["flow"] = srv["flow"]
    outbound: dict = {
        "protocol": "vless",
        "settings": {"vnext": [{
            "address": srv["host"], "port": srv["port"], "users": [user],
        }]},
    }
    stream: dict = {"network": srv["network"], "security": srv["security"]}
    if srv["security"] == "reality":
        stream["realitySettings"] = {
            "serverName": srv["sni"], "fingerprint": srv["fp"],
            "publicKey": srv["pbk"], "shortId": srv["sid"],
        }
    outbound["streamSettings"] = stream
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1", "port": port, "protocol": "socks",
            "settings": {"udp": True},
        }],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
    }


def _download(url: str) -> bytes:
    """Скачивает подписку (urllib → fallback на curl для медленных CDN)."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read()
    except Exception:
        proc = subprocess.run(
            ["curl", "-s", "-m", "40", url], capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError("не удалось скачать подписку")
        return proc.stdout


def fetch_servers(url: str) -> list[dict]:
    """Скачивает подписку, декодирует base64, парсит vless-ссылки."""
    raw = _download(url)
    dec = base64.b64decode(raw + b"=" * (-len(raw) % 4))
    txt = dec.decode("utf-8", errors="replace")
    servers = []
    seen_hosts: set[str] = set()
    for line in txt.splitlines():
        line = line.strip()
        if not line.startswith("vless://"):
            continue
        try:
            srv = parse_vless(line)
        except ValueError:
            continue
        # «Авто»-узлы маршрутизируют через другие серверы — пропускаем,
        # берём по одному серверу на уникальный хост
        if "авто" in srv["name"].lower():
            continue
        if srv["host"] in seen_hosts:
            continue
        seen_hosts.add(srv["host"])
        servers.append(srv)
    return servers


class HappProxyPool:
    """Пул socks-прокси на серверах подписки Happ (ротация выходных IP)."""

    def __init__(self, url: str = "", size: int = 0,
                 base_port: int = BASE_PORT) -> None:
        self._url = url or config.SUBSCRIPTION_URL
        self._size = size or config.POOL_SIZE
        self._base_port = base_port
        self._servers: list[dict] = []
        self._procs: dict[int, subprocess.Popen] = {}
        self._cursor = 0
        self._launched = False

    # ── загрузка серверов ──────────────────────────────────────────
    def _load_servers(self) -> list[dict]:
        if self._servers:
            return self._servers
        if CACHE_FILE.exists():
            try:
                self._servers = json.loads(
                    CACHE_FILE.read_text(encoding="utf-8"))
                logger.info("Пул: %d серверов из кэша", len(self._servers))
                return self._servers
            except Exception:
                pass
        if self._url:
            try:
                self._servers = fetch_servers(self._url)
                CACHE_FILE.write_text(
                    json.dumps(self._servers, ensure_ascii=False),
                    encoding="utf-8")
                logger.info("Пул: %d серверов из подписки", len(self._servers))
            except Exception as exc:
                logger.warning("Пул: подписка недоступна: %s", exc)
        return self._servers

    # ── запуск xray-инстансов ──────────────────────────────────────
    def _ensure_launched(self) -> None:
        if self._launched:
            return
        if not XRAY_EXE.exists():
            logger.warning("Пул: xray.exe не найден (%s)", XRAY_EXE)
            self._launched = True
            return
        servers = self._load_servers()
        if not servers:
            logger.warning("Пул: нет серверов подписки — пул пуст")
            self._launched = True
            return
        n = min(self._size, len(servers))
        for i in range(n):
            port = self._base_port + i
            if port in self._procs and self._procs[port].poll() is None:
                continue
            if _alive(port, timeout=6):
                # порт уже отвечает (инстанс от прошлого пула) — принимаем
                self._procs[port] = None
                continue
            try:
                cfg = build_config(servers[i], port)
                cfg_path = BASE_DIR / f"_xray_{port}.json"
                cfg_path.write_text(json.dumps(cfg, ensure_ascii=False),
                                    encoding="utf-8")
                proc = subprocess.Popen(
                    [str(XRAY_EXE), "-c", str(cfg_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._procs[port] = proc
            except Exception as exc:
                logger.warning("Пул: не удалось запустить xray на %d: %s",
                               port, exc)
        time.sleep(3)  # дать xray подняться
        # health-check: оставляем только живые (мёртвые серверы подписки
        # отсекаем, чтобы ротация не сжигала попытки на них)
        for port in list(self._procs):
            proc = self._procs[port]
            if (proc is not None and proc.poll() is not None) or not _alive(port):
                if proc is not None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                self._procs.pop(port, None)
                logger.info("Пул: сервер на %d не отвечает — исключён", port)
        self._launched = True
        logger.info("Пул: запущено %d xray-инстансов (порты %d-%d)",
                    len(self._procs), self._base_port, self._base_port + n - 1)

    # ── выбор прокси ───────────────────────────────────────────────
    def next(self) -> str:
        """Следующий прокси по кругу: «socks5://127.0.0.1:PORT»."""
        self._ensure_launched()
        if not self._procs:
            return ""
        ports = [p for p, proc in self._procs.items()
                 if proc is None or proc.poll() is None]
        if not ports:
            logger.warning("Пул: все xray-инстансы мертвы")
            return ""
        port = ports[self._cursor % len(ports)]
        self._cursor += 1
        return f"socks5://127.0.0.1:{port}"

    def proxies(self) -> list[str]:
        self._ensure_launched()
        return [f"socks5://127.0.0.1:{p}"
                for p, proc in self._procs.items()
                if proc is None or proc.poll() is None]

    def down(self) -> None:
        for proc in self._procs.values():
            if proc is None:
                continue
            try:
                proc.terminate()
            except Exception:
                pass
        self._procs.clear()
        self._launched = False


_pool_instance: HappProxyPool | None = None


def get_pool() -> HappProxyPool | None:
    """Единственный пул на процесс (создаётся при первом использовании)."""
    global _pool_instance
    if _pool_instance is None:
        if not config.PROXY_POOL:
            return None
        _pool_instance = HappProxyPool()
    return _pool_instance
