"""Web dashboard: manage the bot in a browser instead of the CLI.

Provides an admin login (with first-run setup), GUI forms for Instagram
credentials and one-time codes (writing the same live store keys as the
``--set-credentials`` / ``--set-code`` SSH commands, so the running bot
picks values up without a restart), the bot's current status, editable
gentle-mode settings, and a live log viewer.

The same HTTP server also answers the platform health probe (``/health``),
so the deploy stays green.

Security: every page except /health, /setup and /login requires an admin
session; POST forms carry a CSRF token; login attempts are throttled per
client; secrets are hashed (admin password) or never echoed back.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.serving import make_server

from src.lib.bot_state import (
    LOG_BUFFER,
    SETTING_SPECS,
    load_settings,
    read_status,
    save_settings,
    validate_settings,
)

logger = logging.getLogger(__name__)

ADMIN_USERNAME_KEY = "web:admin_username"
ADMIN_PASSWORD_KEY = "web:admin_password"  # pbkdf2 hash
SECRET_KEY_KEY = "web:secret_key"

PUBLIC_PATHS = {"/health", "/healthz", "/ping", "/login", "/setup", "/favicon.ico"}


# ---------------------------------------------------------------------- #
# Login throttling (per client)
# ---------------------------------------------------------------------- #
class LoginThrottle:
    def __init__(self, max_failures: int = 10, lockout_seconds: float = 300.0):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict = {}
        self._lock = threading.Lock()

    def blocked_for(self, ip: str) -> float:
        """Seconds until this client may try again (0 = allowed)."""
        with self._lock:
            entry = self._failures.get(ip)
            if not entry:
                return 0.0
            remaining = entry.get("until", 0.0) - time.monotonic()
            return remaining if remaining > 0 else 0.0

    def record_failure(self, ip: str) -> None:
        with self._lock:
            entry = self._failures.setdefault(ip, {"fails": 0, "until": 0.0})
            entry["fails"] += 1
            if entry["fails"] >= self.max_failures:
                entry["until"] = time.monotonic() + self.lockout_seconds
                entry["fails"] = 0

    def reset(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------- #
# Admin credential helpers
# ---------------------------------------------------------------------- #
def _has_admin(store, config) -> bool:
    return bool(store.get(ADMIN_PASSWORD_KEY) or config.web_password)


def _verify_admin(store, config, username: str, password: str) -> bool:
    stored_hash = store.get(ADMIN_PASSWORD_KEY)
    if stored_hash:
        ok_user = hmac.compare_digest(
            (username or "").strip(), store.get(ADMIN_USERNAME_KEY) or config.web_username
        )
        return ok_user and check_password_hash(stored_hash, password or "")
    if config.web_password:
        ok_user = hmac.compare_digest((username or "").strip(), config.web_username)
        return ok_user and hmac.compare_digest(password or "", config.web_password)
    return False


def _secret_key(store, config) -> str:
    if config.web_secret_key:
        return config.web_secret_key
    existing = store.get(SECRET_KEY_KEY)
    if existing:
        return existing
    generated = secrets.token_urlsafe(32)
    store.set(SECRET_KEY_KEY, generated)
    return generated


# ---------------------------------------------------------------------- #
# App factory
# ---------------------------------------------------------------------- #
def create_app(config, store, instagram) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = _secret_key(store, config)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    throttle = LoginThrottle()

    # ---- guards ------------------------------------------------------ #
    @app.before_request
    def _guard():
        path = request.path
        if path in PUBLIC_PATHS or path.startswith("/static"):
            return None
        if not session.get("user"):
            return redirect(url_for("login", next=path))
        return None

    def _csrf_ok() -> bool:
        token = request.form.get("csrf", "")
        expected = session.get("csrf", "")
        return bool(expected) and hmac.compare_digest(token, expected)

    def _ensure_csrf() -> None:
        """One CSRF token per session.

        Regenerating the token on every GET invalidates any form the user is
        still filling in (a background fetch of the same page, or the favicon
        bouncing through /login -> /setup, used to rotate it and the submit
        then failed with 403). Keep it stable until login/logout.
        """
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(16)

    def _current_settings() -> dict:
        overrides = load_settings(store)
        merged = {}
        for key in SETTING_SPECS:
            merged[key] = overrides.get(key, getattr(config, key))
        return merged

    # ---- public: health --------------------------------------------- #
    @app.get("/health")
    @app.get("/healthz")
    @app.get("/ping")
    def health():
        return {"status": "ok"}

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)  # keep browsers from bouncing through /login

    # ---- setup (first run) & login ---------------------------------- #
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if _has_admin(store, config):
            return redirect(url_for("login"))
        error = ""
        if request.method == "POST":
            if not _csrf_ok():
                error = "Your form expired — please submit again."
            else:
                username = (request.form.get("username") or "").strip()
                password = request.form.get("password") or ""
                confirm = request.form.get("confirm") or ""
                if not username or not password:
                    error = "Username and password are required."
                elif len(password) < 8:
                    error = "Password must be at least 8 characters."
                elif password != confirm:
                    error = "Passwords do not match."
                else:
                    store.set(ADMIN_USERNAME_KEY, username)
                    store.set(ADMIN_PASSWORD_KEY, generate_password_hash(password))
                    logger.info("Web dashboard admin account created for %s", username)
                    return redirect(url_for("login"))
        _ensure_csrf()
        return render_template("setup.html", error=error)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not _has_admin(store, config):
            return redirect(url_for("setup"))
        error = ""
        if request.method == "POST":
            if not _csrf_ok():
                error = "Your form expired — please submit again."
            else:
                ip = _client_ip()
                wait = throttle.blocked_for(ip)
                if wait > 0:
                    error = f"Too many attempts — try again in {int(wait) + 1} seconds."
                elif _verify_admin(store, config, request.form.get("username", ""), request.form.get("password", "")):
                    throttle.reset(ip)
                    session.clear()
                    session["user"] = (request.form.get("username") or "").strip()
                    session["csrf"] = secrets.token_urlsafe(16)
                    return redirect(request.form.get("next") or url_for("status"))
                else:
                    throttle.record_failure(ip)
                    error = "Wrong username or password."
        _ensure_csrf()
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        if _csrf_ok():
            session.clear()
        return redirect(url_for("login"))

    # ---- status ------------------------------------------------------ #
    @app.get("/")
    def status():
        bot = read_status(store)
        return render_template(
            "status.html",
            bot=bot,
            has_credentials=instagram.has_credentials(),
            has_code=bool(instagram.has_live_code()),
            settings=_current_settings(),
        )

    # ---- credentials & one-time codes -------------------------------- #
    @app.route("/credentials", methods=["GET", "POST"])
    def credentials():
        message, error = "", ""
        if request.method == "POST":
            if not _csrf_ok():
                return ("Invalid CSRF token", 403)
            action = request.form.get("action", "")
            try:
                if action == "save_credentials":
                    username = (request.form.get("ig_username") or "").strip()
                    password = request.form.get("ig_password") or ""
                    if not password:
                        if instagram.has_credentials():
                            # Keep the current password, replace the username only.
                            _, password = instagram.resolve_credentials()
                        else:
                            error = "Password is required the first time."
                    if not error:
                        instagram.provide_credentials(username, password)
                        message = (
                            f"Credentials saved for @{username}. The bot picks them up "
                            "automatically — no restart needed."
                        )
                elif action == "save_code":
                    instagram.provide_verification_code(request.form.get("code") or "")
                    message = "Verification code saved. The bot will use it automatically (one-time)."
                elif action == "import_session":
                    instagram.provide_session(request.form.get("session_json") or "")
                    message = (
                        "Session imported. The bot will sign in with it — no password "
                        "login — and picks it up automatically (no restart needed)."
                    )
                elif action == "clear_session":
                    instagram.clear_session()
                    message = "Stored Instagram session cleared."
                elif action == "clear_code":
                    instagram.clear_verification_code()
                    message = "Stored verification code cleared."
                elif action == "clear_credentials":
                    instagram.clear_credentials()
                    message = "Stored credentials cleared (environment values still apply)."
            except ValueError as exc:
                error = str(exc)
        current_user = instagram.resolve_credentials()[0] if instagram.has_credentials() else ""
        return render_template(
            "credentials.html",
            message=message,
            error=error,
            current_user=current_user,
            has_credentials=instagram.has_credentials(),
            has_code=bool(instagram.has_live_code()),
            has_session=instagram.has_session(),
        )

    # ---- settings ---------------------------------------------------- #
    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        message, error = "", ""
        if request.method == "POST":
            if not _csrf_ok():
                return ("Invalid CSRF token", 403)
            if request.form.get("action") == "reset":
                save_settings(store, {})
                message = "Settings reset to defaults."
            else:
                clean, errors = validate_settings(request.form)
                if errors:
                    error = "; ".join(errors)
                else:
                    merged = dict(load_settings(store))
                    merged.update(clean)
                    save_settings(store, merged)
                    message = "Settings saved — the running bot applies them on its next cycle."
        return render_template(
            "settings.html",
            message=message,
            error=error,
            values=_current_settings(),
            specs=SETTING_SPECS,
        )

    # ---- logs -------------------------------------------------------- #
    @app.get("/logs")
    def logs():
        return render_template("logs.html", lines=LOG_BUFFER.tail(300))

    return app


# ---------------------------------------------------------------------- #
# Threaded HTTP server (shares the managed port with the health probe)
# ---------------------------------------------------------------------- #
class WebUIServer:
    """Serve the dashboard (and /health) on a background thread."""

    def __init__(self, flask_app: Flask, port: int, host: str = "0.0.0.0"):
        self._host = host
        self._server = make_server(host, int(port), flask_app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="webui-server",
            daemon=True,
        )
        self._started = False

    @property
    def port(self) -> int:
        return self._server.server_port

    def start(self) -> None:
        if self._started:
            return
        self._thread.start()
        self._started = True
        logger.info(
            "Web dashboard up on http://%s:%s/ (login with the admin password; "
            "platform health probes pass on /health)",
            self._host,
            self.port,
        )

    def stop(self) -> None:
        if self._started:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._started = False
        self._server.server_close()
