"""Persistence backends for the Instagram session and bot state.

Voroa has **no persistent disks** (see the Voroa docs, "Persistent disks"):
anything written to the container filesystem disappears on every deploy. The
bot therefore supports two backends:

* :class:`FileSessionStore` — plain JSON files. Survives process restarts
  within a single deploy; wiped on redeploy.
* :class:`RedisSessionStore` — a managed Key Value (Redis) instance. Survives
  deploys and restarts. Selected automatically when ``REDIS_URL`` is set.

Values are opaque strings (we store JSON documents ourselves), keys are
namespaced (``ig:session``, ``ig:processed``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class SessionStore(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class FileSessionStore:
    """JSON-file backed store (one file per key under *root*)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:  # pragma: no cover - best effort on odd filesystems
            pass

    def _path(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
        return self.root / f"{safe}.json"

    def get(self, key: str) -> Optional[str]:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return None

    def set(self, key: str, value: str) -> None:
        path = self._path(key)
        # Atomic write so a crash never leaves a half-written session.
        fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(value)
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:  # pragma: no cover
                pass
        except OSError as exc:
            logger.warning("Could not write %s: %s", path, exc)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            logger.warning("Could not delete %s: %s", self._path(key), exc)


class RedisSessionStore:
    """Redis-backed store for hosts without persistent disks (Voroa)."""

    def __init__(self, url: str, prefix: str = "igbot:", ttl: Optional[int] = None):
        import redis  # imported lazily; only needed when REDIS_URL is set

        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self.prefix = prefix
        self.ttl = ttl
        # Fail fast with a clear message when the broker is unreachable.
        self._redis.ping()

    def _k(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Optional[str]:
        return self._redis.get(self._k(key))

    def set(self, key: str, value: str) -> None:
        if self.ttl:
            self._redis.set(self._k(key), value, ex=self.ttl)
        else:
            self._redis.set(self._k(key), value)

    def delete(self, key: str) -> None:
        self._redis.delete(self._k(key))


class FallbackStore:
    """Write-through file cache in front of a remote store.

    Keeps the session usable even when Redis blips, and lets the bot fall
    back to file-only behaviour automatically.
    """

    def __init__(self, primary: SessionStore, cache: Optional[FileSessionStore] = None):
        self.primary = primary
        self.cache = cache

    def get(self, key: str) -> Optional[str]:
        try:
            value = self.primary.get(key)
            if value is not None:
                return value
        except Exception as exc:  # pragma: no cover - remote failure
            logger.warning("Primary store read failed for %s: %s", key, exc)
        return self.cache.get(key) if self.cache else None

    def set(self, key: str, value: str) -> None:
        if self.cache:
            self.cache.set(key, value)
        try:
            self.primary.set(key, value)
        except Exception as exc:  # pragma: no cover - remote failure
            logger.warning("Primary store write failed for %s: %s", key, exc)

    def delete(self, key: str) -> None:
        if self.cache:
            self.cache.delete(key)
        try:
            self.primary.delete(key)
        except Exception as exc:  # pragma: no cover
            logger.warning("Primary store delete failed for %s: %s", key, exc)


def build_session_store(redis_url: str, file_root: Path) -> SessionStore:
    """Pick the right backend from configuration."""
    file_store = FileSessionStore(file_root)
    if not redis_url:
        return file_store
    try:
        remote = RedisSessionStore(redis_url)
        logger.info("Using managed Redis for session persistence")
        return FallbackStore(remote, cache=file_store)
    except Exception as exc:
        logger.warning(
            "REDIS_URL is set but Redis is unreachable (%s); falling back to files. "
            "The session will not survive a redeploy.",
            exc,
        )
        return file_store


# ---------------------------------------------------------------------- #
# JSON document helpers (what we actually store)
# ---------------------------------------------------------------------- #
def load_json(store: SessionStore, key: str, default):
    raw = store.get(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Stored JSON for %s is corrupt; ignoring it", key)
        return default


def save_json(store: SessionStore, key: str, value) -> None:
    store.set(key, json.dumps(value))
