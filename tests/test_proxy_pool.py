# -*- coding: utf-8 -*-
"""Юнит-тесты пула прокси подписки Happ (без сети и без xray)."""
from proxy_pool import build_config, parse_vless

SAMPLE = ("vless://78003c5e-8e58-49bb-b480-8e898c8ca192@23.229.66.50:443"
          "?encryption=none&flow=xtls-rprx-vision&type=tcp&headerType=none"
          "&security=reality&sni=uk.quattro-tech.ru&fp=qq"
          "&pbk=10rVZPoOUP1TlQviIAsQ_jAROX0fRQxH0C92nq_zGQc"
          "&sid=43dcff53849b81e6"
          "#%F0%9F%87%AC%F0%9F%87%A7%20UK")


def test_parse_vless():
    srv = parse_vless(SAMPLE)
    assert srv["host"] == "23.229.66.50"
    assert srv["port"] == 443
    assert srv["uuid"].startswith("78003c5e")
    assert srv["flow"] == "xtls-rprx-vision"
    assert srv["security"] == "reality"
    assert srv["sni"] == "uk.quattro-tech.ru"
    assert srv["pbk"].startswith("10rVZPoOUP1")
    assert srv["sid"] == "43dcff53849b81e6"
    assert "UK" in srv["name"]


def test_build_config_socks_inbound():
    srv = parse_vless(SAMPLE)
    cfg = build_config(srv, 10821)
    inbound = cfg["inbounds"][0]
    assert inbound["protocol"] == "socks"
    assert inbound["port"] == 10821
    assert inbound["listen"] == "127.0.0.1"
    out = cfg["outbounds"][0]
    assert out["protocol"] == "vless"
    vnext = out["settings"]["vnext"][0]
    assert vnext["address"] == "23.229.66.50"
    assert vnext["users"][0]["flow"] == "xtls-rprx-vision"
    assert out["streamSettings"]["security"] == "reality"
    rs = out["streamSettings"]["realitySettings"]
    assert rs["publicKey"] == srv["pbk"]
    assert rs["shortId"] == srv["sid"]
    # фолбэк на direct всегда есть
    assert cfg["outbounds"][1]["protocol"] == "freedom"


def test_xray_exe_honors_config_override(monkeypatch):
    """Путь к xray берётся из config.XRAY_BINARY_PATH (env SHOPPER_XRAY_PATH)."""
    import importlib
    import config as cfg
    import proxy_pool
    monkeypatch.setattr(cfg, "XRAY_BINARY_PATH", "xray")
    importlib.reload(proxy_pool)
    assert str(proxy_pool.XRAY_EXE) == "xray"
