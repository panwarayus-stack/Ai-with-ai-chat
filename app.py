"""Instagram bot for Voroa — application entry point.

Start command (Voroa Background Worker)::

    python app.py

Useful flags::

    python app.py --check     # validate config + imports, then exit
    python app.py --help

The bot logs into Instagram (``instagrapi``), polls the direct-message inbox
for slash commands, dispatches them to the registry in ``src/commands`` and
replies in-thread. One shared, bounded FIFO queue serialises OpenRouter
requests for ``/AI`` and ``/Gf``. A failed command is answered with a friendly
error and never crashes the loop.
"""

from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from config import Config, ConfigError

logger = logging.getLogger("app")

# Error markers that mean "back off for a LONG time, not a minute": Instagram
# rate limits (HTTP 429 / payload code 1675004) and challenges/checkpoints.
# Retrying these quickly only keeps the block active.
_LONG_COOLDOWN_MARKERS = (
    "rate limit",
    "too many requests",
    "1675004",
    "429 client",
    "status 429",
    "challenge",
    "checkpoint",
)


def _error_text(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc)


def _needs_long_cooldown(exc: Exception) -> bool:
    text = _error_text(exc).lower()
    return any(marker in text for marker in _LONG_COOLDOWN_MARKERS)


def _login_failure_hint(exc: Exception) -> str:
    """Actionable advice for the failure type — don't blame credentials for a throttle."""
    text = _error_text(exc).lower()
    if "rate limit" in text or "too many requests" in text or "1675004" in text:
        return (
            "Instagram is throttling logins — this clears with time; do NOT redeploy "
            "or keep retrying, each login attempt keeps the throttle active"
        )
    if "challenge" in text or "checkpoint" in text:
        return (
            "approve the pending 'was this you?' prompt in the Instagram app on your "
            "phone; the bot retries automatically"
        )
    return "fix credentials live with: python app.py --set-credentials <username>"

MAX_WORKERS = 4  # concurrent command handlers (AI calls serialise on the queue)


# ---------------------------------------------------------------------- #
# Application wiring
# ---------------------------------------------------------------------- #
class Application:
    """Wires config -> services -> command registry and runs the bot loop."""

    def __init__(self, config: Config):
        self.config = config
        self.services = None  # populated by build()
        self._reply_budget = None  # populated by build()
        self._settings = None  # populated by build()
        self._relogin_blocked_until = 0.0
        self._health_server = None  # populated by _start_health_server()
        self._web_server = None  # populated by _start_health_server() (web mode)
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="cmd")
        self._futures: List = []

    # ------------------------------------------------------------------ #
    def build(self) -> None:
        """Construct every service (no network happens here)."""
        from src.commands import load_all
        from src.commands.base import Services
        from src.lib.ai_queue import AIQueue
        from src.lib.http import HttpClient
        from src.lib.utils import HourlyBudget
        from src.lib.bot_state import RuntimeSettings
        from src.services.anime import AnimeService
        from src.services.demographics import DemographicsService
        from src.services.images import ImageService
        from src.services.instagram import InstagramService
        from src.services.openrouter import ConversationStore, OpenRouterClient
        from src.services.session_store import build_session_store
        from src.services.sherlock import SherlockService
        from src.services.weather import WeatherService
        from src.services.wikipedia import WikipediaService

        cfg = self.config
        http = HttpClient(
            timeout=cfg.http_timeout,
            retries=cfg.http_retries,
            user_agent=cfg.wikipedia_user_agent,
        )

        # File root for sessions/state (also used as the fallback store).
        state_root = cfg.session_file_path().parent / "state"
        store = build_session_store(cfg.redis_url, state_root)

        openrouter = OpenRouterClient(
            api_key=cfg.openrouter_api_key,
            model=cfg.openrouter_model,
            site_url=cfg.openrouter_site_url,
            app_name=cfg.openrouter_app_name,
            timeout=cfg.ai_request_timeout,
            http=http,
        )
        ai_queue = AIQueue(max_size=cfg.ai_max_queue_size)
        conversations = ConversationStore(memory_turns=cfg.ai_memory_turns)

        self.services = Services(
            openrouter=openrouter,
            ai_queue=ai_queue,
            conversations=conversations,
            demographics=DemographicsService(
                genderize_key=cfg.genderize_api_key,
                nationalize_key=cfg.nationalize_api_key,
                http=http,
            ),
            weather=WeatherService(units=cfg.weather_units, http=http),
            anime=AnimeService(
                anilist_enabled=cfg.anilist_enabled,
                jikan_enabled=cfg.jikan_enabled,
                http=http,
            ),
            images=ImageService(waifu_im_enabled=cfg.waifu_im_enabled, http=http),
            wikipedia=WikipediaService(
                language=cfg.wikipedia_language,
                user_agent=cfg.wikipedia_user_agent,
                http=http,
            ),
            instagram=InstagramService(
                username=cfg.ig_username,
                password=cfg.ig_password,
                session_file=cfg.session_file_path(),
                store=store,
                verification_code=cfg.ig_verification_code,
                verification_file=cfg.verification_file_path(),
                verification_wait=cfg.ig_verification_wait,
                min_request_gap=cfg.ig_min_request_gap,
                reply_in_groups=cfg.ig_reply_in_groups,
            ),
            sherlock=SherlockService(
                enabled=cfg.sherlock_enabled,
                sites=cfg.sherlock_sites,
                timeout=cfg.sherlock_timeout,
            ),
            store=store,
        )
        load_all()  # populate the command registry
        self._reply_budget = HourlyBudget(cfg.ig_max_replies_per_hour)
        self._settings = RuntimeSettings(store)

    # ------------------------------------------------------------------ #
    # Message handling
    # ------------------------------------------------------------------ #
    def handle_message(self, message) -> None:
        """Dispatch one inbox message (runs on a worker thread).

        Anti-suspension behaviour: nothing is sent until dispatch produced an
        actual reply, replies are rate-limited per hour, and each reply waits
        a random human-like pause before it is typed out.
        """
        from src.commands.base import CommandContext, dispatch

        ctx = CommandContext(
            services=self.services,
            user_id=message.user_id,
            username=message.username,
            config=self.config,
        )
        try:
            result = dispatch(ctx, message.text)
        except Exception:  # dispatch already guards; this is belt and braces
            logger.exception("Unhandled dispatch failure")
            return
        if result is None:
            return  # not a command — stay silent, cost nothing

        # Keep the account's activity footprint tiny and human-like.
        if self._reply_budget is not None and not self._reply_budget.allow():
            logger.warning(
                "Reply budget reached (IG_MAX_REPLIES_PER_HOUR=%s) — staying quiet to "
                "protect the account. The budget resets automatically.",
                self.config.ig_max_replies_per_hour,
            )
            return
        self._human_delay()

        try:
            if result.image:
                self._send_image_result(message.thread_id, result)
            chunks = result.message_chunks()
            for index, chunk in enumerate(chunks):
                self.services.instagram.send_text(message.thread_id, chunk)
                if index < len(chunks) - 1:
                    time.sleep(1.0)  # pause between chunks only
        except Exception as exc:
            logger.error("Failed to reply in thread %s: %s", message.thread_id, exc)

    def _human_delay(self) -> None:
        """Random pause before replying — humans don't answer instantly."""
        low = float(self._rt("ig_reply_delay_min", self.config.ig_reply_delay_min))
        high = max(low, float(self._rt("ig_reply_delay_max", self.config.ig_reply_delay_max)))
        if high <= 0:
            return
        time.sleep(random.uniform(low, high))

    def _send_image_result(self, thread_id: int, result) -> None:
        suffix = _image_extension(result.image)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(result.image)
            path = Path(tmp.name)
        try:
            self.services.instagram.send_photo(thread_id, path)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        """Login (patiently), then poll the inbox until stopped."""
        self._install_signal_handlers()
        self._start_health_server()  # platform probes pass even during login waits
        self.services.ai_queue.start()

        if not self._ensure_login():
            logger.info("Stopped before login completed — shutting down")
            self.shutdown()
            return

        from src.commands.base import COMMANDS

        logger.info(
            "Bot is running — polling every %ss (commands: %d active)",
            self.config.ig_poll_interval,
            len(COMMANDS),
        )

        failures = 0
        last_activity = time.monotonic()
        while not self._stop.is_set():
            try:
                active = self._poll_once()
                if active:
                    last_activity = time.monotonic()
                failures = 0
            except Exception as exc:
                failures += 1
                logger.warning("Poll cycle failed (%s); backing off", exc)
                self._maybe_relogin(exc)
                # Bounded exponential backoff, capped at 5 minutes.
                backoff = min(300, 10 * (2 ** min(failures, 5)))
                self._stop.wait(backoff)
            self._stop.wait(self._poll_delay(last_activity))

        self.shutdown()

    # ------------------------------------------------------------------ #
    # Health endpoint & web dashboard — platform readiness probe
    # ------------------------------------------------------------------ #
    def _start_health_server(self) -> None:
        """Serve HTTP on ``health_port``: the deploy probe and (optionally) the dashboard.

        The deploy platform probes the service over HTTP before calling a
        deploy live. Without a listener that probe fails and the platform
        aborts the deploy — even when the bot is behaving correctly (e.g.
        waiting out an Instagram rate limit). When ``WEB_ENABLED`` the same
        port serves the management dashboard (which includes ``/health``);
        otherwise a bare always-200 health server answers. Binding fails
        soft: a worker on a platform without probes just logs and moves on.
        """
        port = self.config.health_port
        if port <= 0:
            logger.info("Web dashboard & health endpoint disabled (HEALTH_PORT=0)")
            return
        try:
            if self.config.web_enabled:
                from src.webui.app import WebUIServer, create_app

                flask_app = create_app(self.config, self.services.store, self.services.instagram)
                self._web_server = WebUIServer(flask_app, port)
                self._web_server.start()
            else:
                from src.lib.health import HealthServer

                self._health_server = HealthServer(port)
                self._health_server.start()
        except OSError as exc:
            self._health_server = None
            self._web_server = None
            logger.warning("Could not bind health/dashboard port %s: %s", port, exc)

    # ------------------------------------------------------------------ #
    # Bot status (shown on the web dashboard)
    # ------------------------------------------------------------------ #
    def _publish_status(self, state: str, detail: str = "") -> None:
        from src.lib.bot_state import publish_status

        if self.services is not None and self.services.store is not None:
            publish_status(self.services.store, state, detail)

    # ------------------------------------------------------------------ #
    # Login resilience — never crash-loop
    # ------------------------------------------------------------------ #
    def _ensure_login(self) -> bool:
        """Log in — and if it fails, stay up and retry with a slow backoff.

        A failed login must NEVER crash the worker. Exiting makes the
        platform restart the process, and every restart hammers Instagram's
        login endpoint again — a crash-loop is exactly what triggers rate
        limits ("Rate limit exceeded", code 1675004) and "verify it's you"
        challenges. Instead the process stays alive (and SSH-reachable for
        ``--set-credentials``) while retrying at a human pace.

        Persistent rate limits/challenges back off further each consecutive
        failure, because retrying a throttle too often keeps it active.

        Returns True once logged in, False if stopping interrupted the wait.
        """
        attempt = 0
        slowdown_streak = 0
        while not self._stop.is_set():
            self._publish_status("logging_in", "contacting Instagram")
            try:
                self.services.instagram.login()
                self._publish_status("online", "logged in — waiting for messages")
                return True
            except Exception as exc:  # any failure must stay non-fatal
                attempt += 1
                if _needs_long_cooldown(exc):
                    slowdown_streak += 1
                else:
                    slowdown_streak = 0
                delay = self._login_backoff(exc, attempt, slowdown_streak)
                self._publish_status(
                    "backing_off",
                    f"{_error_text(exc)[:120]} — retrying in {delay / 60:.0f} min",
                )
                logger.error(
                    "Startup login failed: %s — staying up and retrying in %.1f min (%s)",
                    _error_text(exc),
                    delay / 60,
                    _login_failure_hint(exc),
                )
                self._stop.wait(delay)
        return False

    def _login_backoff(
        self, exc: Exception, attempt: int, slowdown_streak: int = 1
    ) -> float:
        """Seconds to wait before the next login attempt.

        Rate limits and challenges get the long cooldown — retrying sooner
        only keeps the block active — and each consecutive one waits longer
        (doubling up to ``ig_rate_limit_cooldown_max``). Everything else
        backs off exponentially from ``ig_login_retry_min`` up to
        ``ig_login_retry_max``.
        """
        cfg = self.config
        if _needs_long_cooldown(exc):
            streak = max(1, slowdown_streak)
            delay = cfg.ig_rate_limit_cooldown * (2 ** min(streak - 1, 6))
            return float(min(cfg.ig_rate_limit_cooldown_max, delay))
        delay = cfg.ig_login_retry_min * (2 ** min(attempt - 1, 6))
        return float(min(cfg.ig_login_retry_max, delay))

    def _rt(self, key: str, default):
        """Dashboard-editable setting, falling back to the configured default."""
        return self._settings.get(key, default) if self._settings is not None else default

    def _apply_runtime_settings(self) -> None:
        """Push dashboard settings into the live services (called per poll)."""
        svc = self.services.instagram
        svc.min_request_gap = float(self._rt("ig_min_request_gap", self.config.ig_min_request_gap))
        svc.reply_in_groups = bool(self._rt("ig_reply_in_groups", self.config.ig_reply_in_groups))
        if self._reply_budget is not None:
            self._reply_budget.set_limit(
                int(self._rt("ig_max_replies_per_hour", self.config.ig_max_replies_per_hour))
            )

    def _poll_delay(self, last_activity: float) -> float:
        """Next poll interval: jittered, and slower while the inbox is idle.

        The bot stays quiet when nobody is talking (it "comes online" only
        around actual conversations), which keeps the request pattern far
        from that of a busy automation.
        """
        cfg = self.config
        idle_after = float(self._rt("ig_idle_after_minutes", cfg.ig_idle_after_minutes))
        idle = time.monotonic() - last_activity >= idle_after * 60
        if idle:
            base = float(self._rt("ig_idle_poll_interval", cfg.ig_idle_poll_interval))
        else:
            base = float(self._rt("ig_poll_interval", cfg.ig_poll_interval))
        return base * random.uniform(0.85, 1.15)

    def _poll_once(self) -> bool:
        """Fetch and dispatch new messages. True when something new arrived."""
        if self._settings is not None:
            self._settings.refresh()
        self._apply_runtime_settings()
        messages = self.services.instagram.fetch_unread(amount=self.config.ig_poll_amount)
        new_messages = 0
        for message in messages:
            if self.services.instagram.is_processed(message.message_id):
                continue
            self.services.instagram.mark_processed(message.message_id)
            new_messages += 1
            future = self._executor.submit(self.handle_message, message)
            self._futures.append(future)
        # Reap finished futures so the list stays bounded.
        self._futures = [f for f in self._futures if not f.done()]
        if messages and self._rt("ig_mark_seen", self.config.ig_mark_seen):
            # Optional: mark threads read (each one is an extra request).
            for thread_id in {m.thread_id for m in messages}:
                self.services.instagram.mark_thread_seen(thread_id)
        return new_messages > 0

    def _maybe_relogin(self, exc: Exception) -> None:
        if time.monotonic() < self._relogin_blocked_until:
            return  # a recent relogin failed — give Instagram a long breather
        text = str(exc).lower()
        if any(marker in text for marker in ("login", "403", "401", "challenge", "checkpoint")):
            logger.info("Attempting Instagram relogin after: %s", exc)
            try:
                self.services.instagram.relogin()
                self._relogin_blocked_until = 0.0
            except Exception as relogin_exc:
                delay = self._login_backoff(relogin_exc, 1)
                self._relogin_blocked_until = time.monotonic() + delay
                logger.error(
                    "Relogin failed: %s — not retrying for %.1f min",
                    _error_text(relogin_exc),
                    delay / 60,
                )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info("Received signal %s — shutting down", signum)
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except ValueError:  # pragma: no cover - non-main thread
                pass

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        for server in (self._health_server, self._web_server):
            if server is not None:
                try:
                    server.stop()
                except Exception:  # pragma: no cover - defensive
                    pass
        self._publish_status("stopped", "shutting down")
        try:
            self.services.ai_queue.close(wait=False)
        except Exception:  # pragma: no cover
            pass
        logger.info("Shutdown complete")


# ---------------------------------------------------------------------- #
# Entry point
# ---------------------------------------------------------------------- #
def _image_extension(data: bytes) -> str:
    """Pick a file extension from the image magic bytes (MIME correctness)."""
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


_LOG_HANDLER: Optional[logging.Handler] = None


def _setup_logging(level: str) -> None:
    """Attach exactly one stdout handler to the root logger (idempotent).

    Duplicate handlers emit every line twice, which is confusing in the
    Voroa Logs tab. Foreign handlers (test runners etc.) are left alone.
    A ring buffer handler feeds the web dashboard's log viewer.
    """
    global _LOG_HANDLER
    from src.lib.bot_state import LOG_BUFFER

    root = logging.getLogger()
    if _LOG_HANDLER is None or _LOG_HANDLER not in root.handlers:
        _LOG_HANDLER = logging.StreamHandler(sys.stdout)
        _LOG_HANDLER.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(_LOG_HANDLER)
    if LOG_BUFFER not in root.handlers:
        root.addHandler(LOG_BUFFER)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Never let third-party libs log request bodies (they may hold tokens).
    for noisy in ("urllib3", "instagrapi", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Instagram command bot (Voroa worker)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and imports, then exit (no network, no login).",
    )
    parser.add_argument(
        "--set-credentials",
        nargs="+",
        metavar="USERNAME",
        help=(
            "Store Instagram credentials at runtime (works via SSH while the "
            "bot waits). Usage: --set-credentials <username>  (password is "
            "prompted securely)  or  --set-credentials <username> <password>."
        ),
    )
    parser.add_argument(
        "--set-code",
        metavar="CODE",
        help="Provide a live one-time verification code (2FA/OTP) without restarting.",
    )
    parser.add_argument(
        "--clear-code",
        action="store_true",
        help="Remove a stored verification code.",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help=(
            "Log in once, save the session to IG_SESSION_FILE, then exit. "
            "Run this on YOUR machine to generate a session the server can "
            "import (dashboard: Import session) — the server then never "
            "performs a password login."
        ),
    )
    parser.add_argument(
        "--import-session",
        metavar="PATH",
        help="Import a session file produced by --login-only into the store.",
    )
    parser.add_argument(
        "--clear-session",
        action="store_true",
        help="Remove the stored Instagram session.",
    )
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    _setup_logging(config.log_level)

    if (
        args.set_credentials
        or args.set_code
        or args.clear_code
        or args.login_only
        or args.import_session
        or args.clear_session
    ):
        return _run_live_config(config, args)

    if args.check:
        return _run_check(config)

    errors = config.validate_for_run()
    if errors:
        # Missing configuration must NOT crash-loop the worker: the login
        # flow waits for live credentials (dashboard env or SSH --set-credentials)
        # so the service stays up and reachable. Only --check treats this as
        # a hard failure.
        for err in errors:
            logger.warning("Config note: %s", err)
    for note in config.soft_warnings():
        logger.warning("Config note: %s", note)
    logger.info("Starting with configuration: %s", config.summary())

    app = Application(config)
    try:
        app.build()
    except Exception:
        logger.exception("Failed to build the application (missing dependency?)")
        return 3

    app.run()
    return 0


def _run_live_config(config: Config, args) -> int:
    """Live configuration commands (run via SSH while the bot is waiting).

    * ``--set-credentials <username> [password]`` — store login credentials
      (password prompted securely when omitted).
    * ``--set-code <code>`` — deliver a one-time 2FA/verification code.
    * ``--clear-code`` — remove a stored code.
    * ``--login-only`` — log in once and save the session (run locally to
      generate one the server can import).
    * ``--import-session <path>`` — import a session file into the store.
    * ``--clear-session`` — remove the stored session.

    Values go to the configured session store (files under ``auth/``, or
    Redis when ``REDIS_URL`` is set) and the running bot picks them up on its
    next poll — no restart needed. Never prints secrets back.
    """
    import getpass

    from src.services.instagram import InstagramService
    from src.services.session_store import build_session_store

    store = build_session_store(config.redis_url, config.session_file_path().parent / "state")
    service = InstagramService(
        username=config.ig_username,
        password=config.ig_password,
        session_file=config.session_file_path(),
        store=store,
        verification_code=config.ig_verification_code,
        verification_file=config.verification_file_path(),
        verification_wait=config.ig_verification_wait,
    )

    try:
        if args.set_credentials:
            parts = args.set_credentials
            if len(parts) == 1:
                username = parts[0]
                password = getpass.getpass("Instagram password (input hidden): ")
            elif len(parts) == 2:
                username, password = parts[0], parts[1]
                print(
                    "Note: a password passed on the command line may be saved in "
                    "shell history. Prefer: python app.py --set-credentials <username>"
                )
            else:
                print("Usage: python app.py --set-credentials <username> [password]", file=sys.stderr)
                return 2
            service.provide_credentials(username, password)
            print(
                f"Credentials stored for @{username.strip()}. The waiting bot will "
                "continue its login automatically (no restart needed)."
            )
        if args.set_code:
            service.provide_verification_code(args.set_code)
            print(
                "Verification code stored. The waiting bot will use it automatically "
                "(codes are one-time and are cleared after login)."
            )
        if args.clear_code:
            service.clear_verification_code()
            print("Stored verification code cleared.")
        if args.login_only:
            if not service.has_credentials() and not service.has_session():
                print(
                    "No Instagram credentials or session found. Set IG_USERNAME/"
                    "IG_PASSWORD (or run --set-credentials / --import-session) first.",
                    file=sys.stderr,
                )
                return 2
            try:
                service.login()
            except Exception as exc:
                print(f"Login failed: {exc}", file=sys.stderr)
                return 1
            print(f"Session saved to: {config.session_file_path()}")
            print(
                "Import it on the server: dashboard -> Login & codes -> Import "
                "session (paste the file contents), or SSH: python app.py "
                "--import-session <path>. The server will then sign in without "
                "a password login."
            )
        if args.import_session:
            path = Path(args.import_session)
            if not path.is_file():
                print(f"Error: no such file: {path}", file=sys.stderr)
                return 2
            service.provide_session(path.read_text(encoding="utf-8"))
            print(
                "Session imported. The running bot will use it on its next "
                "sign-in — no password login needed."
            )
        if args.clear_session:
            service.clear_session()
            print("Stored Instagram session cleared.")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - store failures
        print(f"Could not store the value: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_check(config: Config) -> int:
    """`python app.py --check`: validate everything short of logging in."""
    ok = True
    print("== Instagram bot configuration check ==")
    errors = config.validate_for_run()
    if errors:
        ok = False
        for err in errors:
            print(f"[error] {err}")
        print(
            "[hint]  Missing credentials no longer stop the running bot: it waits "
            "and can be configured live via SSH — python app.py --set-credentials "
            "<username>  (password prompted) — or via the Voroa Environment tab."
        )
    for note in config.soft_warnings():
        print(f"[note]  {note}")

    try:
        app = Application(config)
        app.build()
        from src.commands.base import COMMANDS
        from src.commands.paused import paused_names

        print(f"[ok]    imports resolved; {len(COMMANDS)} active commands registered")
        for name in sorted(COMMANDS, key=str.lower):
            print(f"          /{COMMANDS[name].name}")
        for name, reason in paused_names().items():
            print(f"[paused] /{name} — {reason}")
        print(f"[ok]    session backend: {config.summary()['session_backend']}")
    except Exception as exc:
        ok = False
        print(f"[error] application build failed: {exc}")

    print("== check " + ("passed ==" if ok else "FAILED =="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
