"""Shared live state between the bot process and the web dashboard.

Everything here goes through the session store (files under ``auth/state/``
or Redis), the same store that already carries live credentials and OTP
codes — so values saved in the web dashboard are picked up by the running
bot without a restart, exactly like the SSH CLI commands.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from src.services.session_store import SessionStore, load_json, save_json

STATUS_KEY = "bot:status"
SETTINGS_KEY = "bot:settings"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# Bot status (shown on the dashboard)
# ---------------------------------------------------------------------- #
def publish_status(store: SessionStore, state: str, detail: str = "") -> None:
    """Best-effort write of what the bot is doing right now."""
    try:
        save_json(
            store,
            STATUS_KEY,
            {"state": state, "detail": detail, "at": time.time()},
        )
    except Exception:  # pragma: no cover - store failures must never hurt the bot
        pass


def read_status(store: SessionStore) -> dict:
    data = load_json(store, STATUS_KEY, None)
    if isinstance(data, dict):
        return data
    return {"state": "unknown", "detail": "", "at": 0}


# ---------------------------------------------------------------------- #
# Runtime settings (gentle-mode knobs editable on the dashboard)
# ---------------------------------------------------------------------- #
# key -> (kind, minimum, maximum). Kinds: int, float, bool.
SETTING_SPECS = {
    "ig_poll_interval": ("int", 5, 3600),
    "ig_idle_after_minutes": ("int", 1, 1440),
    "ig_idle_poll_interval": ("int", 10, 7200),
    "ig_reply_delay_min": ("int", 0, 60),
    "ig_reply_delay_max": ("int", 0, 120),
    "ig_min_request_gap": ("float", 0.0, 600.0),
    "ig_max_replies_per_hour": ("int", 1, 300),
    "ig_mark_seen": ("bool", None, None),
    "ig_reply_in_groups": ("bool", None, None),
}


def validate_settings(raw: dict) -> tuple:
    """Return (clean_settings, errors) for a raw form mapping."""
    clean, errors = {}, []
    for key, (kind, low, high) in SETTING_SPECS.items():
        if key not in raw:
            continue
        value = str(raw.get(key, "")).strip()
        if value == "":
            continue  # keep current
        try:
            if kind == "int":
                parsed = int(value)
            elif kind == "float":
                parsed = float(value)
            else:  # bool
                parsed = value.lower() in ("1", "true", "yes", "on")
                clean[key] = parsed
                continue
        except ValueError:
            errors.append(f"{key}={value!r} is not a {kind}")
            continue
        if low is not None and not (low <= parsed <= high):
            errors.append(f"{key} must be between {low} and {high}")
            continue
        clean[key] = parsed
    return clean, errors


def load_settings(store: SessionStore) -> dict:
    data = load_json(store, SETTINGS_KEY, None)
    return data if isinstance(data, dict) else {}


def save_settings(store: SessionStore, settings: dict) -> None:
    if settings:
        save_json(store, SETTINGS_KEY, settings)
    else:
        store.delete(SETTINGS_KEY)


class RuntimeSettings:
    """Cached view of the dashboard-editable settings (refreshed per poll)."""

    def __init__(self, store: SessionStore):
        self._store = store
        self._data: dict = {}
        self.refresh()

    def refresh(self) -> None:
        try:
            self._data = load_settings(self._store)
        except Exception:  # pragma: no cover - keep last known settings
            pass

    def get(self, key: str, default):
        return self._data.get(key, default)


# ---------------------------------------------------------------------- #
# Log buffer for the dashboard's log viewer
# ---------------------------------------------------------------------- #
class LogBuffer(logging.Handler):
    """Ring buffer of recent log lines for the web dashboard."""

    def __init__(self, maxlen: int = 500):
        super().__init__()
        self._lines: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - defensive
            return
        with self._lock:
            self._lines.append(line)

    def tail(self, n: int = 200) -> list:
        with self._lock:
            return list(self._lines)[-n:]


# One shared buffer: logging setup attaches it, the dashboard reads it.
LOG_BUFFER = LogBuffer()
LOG_BUFFER.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
