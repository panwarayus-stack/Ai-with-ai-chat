"""Live configuration: SSH-friendly credentials and one-time codes.

Covers the "add them later" workflow: the bot waits for configuration and
picks up values delivered at runtime (``python app.py --set-credentials`` /
``--set-code``) without a restart.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from src.services.instagram import (
    CREDENTIALS_KEY,
    VERIFICATION_KEY,
    InstagramError,
    InstagramService,
)
from src.services.session_store import FileSessionStore


@pytest.fixture
def store(tmp_path):
    return FileSessionStore(tmp_path / "state")


@pytest.fixture
def service(store, tmp_path):
    return InstagramService(
        username="envuser",
        password="envpass",
        session_file=tmp_path / "auth" / "instagram_session.json",
        store=store,
        verification_code="111111",
        verification_file=tmp_path / "auth" / "verification_code.txt",
        verification_wait=2,
    )


# ---------------------------------------------------------------------- #
# Credentials
# ---------------------------------------------------------------------- #
def test_env_credentials_used_by_default(service):
    assert service.resolve_credentials() == ("envuser", "envpass")
    assert service.has_credentials() is True


def test_live_credentials_win_over_env(service, store):
    service.provide_credentials("sshuser", "sshpass")
    assert service.resolve_credentials() == ("sshuser", "sshpass")
    # Stored where the running bot can see it
    data = json.loads(store.get(CREDENTIALS_KEY))
    assert data["username"] == "sshuser"


def test_provide_credentials_validates(service):
    with pytest.raises(ValueError):
        service.provide_credentials("", "x")
    with pytest.raises(ValueError):
        service.provide_credentials("user", "")


def test_clear_credentials_reverts_to_env(service):
    service.provide_credentials("sshuser", "sshpass")
    service.clear_credentials()
    assert service.resolve_credentials() == ("envuser", "envpass")


def test_ensure_credentials_waits_for_live_values(store, tmp_path):
    service = InstagramService(
        username="",
        password="",
        session_file=tmp_path / "s.json",
        store=store,
        verification_file=tmp_path / "code.txt",
    )
    assert service.has_credentials() is False

    def provider():
        time.sleep(0.1)
        service.provide_credentials("lateuser", "latepass")

    thread = threading.Thread(target=provider)
    thread.start()
    username, password = service._ensure_credentials(timeout=5.0, poll=0.02)
    thread.join()
    assert (username, password) == ("lateuser", "latepass")


def test_ensure_credentials_times_out(store, tmp_path):
    service = InstagramService(
        username="", password="", session_file=tmp_path / "s.json", store=store
    )
    with pytest.raises(InstagramError) as exc:
        service._ensure_credentials(timeout=0.05, poll=0.02)
    assert "set-credentials" in exc.value.message


# ---------------------------------------------------------------------- #
# Verification codes (OTP)
# ---------------------------------------------------------------------- #
def test_verification_code_priority_file_beats_env(service):
    # env value exists ("111111") but a live file wins
    service.verification_file.parent.mkdir(parents=True, exist_ok=True)
    service.verification_file.write_text("222222")
    assert service.resolve_verification_code() == "222222"


def test_verification_code_store_beats_env(service):
    service.store.set(VERIFICATION_KEY, "333333")
    assert service.resolve_verification_code() == "333333"


def test_verification_code_env_fallback(service):
    assert service.resolve_verification_code() == "111111"


def test_provide_and_clear_code_touches_all_live_sources(service, store):
    service.provide_verification_code("444444")
    assert service.verification_file.read_text() == "444444"
    assert store.get(VERIFICATION_KEY) == "444444"

    service.clear_verification_code()
    assert not service.verification_file.exists()
    assert store.get(VERIFICATION_KEY) is None
    # env value remains as fallback
    assert service.resolve_verification_code() == "111111"


def test_provide_code_validates(service):
    with pytest.raises(ValueError):
        service.provide_verification_code("")
    with pytest.raises(ValueError):
        service.provide_verification_code("not-a-code")


def test_await_verification_code_returns_when_file_arrives(service):
    def provider():
        time.sleep(0.1)
        service.provide_verification_code("555555")

    thread = threading.Thread(target=provider)
    thread.start()
    code = service.await_verification_code(timeout=5.0, poll=0.02)
    thread.join()
    assert code == "555555"


def test_await_verification_code_times_out_empty(service):
    service.clear_verification_code()
    assert service.await_verification_code(timeout=0.05, poll=0.02) == ""


def test_code_is_one_time_after_login(service):
    """_on_login_success must clear live codes."""
    service.provide_verification_code("666666")
    service._on_login_success("envuser")
    assert service._live_code() == ""


# ---------------------------------------------------------------------- #
# CLI: app.main --set-credentials / --set-code / --clear-code
# ---------------------------------------------------------------------- #
def test_cli_set_credentials_prompts_for_password(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IG_SESSION_FILE", str(tmp_path / "auth" / "session.json"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "promptpass")
    code = app.main(["--set-credentials", "cliuser"])
    assert code == 0
    out = capsys.readouterr().out
    assert "no restart needed" in out
    assert "promptpass" not in out  # never printed back

    store = FileSessionStore(tmp_path / "auth" / "state")
    data = json.loads(store.get(CREDENTIALS_KEY))
    assert data == {"username": "cliuser", "password": "promptpass"}


def test_cli_set_credentials_two_args(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IG_SESSION_FILE", str(tmp_path / "auth" / "session.json"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    code = app.main(["--set-credentials", "cliuser", "clipass"])
    assert code == 0
    assert "shell history" in capsys.readouterr().out  # warns about argv form


def test_cli_set_code_and_clear(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IG_SESSION_FILE", str(tmp_path / "auth" / "session.json"))
    monkeypatch.setenv("IG_VERIFICATION_FILE", str(tmp_path / "auth" / "verification_code.txt"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    assert app.main(["--set-code", "987654"]) == 0
    code_file = tmp_path / "auth" / "verification_code.txt"
    assert code_file.read_text() == "987654"

    assert app.main(["--clear-code"]) == 0
    assert not code_file.exists()
    out = capsys.readouterr().out
    assert "cleared" in out.lower()
    assert "987654" not in out  # codes are never echoed


def test_cli_set_code_rejects_bad_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IG_SESSION_FILE", str(tmp_path / "auth" / "session.json"))
    monkeypatch.setenv("IG_VERIFICATION_FILE", str(tmp_path / "auth" / "verification_code.txt"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    assert app.main(["--set-code", "abc"]) == 2
    assert "numeric" in capsys.readouterr().err.lower()
