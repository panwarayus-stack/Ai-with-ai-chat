"""Environment configuration for the Instagram bot.

All runtime settings come from environment variables (optionally loaded from a
local ``.env`` file in development). Secrets are never hard-coded and are never
written to logs: :meth:`Config.summary` returns a redacted snapshot only.

The class is intentionally boring: parse, validate, expose. Anything clever
belongs in the services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard dependency
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent

# Defaults that also act as documentation for the .env.example file.
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_IG_SESSION_FILE = "auth/instagram_session.json"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(Exception):
    """Raised when configuration is present but unusable."""


def _get_str(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        return default
    return value.strip()


def _get_bool(env: Mapping[str, str], key: str, default: bool, warnings: list) -> bool:
    raw = _get_str(env, key, "")
    if raw == "":
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    warnings.append(f"{key}={raw!r} is not a boolean; using default {default}")
    return default


def _get_int(
    env: Mapping[str, str],
    key: str,
    default: int,
    warnings: list,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    raw = _get_str(env, key, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        warnings.append(f"{key}={raw!r} is not an integer; using default {default}")
        return default
    if minimum is not None and value < minimum:
        warnings.append(f"{key}={value} is below the minimum {minimum}; using {minimum}")
        return minimum
    if maximum is not None and value > maximum:
        warnings.append(f"{key}={value} is above the maximum {maximum}; using {maximum}")
        return maximum
    return value


def _get_float(
    env: Mapping[str, str],
    key: str,
    default: float,
    warnings: list,
    minimum: Optional[float] = None,
) -> float:
    raw = _get_str(env, key, "")
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        warnings.append(f"{key}={raw!r} is not a number; using default {default}")
        return default
    if minimum is not None and value < minimum:
        warnings.append(f"{key}={value} is below the minimum {minimum}; using {minimum}")
        return minimum
    return value


def _get_csv(env: Mapping[str, str], key: str) -> Tuple[str, ...]:
    raw = _get_str(env, key, "")
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _resolve_health_port(env: Mapping[str, str], warnings: list) -> int:
    """Health probe port: ``HEALTH_PORT`` > ``PORT`` (platform-managed) > 8080.

    ``0`` disables the endpoint. Invalid values fall back to 8080 with a
    warning rather than breaking the bot.
    """
    raw = _get_str(env, "HEALTH_PORT") or _get_str(env, "PORT")
    if not raw:
        return 8080
    try:
        port = int(raw)
    except ValueError:
        warnings.append(f"HEALTH_PORT/PORT={raw!r} is not a number; using 8080")
        return 8080
    if port == 0:
        return 0
    if not (1 <= port <= 65535):
        warnings.append(f"HEALTH_PORT/PORT={port} is out of range 1-65535; using 8080")
        return 8080
    return port


@dataclass(frozen=True)
class Config:
    """Immutable, fully-parsed runtime configuration."""

    # Application
    app_env: str = "production"
    log_level: str = "INFO"
    # Platform health probe port. The bot serves HTTP 200 here so the deploy
    # health check passes even while it waits out an Instagram rate limit.
    # Resolution: HEALTH_PORT > PORT (platform-managed) > 8080.
    # HEALTH_PORT=0 disables the endpoint entirely.
    health_port: int = 8080

    # Instagram authentication
    ig_username: str = ""
    ig_password: str = ""
    ig_session_file: str = DEFAULT_IG_SESSION_FILE
    ig_verification_code: str = ""
    ig_verification_file: str = "auth/verification_code.txt"
    ig_verification_wait: int = 300
    ig_poll_interval: int = 60
    ig_poll_amount: int = 20
    # Anti-suspension ("gentle mode") — see README "Avoiding Instagram flags"
    ig_idle_after_minutes: int = 10
    ig_idle_poll_interval: int = 180
    ig_reply_delay_min: int = 3
    ig_reply_delay_max: int = 10
    ig_min_request_gap: float = 5.0
    ig_max_replies_per_hour: int = 20
    ig_mark_seen: bool = False
    ig_reply_in_groups: bool = False
    # Login resilience — a failed login must never crash-loop the worker.
    # Exiting makes the platform restart us and every restart hammers
    # Instagram's login endpoint (the exact pattern that triggers rate
    # limits and "verify it's you" challenges).
    ig_login_retry_min: int = 60
    ig_login_retry_max: int = 1800
    ig_rate_limit_cooldown: int = 1200

    # Persistence (Voroa has no persistent disks)
    redis_url: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_site_url: str = ""
    openrouter_app_name: str = "InstagramBot"
    ai_request_timeout: int = 60
    ai_max_queue_size: int = 50
    ai_memory_turns: int = 4
    ai_max_prompt_chars: int = 2000

    # Gender / nationality
    genderize_api_key: str = ""
    nationalize_api_key: str = ""

    # Weather
    weather_units: str = "metric"

    # Anime
    anilist_enabled: bool = True
    jikan_enabled: bool = True

    # Anime images
    waifu_im_enabled: bool = True

    # Wikipedia
    wikipedia_language: str = "en"
    wikipedia_user_agent: str = "InstagramBot/1.0"

    # Sherlock
    sherlock_enabled: bool = True
    sherlock_sites: Tuple[str, ...] = ()
    sherlock_timeout: int = 12

    # Paused commands — future configuration only (no provider integrated)
    phone_lookup_api_key: str = ""
    instagram_id_api_key: str = ""
    enable_num_command: bool = False
    enable_id_command: bool = False

    # HTTP hardening
    http_timeout: float = 20.0
    http_retries: int = 2

    warnings: Tuple[str, ...] = field(default=(), compare=False)

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        dotenv_path: Optional[str] = None,
    ) -> "Config":
        """Build a Config from environment variables.

        When *environ* is omitted the real process environment is used and a
        local ``.env`` file (if present) is loaded first — existing process
        variables always win over the file.
        """
        if environ is None:
            if load_dotenv is not None:
                load_dotenv(dotenv_path or (PROJECT_ROOT / ".env"), override=False)
            env: Mapping[str, str] = os.environ
        else:
            env = environ

        warnings: list = []

        model = _get_str(env, "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        if not model:
            model = DEFAULT_OPENROUTER_MODEL

        units = _get_str(env, "WEATHER_UNITS", "metric").lower()
        if units not in ("metric", "imperial"):
            warnings.append(f"WEATHER_UNITS={units!r} is not metric/imperial; using metric")
            units = "metric"

        return cls(
            app_env=_get_str(env, "APP_ENV", "production") or "production",
            log_level=(_get_str(env, "LOG_LEVEL", "INFO") or "INFO").upper(),
            health_port=_resolve_health_port(env, warnings),
            ig_username=_get_str(env, "IG_USERNAME"),
            ig_password=_get_str(env, "IG_PASSWORD"),
            ig_session_file=_get_str(env, "IG_SESSION_FILE", DEFAULT_IG_SESSION_FILE)
            or DEFAULT_IG_SESSION_FILE,
            ig_verification_code=_get_str(env, "IG_VERIFICATION_CODE"),
            ig_verification_file=_get_str(env, "IG_VERIFICATION_FILE", "auth/verification_code.txt")
            or "auth/verification_code.txt",
            ig_verification_wait=_get_int(
                env, "IG_VERIFICATION_WAIT", 300, warnings, minimum=0, maximum=3600
            ),
            ig_poll_interval=_get_int(env, "IG_POLL_INTERVAL", 60, warnings, minimum=5),
            ig_poll_amount=_get_int(env, "IG_POLL_AMOUNT", 20, warnings, minimum=1, maximum=50),
            ig_idle_after_minutes=_get_int(
                env, "IG_IDLE_AFTER_MINUTES", 10, warnings, minimum=1, maximum=720
            ),
            ig_idle_poll_interval=_get_int(
                env, "IG_IDLE_POLL_INTERVAL", 180, warnings, minimum=30, maximum=3600
            ),
            ig_reply_delay_min=_get_int(env, "IG_REPLY_DELAY_MIN", 3, warnings, minimum=0, maximum=60),
            ig_reply_delay_max=_get_int(env, "IG_REPLY_DELAY_MAX", 10, warnings, minimum=0, maximum=120),
            ig_min_request_gap=_get_float(env, "IG_MIN_REQUEST_GAP", 5.0, warnings, minimum=0.0),
            ig_max_replies_per_hour=_get_int(
                env, "IG_MAX_REPLIES_PER_HOUR", 20, warnings, minimum=1, maximum=300
            ),
            ig_mark_seen=_get_bool(env, "IG_MARK_SEEN", False, warnings),
            ig_reply_in_groups=_get_bool(env, "IG_REPLY_IN_GROUPS", False, warnings),
            ig_login_retry_min=_get_int(
                env, "IG_LOGIN_RETRY_MIN", 60, warnings, minimum=5, maximum=3600
            ),
            ig_login_retry_max=_get_int(
                env, "IG_LOGIN_RETRY_MAX", 1800, warnings, minimum=30, maximum=7200
            ),
            ig_rate_limit_cooldown=_get_int(
                env, "IG_RATE_LIMIT_COOLDOWN", 1200, warnings, minimum=60, maximum=7200
            ),
            redis_url=_get_str(env, "REDIS_URL"),
            openrouter_api_key=_get_str(env, "OPENROUTER_API_KEY"),
            openrouter_model=model,
            openrouter_site_url=_get_str(env, "OPENROUTER_SITE_URL"),
            openrouter_app_name=_get_str(env, "OPENROUTER_APP_NAME", "InstagramBot")
            or "InstagramBot",
            ai_request_timeout=_get_int(env, "AI_REQUEST_TIMEOUT", 60, warnings, minimum=5, maximum=300),
            ai_max_queue_size=_get_int(env, "AI_MAX_QUEUE_SIZE", 50, warnings, minimum=1, maximum=1000),
            ai_memory_turns=_get_int(env, "AI_MEMORY_TURNS", 4, warnings, minimum=0, maximum=20),
            ai_max_prompt_chars=_get_int(
                env, "AI_MAX_PROMPT_CHARS", 2000, warnings, minimum=100, maximum=20000
            ),
            genderize_api_key=_get_str(env, "GENDERIZE_API_KEY"),
            nationalize_api_key=_get_str(env, "NATIONALIZE_API_KEY"),
            weather_units=units,
            anilist_enabled=_get_bool(env, "ANILIST_ENABLED", True, warnings),
            jikan_enabled=_get_bool(env, "JIKAN_ENABLED", True, warnings),
            waifu_im_enabled=_get_bool(env, "WAIFU_IM_ENABLED", True, warnings),
            wikipedia_language=_get_str(env, "WIKIPEDIA_LANGUAGE", "en") or "en",
            wikipedia_user_agent=_get_str(env, "WIKIPEDIA_USER_AGENT", "InstagramBot/1.0")
            or "InstagramBot/1.0",
            sherlock_enabled=_get_bool(env, "SHERLOCK_ENABLED", True, warnings),
            sherlock_sites=_get_csv(env, "SHERLOCK_SITES"),
            sherlock_timeout=_get_int(env, "SHERLOCK_TIMEOUT", 12, warnings, minimum=3, maximum=60),
            phone_lookup_api_key=_get_str(env, "PHONE_LOOKUP_API_KEY"),
            instagram_id_api_key=_get_str(env, "INSTAGRAM_ID_API_KEY"),
            enable_num_command=_get_bool(env, "ENABLE_NUM_COMMAND", False, warnings),
            enable_id_command=_get_bool(env, "ENABLE_ID_COMMAND", False, warnings),
            http_timeout=_get_float(env, "HTTP_TIMEOUT", 20.0, warnings, minimum=1.0),
            http_retries=_get_int(env, "HTTP_RETRIES", 2, warnings, minimum=0, maximum=5),
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate_for_run(self) -> list:
        """Return blocking errors that prevent the bot from starting."""
        errors: list = []
        if not self.ig_username:
            errors.append("IG_USERNAME is required (the bot logs into Instagram).")
        if not self.ig_password:
            errors.append("IG_PASSWORD is required (the bot logs into Instagram).")
        return errors

    def soft_warnings(self) -> list:
        """Non-blocking notes: missing optional keys, dubious values, etc."""
        notes = list(self.warnings)
        if not self.openrouter_api_key:
            notes.append(
                "OPENROUTER_API_KEY is not set: /AI and /Gf will answer with a "
                "configuration error instead of calling OpenRouter."
            )
        if not self.redis_url:
            notes.append(
                "REDIS_URL is not set: the Instagram session is stored in local "
                "files only. On Voroa a redeploy wipes the filesystem and the "
                "bot must log in again (see README, 'Session persistence')."
            )
        if not self._is_free_model(self.openrouter_model):
            notes.append(
                f"OPENROUTER_MODEL={self.openrouter_model!r} is not a free model: "
                "OpenRouter will charge for /AI and /Gf usage. Use a ':free' model "
                "or the 'openrouter/free' router to avoid charges."
            )
        return notes

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_free_model(slug: str) -> bool:
        """True for OpenRouter models that cost $0 in tokens.

        Covers explicit ':free' variants and the official Free Models Router
        ('openrouter/free'), which routes each request to a free model.
        """
        return slug.endswith(":free") or slug == "openrouter/free"

    def session_file_path(self) -> Path:
        path = Path(self.ig_session_file)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def verification_file_path(self) -> Path:
        path = Path(self.ig_verification_file)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def summary(self) -> dict:
        """Redacted configuration snapshot safe for logging."""
        return {
            "app_env": self.app_env,
            "log_level": self.log_level,
            "health_port": self.health_port,
            "ig_username": self.ig_username or "<unset>",
            "ig_session_file": str(self.session_file_path()),
            "session_backend": "redis" if self.redis_url else "file",
            "openrouter_model": self.openrouter_model,
            "openrouter_api_key": "<set>" if self.openrouter_api_key else "<unset>",
            "ai_max_queue_size": self.ai_max_queue_size,
            "ai_memory_turns": self.ai_memory_turns,
            "weather_units": self.weather_units,
            "anilist_enabled": self.anilist_enabled,
            "jikan_enabled": self.jikan_enabled,
            "waifu_im_enabled": self.waifu_im_enabled,
            "sherlock_enabled": self.sherlock_enabled,
            "sherlock_sites": list(self.sherlock_sites) or ["<all sites>"],
            "wikipedia_language": self.wikipedia_language,
            "paused_commands": {
                "num_enabled": self.enable_num_command,
                "id_enabled": self.enable_id_command,
            },
        }
