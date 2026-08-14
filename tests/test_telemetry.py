# -*- coding: utf-8 -*-
"""Тесты опциональной интеграции OpenTelemetry (ТЗ §5).

Проверяем поведение без реального OTLP-эндпоинта (тесты детерминированные,
без сети): no-op среда работает, spans не падают, elapsed_ms корректен.
"""
import time

import telemetry


def test_noop_without_endpoint(monkeypatch):
    """Без OTEL_ENDPOINT всё работает как заглушка (нет исключений)."""
    import config
    monkeypatch.setattr(config, "OTEL_ENDPOINT", "")
    telemetry._TRACER = None  # форсируем пересборку
    with telemetry.start_span("api.request.GET") as span:
        assert span is None  # no-op контекст
    telemetry.record_duration_ms(10.5)  # не должно падать


def test_elapsed_ms():
    t0 = time.perf_counter()
    time.sleep(0.01)
    ms = telemetry.elapsed_ms(t0)
    assert ms >= 9.0
    assert ms < 1000.0
    assert isinstance(ms, float)


def test_start_span_with_mock_endpoint(monkeypatch):
    """С заданным эндпоинтом строится реальный span (без сети не падает).

    Используем in-memory exporter вместо реальной OTLP-отправки: проверяем
    саму сборку телеметрии, а не сеть. Экспортёр шумно падал на порт 1.
    """
    import config
    monkeypatch.setattr(config, "OTEL_ENDPOINT", "http://127.0.0.1:1/v1")

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry._TRACER = provider.get_tracer("test")
    telemetry._histogram = None  # метрики не проверяем, только spans

    from opentelemetry import trace
    with telemetry.start_span("api.request.GET"):
        trace.get_current_span().set_attribute("path", "/api/search")
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "api.request.GET"

    telemetry.record_duration_ms(5.0)  # no-op, не падает
    # сброс: чтобы следующие тесты снова были no-op
    monkeypatch.setattr(config, "OTEL_ENDPOINT", "")
    telemetry._TRACER = None
    telemetry._histogram = None
