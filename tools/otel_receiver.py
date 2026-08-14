#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Минимальный OTLP/HTTP приёмник для проверки живого экспорта.

Ловит POST на /v1/traces и /v1/metrics (протобуф, не декодируем — только
считаем байты и заголовки) и печатает в консоль. Используется для
проверки, что бот реально отправляет телеметрию OpenTelemetry:

    python tools/otel_receiver.py --port 4318

Затем запустите бота с SHOPPER_OTEL_ENDPOINT=http://127.0.0.1:4318/v1 —
на каждый HTTP-запрос API приёмник покажет POST /v1/traces и периодически
POST /v1/metrics (BatchSpanProcessor/PeriodicExportingMetricReader).
"""
import argparse
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {self.command} {self.path} "
              f"bytes={len(body)} "
              f"content-type={self.headers.get('Content-Type', '?')}",
              flush=True)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _handle
    do_GET = _handle
    do_PUT = _handle

    def log_message(self, *args):  # тишина в stderr
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4318)
    args = ap.parse_args()
    print(f"OTLP/HTTP receiver на 127.0.0.1:{args.port} — жду /v1/traces и /v1/metrics",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
