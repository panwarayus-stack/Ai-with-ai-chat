"""Configuration parsing and validation tests."""

from __future__ import annotations

from config import DEFAULT_OPENROUTER_MODEL, Config


def test_defaults():
    cfg = Config.from_env(environ={})
    assert cfg.app_env == "production"
    assert cfg.log_level == "INFO"
    assert cfg.weather_units == "metric"
    assert cfg.openrouter_model == DEFAULT_OPENROUTER_MODEL
    assert cfg.enable_num_command is False
    assert cfg.enable_id_command is False
    assert cfg.ai_max_queue_size == 50


def test_openrouter_free_router_is_recognized_as_free():
    """'openrouter/free' (the Free Models Router) must not warn about charges."""
    cfg = Config.from_env(environ={"OPENROUTER_MODEL": "openrouter/free"})
    assert not any("not a free model" in note for note in cfg.soft_warnings())

    cfg = Config.from_env(environ={"OPENROUTER_MODEL": "openai/gpt-4o"})
    assert any("not a free model" in note for note in cfg.soft_warnings())


def test_anti_suspension_defaults():
    """Gentle mode is on out of the box."""
    cfg = Config.from_env(environ={})
    assert cfg.ig_poll_interval == 60
    assert cfg.ig_idle_after_minutes == 10
    assert cfg.ig_idle_poll_interval == 180
    assert cfg.ig_reply_delay_min == 3
    assert cfg.ig_reply_delay_max == 10
    assert cfg.ig_min_request_gap == 5.0
    assert cfg.ig_max_replies_per_hour == 20
    assert cfg.ig_mark_seen is False
    assert cfg.ig_reply_in_groups is False

    cfg = Config.from_env(
        environ={
            "IG_POLL_INTERVAL": "90",
            "IG_IDLE_AFTER_MINUTES": "30",
            "IG_IDLE_POLL_INTERVAL": "300",
            "IG_REPLY_DELAY_MIN": "1",
            "IG_REPLY_DELAY_MAX": "4",
            "IG_MIN_REQUEST_GAP": "2.5",
            "IG_MAX_REPLIES_PER_HOUR": "5",
            "IG_MARK_SEEN": "true",
            "IG_REPLY_IN_GROUPS": "true",
        }
    )
    assert cfg.ig_poll_interval == 90
    assert cfg.ig_idle_after_minutes == 30
    assert cfg.ig_idle_poll_interval == 300
    assert (cfg.ig_reply_delay_min, cfg.ig_reply_delay_max) == (1, 4)
    assert cfg.ig_min_request_gap == 2.5
    assert cfg.ig_max_replies_per_hour == 5
    assert cfg.ig_mark_seen is True
    assert cfg.ig_reply_in_groups is True


def test_required_credentials_validated():
    cfg = Config.from_env(environ={})
    errors = cfg.validate_for_run()
    assert any("IG_USERNAME" in e for e in errors)
    assert any("IG_PASSWORD" in e for e in errors)

    cfg = Config.from_env(environ={"IG_USERNAME": "bot", "IG_PASSWORD": "pw"})
    assert cfg.validate_for_run() == []


def test_optional_keys_are_optional():
    cfg = Config.from_env(environ={"IG_USERNAME": "bot", "IG_PASSWORD": "pw"})
    # No OpenRouter key etc. — must not be blocking errors.
    errors = cfg.validate_for_run()
    assert errors == []
    warnings = " ".join(cfg.soft_warnings())
    assert "OPENROUTER_API_KEY" in warnings
    assert "REDIS_URL" in warnings


def test_boolean_and_int_parsing_with_bad_values():
    cfg = Config.from_env(
        environ={
            "IG_USERNAME": "bot",
            "IG_PASSWORD": "pw",
            "ANILIST_ENABLED": "false",
            "JIKAN_ENABLED": "TRUE",
            "ENABLE_NUM_COMMAND": "no",
            "IG_POLL_INTERVAL": "not-a-number",
            "AI_MAX_QUEUE_SIZE": "3",  # below minimum 1? no - valid
            "AI_MEMORY_TURNS": "9999",  # clamped to max 20
        }
    )
    assert cfg.anilist_enabled is False
    assert cfg.jikan_enabled is True
    assert cfg.enable_num_command is False
    assert cfg.ig_poll_interval == 60  # fallback used
    assert cfg.ai_max_queue_size == 3
    assert cfg.ai_memory_turns == 20
    assert any("IG_POLL_INTERVAL" in w for w in cfg.warnings)


def test_weather_units_validated():
    cfg = Config.from_env(environ={"WEATHER_UNITS": "kelvin"})
    assert cfg.weather_units == "metric"
    cfg = Config.from_env(environ={"WEATHER_UNITS": "imperial"})
    assert cfg.weather_units == "imperial"


def test_sherlock_sites_csv():
    cfg = Config.from_env(environ={"SHERLOCK_SITES": "GitHub, X ,Instagram"})
    assert cfg.sherlock_sites == ("GitHub", "X", "Instagram")
    cfg = Config.from_env(environ={})
    assert cfg.sherlock_sites == ()


def test_summary_redacts_secrets():
    cfg = Config.from_env(
        environ={"IG_USERNAME": "bot", "IG_PASSWORD": "hunter2", "OPENROUTER_API_KEY": "sk-secret"}
    )
    summary = str(cfg.summary())
    assert "hunter2" not in summary
    assert "sk-secret" not in summary
    assert "<set>" in summary


def test_model_default_applied_when_empty():
    cfg = Config.from_env(environ={"OPENROUTER_MODEL": ""})
    assert cfg.openrouter_model == DEFAULT_OPENROUTER_MODEL


def test_verification_live_fields():
    from pathlib import Path

    cfg = Config.from_env(
        environ={
            "IG_VERIFICATION_WAIT": "60",
            "IG_VERIFICATION_FILE": "/tmp/live_code.txt",
        }
    )
    assert cfg.ig_verification_wait == 60
    assert cfg.verification_file_path() == Path("/tmp/live_code.txt")

    cfg = Config.from_env(environ={})
    assert cfg.ig_verification_wait == 300
    assert cfg.verification_file_path().name == "verification_code.txt"

    cfg = Config.from_env(environ={"IG_VERIFICATION_WAIT": "0"})
    assert cfg.ig_verification_wait == 0  # fail-fast mode allowed
