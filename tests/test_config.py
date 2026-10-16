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
    assert cfg.ig_poll_interval == 20  # fallback used
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
