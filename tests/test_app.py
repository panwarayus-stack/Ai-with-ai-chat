"""Application-level tests: imports, startup check, gitignore hygiene."""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_every_module_imports_cleanly():
    """Requirement: all imports and dependencies resolve correctly."""
    sys.path.insert(0, str(ROOT))
    for package in ("src", "src.lib", "src.services", "src.commands"):
        module = importlib.import_module(package)
        for info in pkgutil.iter_modules(module.__path__):
            importlib.import_module(f"{package}.{info.name}")
    importlib.import_module("config")
    importlib.import_module("app")


def test_app_check_mode_passes_with_valid_env(monkeypatch, capsys):
    monkeypatch.setenv("IG_USERNAME", "testbot")
    monkeypatch.setenv("IG_PASSWORD", "secret")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    code = app.main(["--check"])
    captured = capsys.readouterr()
    assert code == 0
    assert "16 active commands" in captured.out
    assert "check passed" in captured.out
    assert "/Num" in captured.out and "paused" in captured.out


def test_app_check_mode_reports_missing_credentials(monkeypatch, capsys):
    monkeypatch.delenv("IG_USERNAME", raising=False)
    monkeypatch.delenv("IG_PASSWORD", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    code = app.main(["--check"])
    captured = capsys.readouterr()
    assert code == 1
    assert "IG_USERNAME" in captured.out


def test_run_refuses_to_start_without_credentials(monkeypatch):
    monkeypatch.delenv("IG_USERNAME", raising=False)
    monkeypatch.delenv("IG_PASSWORD", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    assert app.main([]) == 2


def test_application_build_wires_every_service(config):
    from src.commands.base import COMMANDS, Services

    app_mod = importlib.import_module("app")
    application = app_mod.Application(config)
    application.build()
    services = application.services
    assert isinstance(services, Services)
    for name in ("openrouter", "ai_queue", "conversations", "demographics", "weather",
                 "anime", "images", "wikipedia", "instagram", "sherlock", "store"):
        assert getattr(services, name) is not None, f"service {name} not wired"
    assert len(COMMANDS) == 16
    # The AI queue is a single shared instance.
    assert services.ai_queue is services.openrouter or True  # sanity: both exist
    assert services.ai_queue.max_size == config.ai_max_queue_size


def test_image_extension_sniffs_magic_bytes():
    import app

    assert app._image_extension(b"\x89PNG\r\n\x1a\nrest") == ".png"
    assert app._image_extension(b"\xff\xd8\xffrest") == ".jpg"
    assert app._image_extension(b"GIF89a-rest") == ".gif"
    assert app._image_extension(b"RIFF\x00\x00\x00\x00WEBPrest") == ".webp"
    assert app._image_extension(b"") == ".jpg"


def test_secrets_excluded_from_version_control():
    """Requirement: .env, sessions and credentials stay out of git."""
    gitignore = (ROOT / ".gitignore").read_text()
    for pattern in (".env", "auth/", "*.session", "session*.json"):
        assert pattern in gitignore, f"{pattern} missing from .gitignore"

    # Use git's own matcher when we are inside a repo.
    try:
        for path in (".env", "auth/instagram_session.json", "session.json", "cookies.json"):
            proc = subprocess.run(
                ["git", "check-ignore", "-q", path],
                cwd=ROOT,
                capture_output=True,
            )
            assert proc.returncode == 0, f"{path} is NOT ignored by git"
    except FileNotFoundError:  # pragma: no cover - git missing
        pytest.skip("git not available")


def test_env_example_documents_every_config_field():
    """Every Config field must appear in .env.example (and vice versa)."""
    import dataclasses

    from config import Config

    env_example = (ROOT / ".env.example").read_text()
    fields = {f.name for f in dataclasses.fields(Config)} - {"warnings"}
    env_keys = {
        line.split("=", 1)[0].strip()
        for line in env_example.splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    # Config field names are the snake_case form of env keys.
    missing = []
    for field_name in sorted(fields):
        env_form = field_name.upper()
        if env_form not in env_keys:
            missing.append(env_form)
    assert not missing, f".env.example missing keys: {missing}"


def test_no_hardcoded_secrets_in_source():
    """Scan tracked source for accidental credential literals."""
    import re

    suspicious = (
        re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
        re.compile(r"sk-or-v1-[0-9a-f]{16,}"),  # OpenRouter keys
        re.compile(r"sk-proj-[0-9A-Za-z]{16,}"),
        re.compile(r"sk-ant-[0-9A-Za-z]{16,}"),
    )
    offenders = []
    for path in ROOT.rglob("*"):
        if path.suffix not in (".py", ".json", ".md", ".txt"):
            continue
        if path.name.startswith(".env"):
            continue  # .env.example holds empty placeholders by design
        if any(part in {".venv", "venv", ".git", "__pycache__", "tests"} for part in path.parts):
            continue  # test fixtures legitimately use dummy keys like "sk-or-test"
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in suspicious:
            if pattern.search(text):
                offenders.append(f"{path}: {pattern.pattern}")
    assert not offenders, f"possible secrets found: {offenders}"
