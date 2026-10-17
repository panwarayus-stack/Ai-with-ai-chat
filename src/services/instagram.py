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

Live configuration (no restart needed):

* Credentials can be provided at runtime — dashboard env vars **or** the
  live store (``python app.py --set-credentials ...`` via SSH). When neither
  is present the bot *waits* instead of exiting, so the service stays up and
  reachable over SSH.
* One-time login codes (2FA / verification) can be delivered live the same
  way (``python app.py --set-code <code>``, or the ``IG_VERIFICATION_FILE``),
  and the bot polls for them when Instagram asks.

Limitations (please read the README section "Instagram integration"):

* This is an **unofficial** private-API wrapper. Instagram can rate-limit,
  challenge (checkpoint) or restrict automated accounts at any time.
* The bot never bypasses CAPTCHAs or authentication challenges. When
  Instagram raises one, it logs a clear message and backs off.
* No cookies, passwords, tokens or session settings are ever logged or sent
  to users. Live credentials/codes live under ``auth/`` (git-ignored,
  chmod 600) and in the configured session store.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from src.services.session_store import SessionStore, load_json, save_json

logger = logging.getLogger(__name__)

SESSION_KEY = "ig:session"
STATE_KEY = "ig:processed"
CREDENTIALS_KEY = "ig:credentials"
VERIFICATION_KEY = "ig:verification_code"
PROCESSED_MEMORY_LIMIT = 2000  # bounded in-memory id set
PROCESSED_PERSIST_LIMIT = 500  # bounded persisted id set

_MISSING_CREDS_INSTRUCTIONS = (
    "Instagram credentials are not configured. Provide them either way: "
    "(1) Voroa dashboard -> Service -> Environment -> IG_USERNAME and "
    "IG_PASSWORD (then Restart), or (2) live via SSH, without restarting: "
    "python app.py --set-credentials <username>  (the password is prompted "
    "securely). The bot keeps waiting until they arrive."
)


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
        verification_file: Optional[Path] = None,
        verification_wait: int = 300,
        min_request_gap: float = 5.0,
        reply_in_groups: bool = False,
    ):
        self.env_username = username
        self.env_password = password
        self.session_file = Path(session_file)
        self.store = store
        self.env_verification_code = verification_code
        self.verification_file = Path(verification_file) if verification_file else None
        self.verification_wait = max(0, int(verification_wait))
        self.min_request_gap = max(0.0, float(min_request_gap))
        self.reply_in_groups = bool(reply_in_groups)
        self._lock = threading.RLock()
        self._pace_lock = threading.Lock()
        self._last_request = 0.0
        self._client = None
        self._processed: List[str] = self._load_processed()

    # ------------------------------------------------------------------ #
    # Request pacing (anti-suspension)
    # ------------------------------------------------------------------ #
    def _pace(self) -> None:
        """Enforce a minimum gap between Instagram API calls.

        Keeps the account's request footprint human-like and avoids bursts
        that trigger Instagram's automated checks. Applied to every inbox
        poll and every outgoing send (login happens once and is exempt).
        """
        if self.min_request_gap <= 0:
            return
        with self._pace_lock:
            wait = self._last_request + self.min_request_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    # ------------------------------------------------------------------ #
    # Live configuration: credentials
    # ------------------------------------------------------------------ #
    def provide_credentials(self, username: str, password: str) -> None:
        """Store credentials at runtime (SSH: ``python app.py --set-credentials``)."""
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            raise ValueError("Both a username and a password are required")
        save_json(self.store, CREDENTIALS_KEY, {"username": username, "password": password})
        logger.info("Live Instagram credentials stored for @%s", username)

    def resolve_credentials(self) -> Tuple[str, str]:
        """Live store wins over env vars (it can be updated without a restart)."""
        data = load_json(self.store, CREDENTIALS_KEY, None)
        if isinstance(data, dict):
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "")
            if username and password:
                return username, password
        return self.env_username, self.env_password

    def clear_credentials(self) -> None:
        self.store.delete(CREDENTIALS_KEY)

    def has_credentials(self) -> bool:
        username, password = self.resolve_credentials()
        return bool(username and password)

    def _ensure_credentials(self, timeout: Optional[float] = None, poll: float = 2.0):
        """Return credentials, waiting for live ones when missing.

        With ``timeout=None`` the bot waits indefinitely — the process stays
        up (SSH remains available) until credentials arrive. Waiting performs
        **no** network calls, so repeated restarts cannot trip Instagram.
        """
        if self.has_credentials():
            return self.resolve_credentials()

        deadline = None if timeout is None else time.monotonic() + timeout
        logger.error(_MISSING_CREDS_INSTRUCTIONS)
        last_reminder = time.monotonic()
        while True:
            time.sleep(poll)
            if self.has_credentials():
                logger.info("Live Instagram credentials received — continuing login")
                return self.resolve_credentials()
            if deadline is not None and time.monotonic() >= deadline:
                raise InstagramError(
                    "No Instagram credentials arrived within the wait window. "
                    "Run 'python app.py --set-credentials <username>' via SSH or set "
                    "IG_USERNAME/IG_PASSWORD in the dashboard."
                )
            if time.monotonic() - last_reminder >= 60:
                logger.warning("Still waiting for Instagram credentials… %s", _MISSING_CREDS_INSTRUCTIONS)
                last_reminder = time.monotonic()

    # ------------------------------------------------------------------ #
    # Live configuration: one-time verification code (2FA / OTP)
    # ------------------------------------------------------------------ #
    def provide_verification_code(self, code: str) -> None:
        """Store a one-time code at runtime (SSH: ``python app.py --set-code``)."""
        code = (code or "").strip()
        if not code or not code.isdigit() or len(code) > 10:
            raise ValueError("The verification code should be the numeric code Instagram sent")
        self.store.set(VERIFICATION_KEY, code)
        if self.verification_file is not None:
            try:
                self.verification_file.parent.mkdir(parents=True, exist_ok=True)
                self.verification_file.write_text(code, encoding="utf-8")
                self.verification_file.chmod(0o600)
            except OSError as exc:  # pragma: no cover - odd filesystems
                logger.warning("Could not write %s: %s", self.verification_file, exc)
        logger.info("Live verification code stored")

    def _live_code(self) -> str:
        """A code delivered at runtime (file or store) — never the env value."""
        if self.verification_file is not None:
            try:
                if self.verification_file.exists():
                    value = self.verification_file.read_text(encoding="utf-8").strip()
                    if value:
                        return value
            except OSError:  # pragma: no cover
                pass
        return (self.store.get(VERIFICATION_KEY) or "").strip()

    def resolve_verification_code(self) -> str:
        """Live code first, then the ``IG_VERIFICATION_CODE`` env value."""
        return self._live_code() or self.env_verification_code

    def clear_verification_code(self) -> None:
        """Codes are one-time: remove live copies (env cannot be unset)."""
        self.store.delete(VERIFICATION_KEY)
        if self.verification_file is not None:
            try:
                self.verification_file.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass

    def await_verification_code(self, timeout: Optional[float], poll: float = 5.0) -> str:
        """Poll for a live code (file / store) until one arrives or *timeout*."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self._live_code()
            if code:
                logger.info("Live verification code received")
                return code
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(poll)

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
        """Log in, reusing a cached session when Instagram still accepts it.

        Missing credentials never crash the process: the bot waits for them.
        When Instagram demands a one-time code, the bot waits for a live one
        (``--set-code`` / ``IG_VERIFICATION_FILE``) before retrying once.
        """
        from instagrapi.exceptions import (
            BadCredentials,
            ChallengeError,
            ChallengeRequired,
            ClientError,
            ClientLoginRequired,
            TwoFactorRequired,
        )

        username, password = self._ensure_credentials()

        with self._lock:
            self._restore_session()
            code = self.resolve_verification_code()
            try:
                self._do_login(username, password, code)
                self._on_login_success(username)
                return
            except BadCredentials as exc:
                raise InstagramError(
                    "Instagram rejected the credentials (BadCredentials). "
                    "Check IG_USERNAME / IG_PASSWORD — or update them live via "
                    "SSH: python app.py --set-credentials <username>. If the "
                    "account uses 2FA, provide the code with --set-code."
                ) from exc
            except (ChallengeRequired, ChallengeError) as exc:
                raise InstagramError(
                    "Instagram raised an authentication challenge/checkpoint. "
                    "Open the Instagram app on a trusted device and approve the "
                    "login attempt, then restart the bot. (The bot deliberately "
                    "does not bypass challenges or CAPTCHAs.)"
                ) from exc
            except TwoFactorRequired as exc:
                self._handle_two_factor(username, password, exc)
                return
            except ClientLoginRequired:
                logger.info("Cached session expired; performing a fresh login")
                self._clear_session()
                try:
                    self._do_login(username, password, code)
                    self._on_login_success(username)
                except TwoFactorRequired as exc:
                    self._handle_two_factor(username, password, exc)
                except ClientError as exc2:
                    raise InstagramError(f"Instagram login failed: {exc2}") from exc2
            except ClientError as exc:
                # Some flows surface 2FA as a plain ClientError; retry with a
                # live code when the message makes that plausible.
                if self._looks_like_verification(exc) and self.verification_wait > 0:
                    self._handle_two_factor(username, password, exc)
                    return
                raise InstagramError(f"Instagram login failed: {exc}") from exc

    def _do_login(self, username: str, password: str, code: str) -> None:
        ok = self.client.login(username, password, verification_code=code or "")
        if not ok:
            raise InstagramError("Instagram login returned failure without details.")

    def _on_login_success(self, username: str) -> None:
        self._persist_session()
        # A verification code is strictly one-time.
        self.clear_verification_code()
        logger.info("Instagram login OK for @%s", username)

    def _handle_two_factor(self, username: str, password: str, cause: Exception) -> None:
        """Instagram wants a code: wait for a live one, then retry once."""
        from instagrapi.exceptions import (
            BadCredentials,
            ChallengeError,
            ChallengeRequired,
            ClientError,
        )

        logger.warning(
            "Instagram wants a one-time verification code. Provide it live via "
            "SSH: python app.py --set-code <code>  (or write it to %s), or set "
            "IG_VERIFICATION_CODE and restart. Waiting up to %ss…",
            self.verification_file or "the verification file",
            self.verification_wait,
        )
        code = self.await_verification_code(timeout=float(self.verification_wait), poll=3.0)
        if not code:
            raise InstagramError(
                f"No verification code arrived within {self.verification_wait}s. "
                "Run 'python app.py --set-code <code>' via SSH (or set "
                "IG_VERIFICATION_CODE) and restart the bot."
            ) from cause
        try:
            self._do_login(username, password, code)
        except (BadCredentials, ChallengeError, ChallengeRequired):
            raise
        except ClientError as exc:
            self.clear_verification_code()  # it was stale — drop it
            raise InstagramError(
                f"Instagram rejected the verification code ({exc}). Get a fresh "
                "code and run 'python app.py --set-code <code>' again."
            ) from exc
        self._on_login_success(username)

    @staticmethod
    def _looks_like_verification(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in ("two-factor", "two factor", "verification_code", "one-time"))

    def relogin(self) -> None:
        """Re-authenticate from the cached settings after a drop."""
        with self._lock:
            try:
                self._restore_session()
                self.client.relogin()
                self._persist_session()
                logger.info("Instagram relogin OK")
            except Exception as exc:
                logger.warning("Relogin failed (%s); attempting full login", exc)
                self.login()

    # ------------------------------------------------------------------ #
    # Session persistence
    # ------------------------------------------------------------------ #
    def _restore_session(self) -> None:
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
        """Return unread messages from other users (never our own).

        Group threads are skipped unless ``reply_in_groups`` is enabled —
        fewer requests and fewer angry group members.
        """
        self._pace()
        with self._lock:
            threads = self.client.direct_threads(amount=amount, selected_filter="unread")
            me = self._own_user_id()
            messages: List[IncomingMessage] = []
            for thread in threads:
                is_group = bool(getattr(thread, "is_group", False)) or (
                    getattr(thread, "thread_type", "") == "group"
                )
                if is_group and not self.reply_in_groups:
                    continue
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
        self._pace()
        with self._lock:
            try:
                self.client.direct_send_seen(thread_id)
            except Exception as exc:
                logger.debug("Could not mark thread %s seen: %s", thread_id, exc)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def send_text(self, thread_id: int, text: str) -> None:
        self._pace()
        with self._lock:
            self.client.direct_send(text, thread_ids=[thread_id])

    def send_photo(self, thread_id: int, path: Path, caption: str = "") -> None:
        self._pace()
        with self._lock:
            self.client.direct_send_photo(Path(path), thread_ids=[thread_id])
        if caption:
            time.sleep(0.8)  # keep message order natural
            self.send_text(thread_id, caption)

    def account_snapshot(self) -> dict:
        """Public info about the *authenticated* account only."""
        self._pace()
        with self._lock:
            info = self.client.account_info()
        return {
            "username": getattr(info, "username", self.env_username),
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
        raw = self.store.get(STATE_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [str(x) for x in data][-PROCESSED_MEMORY_LIMIT:]
        except (json.JSONDecodeError, TypeError):
            return []

    def _persist_processed(self) -> None:
        self.store.set(STATE_KEY, json.dumps(self._processed[-PROCESSED_PERSIST_LIMIT:]))
