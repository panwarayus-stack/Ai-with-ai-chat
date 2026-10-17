"""Minimal HTTP health endpoint for the deploy platform's readiness probe.

The bot itself is a long-running worker with no web API, but deploy
platforms (Voroa included) commonly health-check a service over HTTP on
the managed ``PORT`` before calling a deploy live. This module serves a
tiny always-200 response so that probe passes — including while the bot
deliberately waits out an Instagram rate limit, which is correct
behaviour and not an unhealthy state.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    """Answer 200 OK to any request — the probe only cares that we're up."""

    server_version = "HealthServer/1.0"

    def _respond(self) -> None:
        body = json.dumps({"status": "ok"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._respond()

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond()

    def do_POST(self) -> None:  # noqa: N802
        self._respond()

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return  # probes are frequent and uninteresting — stay silent


class HealthServer:
    """Tiny always-200 HTTP server on a background thread.

    Use ``port=0`` to bind an ephemeral port (tests). ``start()`` launches
    a daemon thread; ``stop()`` is idempotent and safe to call even if
    ``start()`` never ran.
    """

    def __init__(self, port: int, host: str = "0.0.0.0"):
        self._host = host
        self._httpd = ThreadingHTTPServer((host, int(port)), _HealthHandler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="health-server",
            daemon=True,
        )
        self._started = False
        self._closed = False

    @property
    def port(self) -> int:
        """The actual bound port (useful when constructed with 0)."""
        return self._httpd.server_address[1]

    def start(self) -> None:
        if self._started or self._closed:
            return  # already running, or stopped for good — never restart
        self._thread.start()
        self._started = True
        logger.info(
            "Health endpoint up on http://%s:%s/ — platform probes pass here",
            self._host,
            self.port,
        )

    def stop(self) -> None:
        """Stop for good. Safe to call before start(), and idempotent."""
        if self._started:
            self._httpd.shutdown()  # exits serve_forever loop
            self._thread.join(timeout=5)
            self._started = False
        if not self._closed:
            self._httpd.server_close()
            self._closed = True
