"""Опциональная интеграция OpenTelemetry (ТЗ §5: «APM мониторинг»).

Метрики p95 latency считаются и без OTel (встроенная миддлварь в web.py),
но ТЗ явно называет OpenTelemetry методом измерения. Этот модуль делает
интеграцию реальной и при этом безопасной:

* если пакеты не установлены или SHOPPER_OTEL_ENDPOINT не задан — все
  вызовы превращаются в no-op, проект работает как раньше (тесты
  детерминированные, без сети и без внешних зависимостей);
* если задан SHOPPER_OTEL_ENDPOINT (например, http://localhost:4318/v1) —
  стартует tracer provider с OTLP-экспортером: каждый HTTP-запрос API
  получает span, а задержка пишется в Histogram `api.request.duration`.

Лицензия: opentelemetry-python распространяется под Apache-2.0 (ТЗ §3 —
допустимая лицензия). Пакеты указаны в requirements.txt, чтобы венв
собирался воспроизводимо.
"""
from __future__ import annotations

import logging
import time
from contextlib import nullcontext

import config

logger = logging.getLogger(__name__)

_TRACER = None
_histogram = None


def _build() -> bool:
    """Инициализация OTel при наличии пакетов и заданном эндпоинте."""
    global _TRACER, _histogram
    endpoint = config.OTEL_ENDPOINT
    if not endpoint:
        return False
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter)
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter)
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": "smart-shopper"})

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/traces")))
        trace.set_tracer_provider(tracer_provider)
        _TRACER = trace.get_tracer("smart-shopper")

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint + "/metrics"))
        meter_provider = MeterProvider(resource=resource,
                                       metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        _histogram = meter_provider.get_meter("smart-shopper").create_histogram(
            name="api.request.duration",
            unit="ms",
            description="Задержка HTTP-запросов API",
        )
        logger.info("OpenTelemetry: экспорт в %s", endpoint)
        return True
    except Exception as exc:  # pragma: no cover — защита от любых сбоев SDK
        logger.warning("OpenTelemetry не запущен: %s", exc)
        return False


def start_span(name: str):
    """Контекст-менеджер спана или no-op, если телеметрия выключена.

    Используется в web.py: каждый запрос API оборачивается в span
    `api.request.<method>`, длительность пишется в Histogram.
    """
    if _TRACER is None:
        if not _build():
            return nullcontext()
    try:
        return _TRACER.start_as_current_span(name)
    except Exception:  # pragma: no cover
        return nullcontext()


def record_duration_ms(value_ms: float) -> None:
    if _histogram is not None:
        try:
            _histogram.record(value_ms)
        except Exception:  # pragma: no cover
            pass


def elapsed_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
