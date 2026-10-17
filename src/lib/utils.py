"""Small shared helpers used across commands and services."""

from __future__ import annotations

import unicodedata
from typing import Iterable, Optional


def ellipsize(text: str, limit: int = 200, suffix: str = "…") -> str:
    """Collapse whitespace and shorten *text* to *limit* characters."""
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - len(suffix))].rstrip() + suffix


def safe_str(value: object, fallback: str = "") -> str:
    """Stringify *value* safely (never raises on odd input)."""
    if value is None:
        return fallback
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return fallback


def first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return ""


def normalize_name(name: str) -> str:
    """NFC-normalise and collapse whitespace (for comparisons)."""
    return " ".join(unicodedata.normalize("NFC", name or "").split())


def titles_match(a: str, b: str) -> bool:
    """Loose title equality: casefolded, punctuation-tolerant."""
    def norm(s: str) -> str:
        s = normalize_name(s).casefold()
        return "".join(ch for ch in s if ch.isalnum() or ch.isspace())

    return norm(a) == norm(b) and bool(norm(a))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_count(value: object) -> str:
    """Human-friendly integer formatting with thousands separators."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return safe_str(value, "?")


def format_percent(probability: object, digits: int = 1) -> str:
    try:
        return f"{float(probability) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "?"


def chunked(items: Iterable, size: int):
    """Yield lists of at most *size* items."""
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class HourlyBudget:
    """Rolling-window budget (used for Instagram replies per hour).

    Thread-safe and clock-injectable for tests. ``allow()`` records a use and
    returns False once the limit is reached inside the window.
    """

    def __init__(self, limit: int, window_seconds: float = 3600.0, clock=None):
        import threading
        import time as _time
        from collections import deque

        self.limit = max(1, int(limit))
        self.window = float(window_seconds)
        self._clock = clock or _time.monotonic
        self._stamps = deque()
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= self.window:
            self._stamps.popleft()

    def allow(self) -> bool:
        """Record a use if budget remains; return whether it is permitted."""
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            if len(self._stamps) >= self.limit:
                return False
            self._stamps.append(now)
            return True

    def remaining(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return self.limit - len(self._stamps)

    def reset(self) -> None:
        with self._lock:
            self._stamps.clear()

    def set_limit(self, limit: int) -> None:
        """Change the hourly cap at runtime (dashboard settings)."""
        with self._lock:
            self.limit = max(1, int(limit))
