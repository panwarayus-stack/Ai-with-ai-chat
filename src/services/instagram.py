"""Instagram integration built on ``instagrapi`` (Python).

Library choice (investigated October 2026):

* ``dilame/instagram-private-api`` — the reference repository — is a
  **TypeScript/Node.js** library. Using it would require a second runtime,
  which the project brief forbids.
* ``instagrapi`` (PyPI, v3.x, Python >= 3.10) is the maintained Python
  equivalent: login with 2FA support, session persistence
  (``load_settings``/``dump_settings``), direct-message threads and sends,
  account info, and challenge handling. Verified against the installed
  package API.

Limitations (please read the README section "Instagram integration"):

* This is an **unofficial** private-API wrapper. Instagram can rate-limit,
  challenge (checkpoint) or restrict automated accounts at any time.
* The bot never bypasses CAPTCHAs or authentication challenges. When
  Instagram raises one, it logs a clear message and backs off.
* No cookies, passwords, tokens or session settings are ever logged or sent
  to users.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.services.session_store import SessionStore

logger = logging.getLogger(__name__)

SESSION_KEY = "ig:session"
STATE_KEY = "ig:processed"
PROCESSED_MEMORY_LIMIT = 2000  # bounded in-memory id set
PROCESSED_PERSIST_LIMIT = 500  # bounded persisted id set

# Exception names are resolved lazily so the module imports even when
# instagrapi is missing (the bot then refuses to start with a clear error).


@dataclass
class IncomingMessage:
    thread_id: int
    message_id: str
    user_id: int
    username: str
    text: str
    timestamp: int = 0


class InstagramError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InstagramService:
    """Thread-safe wrapper around ``instagrapi.Client``."""

    def __init__(
        self,
        username: str,
        password: str,
        session_file: Path,
        store: SessionStore,
        verification_code: str = "",
    ):
        self.username = username
        self.password = password
        self.session_file = Path(session_file)
        self.store = store
        self.verification_code = verification_code
        self._lock = threading.RLock()
        self._client = None
        self._processed: List[str] = self._load_processed()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @property
    def client(self):
        if self._client is None:
            try:
                from instagrapi import Client
            except ImportError as exc:  # pragma: no cover - guarded at startup
                raise InstagramError(
                    "instagrapi is not installed. Run: pip install -r requirements.txt"
                ) from exc
            self._client = Client()
        return self._client

    def login(self) -> None:
        """Log in, reusing a cached session when Instagram still accepts it."""
        from instagrapi.exceptions import (
            BadCredentials,
            ChallengeError,
            ChallengeRequired,
            ClientError,
            ClientLoginRequired,
        )

        with self._lock:
            self._restore_session()
            try:
                ok = self.client.login(
                    self.username,
                    self.password,
                    verification_code=self.verification_code,
                )
                if not ok:
                    raise InstagramError("Instagram login returned failure without details.")
                self._persist_session()
                logger.info("Instagram login OK for @%s", self.username)
                return
            except (BadCredentials,) as exc:
                raise InstagramError(
                    "Instagram rejected the credentials (BadCredentials). "
                    "Check IG_USERNAME / IG_PASSWORD. If the account uses 2FA, "
                    "set IG_VERIFICATION_CODE to the one-time code."
                ) from exc
            except (ChallengeRequired, ChallengeError) as exc:
                raise InstagramError(
                    "Instagram raised an authentication challenge/checkpoint. "
                    "Open the Instagram app on a trusted device and approve the "
                    "login attempt, then restart the bot. (The bot deliberately "
                    "does not bypass challenges or CAPTCHAs.)"
                ) from exc
            except ClientLoginRequired:
                logger.info("Cached session expired; performing a fresh login")
                self._clear_session()
                try:
                    ok = self.client.login(
                        self.username,
                        self.password,
                        verification_code=self.verification_code,
                    )
                    if not ok:
                        raise InstagramError("Instagram login failed after session reset.")
                    self._persist_session()
                    logger.info("Fresh Instagram login OK for @%s", self.username)
                except ClientError as exc:
                    raise InstagramError(f"Instagram login failed: {exc}") from exc
            except ClientError as exc:
                raise InstagramError(f"Instagram login failed: {exc}") from exc

    def relogin(self) -> None:
        """Re-authenticate from the cached settings after a drop."""
        with self._lock:
            try:
                self._restore_session()
                self.client.relogin()
                self._persist_session()
                logger.info("Instagram relogin OK for @%s", self.username)
            except Exception as exc:
                logger.warning("Relogin failed (%s); attempting full login", exc)
                self.login()

    # ------------------------------------------------------------------ #
    # Session persistence
    # ------------------------------------------------------------------ #
    def _restore_session(self) -> None:
        import json

        raw = self.store.get(SESSION_KEY)
        if raw is None and self.session_file.exists():
            raw = self.session_file.read_text(encoding="utf-8")
        if not raw:
            return
        try:
            settings = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Stored session is corrupt; ignoring it")
            return
        try:
            self.client.set_settings(settings)
            logger.debug("Restored Instagram session from store")
        except Exception as exc:
            logger.warning("Could not restore session: %s", exc)

    def _persist_session(self) -> None:
        import json

        try:
            settings = self.client.get_settings()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not read session settings: %s", exc)
            return
        payload = json.dumps(settings)
        self.store.set(SESSION_KEY, payload)
        # Mirror to the configured file path (skipped when it is the store root).
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            self.session_file.write_text(payload, encoding="utf-8")
            self.session_file.chmod(0o600)
        except OSError as exc:
            logger.debug("Session file mirror failed: %s", exc)

    def _clear_session(self) -> None:
        self.store.delete(SESSION_KEY)
        try:
            self.session_file.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------ #
    # Inbox
    # ------------------------------------------------------------------ #
    def fetch_unread(self, amount: int = 20) -> List[IncomingMessage]:
        """Return unread messages from other users (never our own)."""
        with self._lock:
            threads = self.client.direct_threads(amount=amount, selected_filter="unread")
            me = self._own_user_id()
            messages: List[IncomingMessage] = []
            for thread in threads:
                user_map = {
                    int(getattr(u, "pk", 0)): str(getattr(u, "username", "") or "")
                    for u in (getattr(thread, "users", None) or [])
                }
                for item in thread.messages or []:
                    if item.user_id == me:
                        continue
                    text = (getattr(item, "text", None) or "").strip()
                    if not text:
                        continue
                    messages.append(
                        IncomingMessage(
                            thread_id=int(thread.id),
                            message_id=str(item.id),
                            user_id=int(item.user_id),
                            username=user_map.get(int(item.user_id), "") or str(item.user_id),
                            text=text,
                            timestamp=int(getattr(item, "timestamp", 0) or 0),
                        )
                    )
            # Oldest first so commands execute in order.
            messages.sort(key=lambda m: (m.timestamp, m.message_id))
            return messages

    def mark_thread_seen(self, thread_id: int) -> None:
        with self._lock:
            try:
                self.client.direct_send_seen(thread_id)
            except Exception as exc:
                logger.debug("Could not mark thread %s seen: %s", thread_id, exc)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def send_text(self, thread_id: int, text: str) -> None:
        with self._lock:
            self.client.direct_send(text, thread_ids=[thread_id])

    def send_photo(self, thread_id: int, path: Path, caption: str = "") -> None:
        with self._lock:
            self.client.direct_send_photo(Path(path), thread_ids=[thread_id])
        if caption:
            time.sleep(0.8)  # keep message order natural
            self.send_text(thread_id, caption)

    def account_snapshot(self) -> dict:
        """Public info about the *authenticated* account only."""
        with self._lock:
            info = self.client.account_info()
        return {
            "username": getattr(info, "username", self.username),
            "full_name": getattr(info, "full_name", "") or "",
            "biography": getattr(info, "biography", "") or "",
            "follower_count": getattr(info, "follower_count", None),
            "following_count": getattr(info, "following_count", None),
            "media_count": getattr(info, "media_count", None),
            "is_private": bool(getattr(info, "is_private", False)),
            "is_verified": bool(getattr(info, "is_verified", False)),
            "pk": getattr(info, "pk", None),
        }

    def _own_user_id(self) -> Optional[int]:
        try:
            return int(self.client.user_id)
        except Exception:  # pragma: no cover - defensive
            return None

    # ------------------------------------------------------------------ #
    # Processed-message bookkeeping (bounded + persisted)
    # ------------------------------------------------------------------ #
    def is_processed(self, message_id: str) -> bool:
        return message_id in self._processed

    def mark_processed(self, message_id: str) -> None:
        if message_id in self._processed:
            return
        self._processed.append(message_id)
        if len(self._processed) > PROCESSED_MEMORY_LIMIT:
            self._processed = self._processed[-PROCESSED_MEMORY_LIMIT:]
        self._persist_processed()

    def _load_processed(self) -> List[str]:
        import json

        raw = self.store.get(STATE_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [str(x) for x in data][-PROCESSED_MEMORY_LIMIT:]
        except (json.JSONDecodeError, TypeError):
            return []

    def _persist_processed(self) -> None:
        import json

        self.store.set(STATE_KEY, json.dumps(self._processed[-PROCESSED_PERSIST_LIMIT:]))
