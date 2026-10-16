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

MAX_WORKERS = 4  # concurrent command handlers (AI calls serialise on the queue)


# ---------------------------------------------------------------------- #
# Application wiring
# ---------------------------------------------------------------------- #
class Application:
    """Wires config -> services -> command registry and runs the bot loop."""

    def __init__(self, config: Config):
        self.config = config
        self.services = None  # populated by build()
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
            ),
            sherlock=SherlockService(
                enabled=cfg.sherlock_enabled,
                sites=cfg.sherlock_sites,
                timeout=cfg.sherlock_timeout,
            ),
            store=store,
        )
        load_all()  # populate the command registry

    # ------------------------------------------------------------------ #
    # Message handling
    # ------------------------------------------------------------------ #
    def handle_message(self, message) -> None:
        """Dispatch one inbox message (runs on a worker thread)."""
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
            return

        try:
            if result.image:
                self._send_image_result(message.thread_id, result)
            for chunk in result.message_chunks():
                self.services.instagram.send_text(message.thread_id, chunk)
                time.sleep(0.4)  # keep messages ordered and rate-limit friendly
        except Exception as exc:
            logger.error("Failed to reply in thread %s: %s", message.thread_id, exc)

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
        """Login, then poll the inbox until stopped."""
        from src.services.instagram import InstagramError

        self._install_signal_handlers()
        self.services.ai_queue.start()

        try:
            self.services.instagram.login()
        except InstagramError as exc:
            logger.error("Startup login failed: %s", exc.message)
            raise SystemExit(1) from exc

        from src.commands.base import COMMANDS

        logger.info(
            "Bot is running — polling every %ss (commands: %d active)",
            self.config.ig_poll_interval,
            len(COMMANDS),
        )

        failures = 0
        while not self._stop.is_set():
            try:
                self._poll_once()
                failures = 0
            except Exception as exc:
                failures += 1
                logger.warning("Poll cycle failed (%s); backing off", exc)
                self._maybe_relogin(exc)
                # Bounded exponential backoff, capped at 5 minutes.
                backoff = min(300, 10 * (2 ** min(failures, 5)))
                self._stop.wait(backoff)
            self._stop.wait(self.config.ig_poll_interval)

        self.shutdown()

    def _poll_once(self) -> None:
        messages = self.services.instagram.fetch_unread(amount=self.config.ig_poll_amount)
        for message in messages:
            if self.services.instagram.is_processed(message.message_id):
                continue
            self.services.instagram.mark_processed(message.message_id)
            future = self._executor.submit(self.handle_message, message)
            self._futures.append(future)
        # Reap finished futures so the list stays bounded.
        self._futures = [f for f in self._futures if not f.done()]
        if messages:
            # Mark the threads read so the next poll only sees new messages.
            for thread_id in {m.thread_id for m in messages}:
                self.services.instagram.mark_thread_seen(thread_id)

    def _maybe_relogin(self, exc: Exception) -> None:
        text = str(exc).lower()
        if any(marker in text for marker in ("login", "403", "401", "challenge", "checkpoint")):
            logger.info("Attempting Instagram relogin after: %s", exc)
            try:
                self.services.instagram.relogin()
            except Exception as relogin_exc:
                logger.error("Relogin failed: %s", relogin_exc)

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


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
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
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    _setup_logging(config.log_level)

    if args.check:
        return _run_check(config)

    errors = config.validate_for_run()
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.error("Fix the environment variables above and restart (see .env.example).")
        return 2

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


def _run_check(config: Config) -> int:
    """`python app.py --check`: validate everything short of logging in."""
    ok = True
    print("== Instagram bot configuration check ==")
    errors = config.validate_for_run()
    if errors:
        ok = False
        for err in errors:
            print(f"[error] {err}")
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
