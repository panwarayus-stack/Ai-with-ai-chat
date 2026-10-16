"""Shared HTTP client for all public API providers.

Wraps :mod:`requests` with:

* a single reusable :class:`requests.Session` per client,
* default timeouts on every request (no unbounded network calls),
* limited retries with exponential backoff for 429/5xx/connection errors,
* ``Retry-After`` handling (capped), and
* JSON / binary helpers with typed errors that commands can render safely.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 10.0


class HttpError(Exception):
    """Base class for HTTP-layer failures."""


class HttpTimeoutError(HttpError):
    """The request exceeded the configured timeout."""


class HttpNetworkError(HttpError):
    """DNS / connection level failure."""


class HttpStatusError(HttpError):
    """The server answered with a non-2xx status."""

    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body[:500]

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429


class HttpClient:
    """Thin requests wrapper shared by every service client."""

    def __init__(
        self,
        timeout: float = 20.0,
        retries: int = 2,
        backoff: float = 0.6,
        user_agent: str = "InstagramBot/1.0",
        session: Optional[requests.Session] = None,
    ):
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.session.headers.setdefault("Accept", "application/json, image/*;q=0.8, */*;q=0.5")

    # ------------------------------------------------------------------ #
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[Any] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        raw: bool = False,
    ) -> Tuple[int, bytes, requests.Response]:
        """Perform a request with bounded retries.

        Returns ``(status, body_bytes, response)``. Raises only on transport
        errors or on final non-2xx statuses.
        """
        last_error: Optional[Exception] = None
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=timeout or self.timeout,
                )
            except requests.Timeout as exc:
                last_error = HttpTimeoutError(f"Request to {url} timed out")
                logger.warning("Timeout (%s) attempt %d/%d: %s", method, attempt, attempts, url)
                if attempt < attempts:
                    self._sleep(attempt)
                    continue
                raise last_error from exc
            except requests.RequestException as exc:
                last_error = HttpNetworkError(f"Network error for {url}: {exc}")
                logger.warning(
                    "Network error (%s) attempt %d/%d: %s", method, attempt, attempts, exc
                )
                if attempt < attempts:
                    self._sleep(attempt)
                    continue
                raise last_error from exc

            status = response.status_code
            if status in RETRY_STATUSES and attempt < attempts:
                delay = self._retry_after(response, attempt)
                logger.warning("HTTP %d for %s; retrying in %.1fs", status, url, delay)
                time.sleep(delay)
                continue
            if raw:
                return status, response.content, response
            if not (200 <= status < 300):
                raise HttpStatusError(status, url, response.text)
            return status, response.content, response

        raise last_error or HttpNetworkError(f"Request to {url} failed")  # pragma: no cover

    # ------------------------------------------------------------------ #
    def get_json(self, url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        _, body, _ = self.request("GET", url, params=params, headers=headers)
        return self._decode(url, body)

    def post_json(
        self,
        url: str,
        payload: Any,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        _, body, _ = self.request(
            "POST", url, params=params, json_body=payload, headers=headers, timeout=timeout
        )
        return self._decode(url, body)

    def get_bytes(self, url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> Tuple[bytes, str]:
        status, body, response = self.request(
            "GET", url, params=params, headers=headers, raw=True
        )
        if status != 200:
            raise HttpStatusError(status, url, "")
        if len(body) > 15 * 1024 * 1024:  # hard cap: never buffer huge files
            raise HttpError(f"Refusing to download more than 15 MB from {url}")
        return body, response.headers.get("Content-Type", "")

    def post_graphql(
        self,
        url: str,
        data: Optional[dict] = None,
        *,
        headers: Optional[dict] = None,
    ) -> Any:
        """POST a JSON GraphQL body (AniList-style endpoint)."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 2):
            try:
                response = self.session.post(
                    url, json=data, headers=headers, timeout=self.timeout
                )
            except requests.Timeout as exc:
                last_error = HttpTimeoutError(f"Request to {url} timed out")
                if attempt <= self.retries:
                    self._sleep(attempt)
                    continue
                raise last_error from exc
            except requests.RequestException as exc:
                last_error = HttpNetworkError(f"Network error for {url}: {exc}")
                if attempt <= self.retries:
                    self._sleep(attempt)
                    continue
                raise last_error from exc
            if response.status_code in RETRY_STATUSES and attempt <= self.retries:
                time.sleep(self._retry_after(response, attempt))
                continue
            if not (200 <= response.status_code < 300):
                raise HttpStatusError(response.status_code, url, response.text)
            return self._decode(url, response.content)
        raise last_error or HttpNetworkError(f"Request to {url} failed")  # pragma: no cover

    # ------------------------------------------------------------------ #
    @staticmethod
    def _decode(url: str, body: bytes) -> Any:
        try:
            return requests.models.complexjson.loads(body.decode("utf-8"))
        except Exception as exc:
            raise HttpError(f"Invalid JSON from {url}") from exc

    def _sleep(self, attempt: int) -> None:
        time.sleep(min(MAX_RETRY_AFTER, self.backoff * (2 ** (attempt - 1))))

    @staticmethod
    def _retry_after(response: requests.Response, attempt: int) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(MAX_RETRY_AFTER, max(0.2, float(header)))
            except ValueError:
                pass
        return min(MAX_RETRY_AFTER, 0.6 * (2 ** (attempt - 1)))
