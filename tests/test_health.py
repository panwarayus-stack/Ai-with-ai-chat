"""Platform health endpoint: the deploy health check must pass.

The deploy platform probes the service over HTTP before calling a deploy
live; without a listener it reports "did not pass its health check" and
aborts — even when the bot is behaving correctly (e.g. patiently waiting
out an Instagram rate limit). These tests lock in the tiny always-200
endpoint and its port resolution.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from config import Config
from src.lib.health import HealthServer


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_health_server_answers_200_on_any_path():
    server = HealthServer(0)  # ephemeral port
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        for path in ("/", "/health", "/some/random/path"):
            status, body = _get(base + path)
            assert status == 200
            assert body == {"status": "ok"}
    finally:
        server.stop()


def test_health_server_stop_is_idempotent_and_safe():
    # stop() before start() must be safe and final (abandons the socket).
    abandoned = HealthServer(0)
    abandoned.stop()
    abandoned.start()  # no-op — never starts after being stopped

    server = HealthServer(0)
    server.start()
    server.start()  # double-start is a no-op
    port = server.port
    server.stop()
    server.stop()  # idempotent
    # Port is released — nothing should answer anymore.
    try:
        _get(f"http://127.0.0.1:{port}/")
        raised = False
    except (urllib.error.URLError, ConnectionError, OSError):
        raised = True
    assert raised, "server should stop answering after stop()"


def test_health_port_resolution_prefers_health_port_over_port():
    cfg = Config.from_env(environ={"HEALTH_PORT": "9001", "PORT": "1234"})
    assert cfg.health_port == 9001

    cfg = Config.from_env(environ={"PORT": "1234"})
    assert cfg.health_port == 1234  # platform-managed PORT

    cfg = Config.from_env(environ={})
    assert cfg.health_port == 8080  # sensible default

    cfg = Config.from_env(environ={"HEALTH_PORT": "0"})
    assert cfg.health_port == 0  # disabled

    cfg = Config.from_env(environ={"HEALTH_PORT": "not-a-port"})
    assert cfg.health_port == 8080  # invalid falls back
    assert any("not a number" in w for w in cfg.warnings)


def test_application_starts_and_stops_health_server():
    import app as app_mod

    # Bind an ephemeral port through the app: use a free port.
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    cfg = Config.from_env(
        environ={
            "IG_USERNAME": "t",
            "IG_PASSWORD": "p",
            "HEALTH_PORT": str(free_port),
            "IG_REPLY_DELAY_MIN": "0",
            "IG_REPLY_DELAY_MAX": "0",
            "IG_MIN_REQUEST_GAP": "0",
        }
    )
    application = app_mod.Application(cfg)
    application.build()
    application._start_health_server()
    try:
        assert application._health_server is not None
        status, body = _get(f"http://127.0.0.1:{free_port}/health")
        assert status == 200 and body["status"] == "ok"
    finally:
        application.shutdown()

    # Disabled: HEALTH_PORT=0 must not start anything.
    cfg_off = Config.from_env(
        environ={"IG_USERNAME": "t", "IG_PASSWORD": "p", "HEALTH_PORT": "0"}
    )
    application_off = app_mod.Application(cfg_off)
    application_off.build()
    application_off._start_health_server()
    assert application_off._health_server is None
