"""Session-first sign-in: a stored/imported session, no server-side login.

The server must sign in with an old or locally generated session
(``python app.py --login-only`` on the user's machine, then imported via the
dashboard or ``--import-session``) and must **never** perform a password
login while that session is accepted — not even as a fallback when a probe
is flaky. Password login happens only when there is no session, or when
Instagram explicitly rejects the one we have.
"""

from __future__ import annotations

import json
import threading

import pytest
from instagrapi.exceptions import ClientLoginRequired

from src.services.instagram import SESSION_KEY, InstagramError, InstagramService
from src.services.session_store import FileSessionStore

DUMP = json.dumps(
    {
        "cookies": {"sessionid": "4242%3Aabcdef%3A27", "ds_user_id": "4242"},
        "authorization_data": {"ds_user_id": "4242"},
        "device_settings": {"device_id": "only-a-test"},
    }
)


class FakeSessionClient:
    """Minimal stand-in for instagrapi.Client on the session paths."""

    def __init__(self, feed_exc=None):
        self.feed_exc = feed_exc
        self.settings = {}
        self.login_calls = []

    def set_settings(self, settings):
        self.settings = settings
        return True

    def get_settings(self):
        return self.settings or json.loads(DUMP)

    def get_timeline_feed(self):
        if self.feed_exc is not None:
            raise self.feed_exc
        return []

    def login(self, username, password, verification_code=""):
        self.login_calls.append((username, password, verification_code))
        return True


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
        verification_wait=2,
    )


# ---------------------------------------------------------------------- #
# login(): session first, password only when needed
# ---------------------------------------------------------------------- #
def test_stored_session_signs_in_without_password_login(store, tmp_path):
    """Session-only sign-in: works with NO credentials and never calls login()."""
    service = InstagramService(
        username="",
        password="",
        session_file=tmp_path / "auth" / "instagram_session.json",
        store=store,
    )
    store.set(SESSION_KEY, DUMP)
    fake = FakeSessionClient()
    service._client = fake

    service.login()

    assert fake.login_calls == [], "the password login endpoint must not be touched"
    assert json.loads(store.get("bot:status"))["state"] == "online"
    assert store.get(SESSION_KEY), "the accepted session is persisted"


def test_rejected_session_falls_back_to_password_login(service, store):
    store.set(SESSION_KEY, DUMP)
    fake = FakeSessionClient(feed_exc=ClientLoginRequired("login_required"))
    service._client = fake

    service.login()

    assert len(fake.login_calls) == 1
    assert fake.login_calls[0][:2] == ("envuser", "envpass")
    assert store.get(SESSION_KEY), "the fresh login's session is persisted"


def test_rejected_session_is_cleared(service, store):
    store.set(SESSION_KEY, DUMP)
    service._client = FakeSessionClient(feed_exc=ClientLoginRequired("login_required"))

    assert service._try_cached_session() is False
    assert not service.has_session()


def test_transient_probe_failure_never_password_logins(service, store):
    """A flaky check is not a dead session: raise and retry — no fallback login."""
    store.set(SESSION_KEY, DUMP)
    fake = FakeSessionClient(feed_exc=RuntimeError("connection reset"))
    service._client = fake

    with pytest.raises(InstagramError):
        service.login()

    assert fake.login_calls == []
    assert service.has_session(), "the session must survive a flaky probe"


def test_no_session_probe_skipped(service):
    service._client = FakeSessionClient()
    assert service._try_cached_session() is False
    assert service._client.login_calls == []


# ---------------------------------------------------------------------- #
# Waiting: session import unblocks the bot without credentials
# ---------------------------------------------------------------------- #
def test_ensure_credentials_returns_empty_when_session_present(store, tmp_path):
    service = InstagramService(
        username="",
        password="",
        session_file=tmp_path / "auth" / "instagram_session.json",
        store=store,
    )
    store.set(SESSION_KEY, DUMP)
    assert service._ensure_credentials(timeout=0.05, poll=0.01) == ("", "")


def test_wait_ends_when_session_is_imported(store, tmp_path):
    service = InstagramService(
        username="",
        password="",
        session_file=tmp_path / "auth" / "instagram_session.json",
        store=store,
    )
    threading.Timer(0.1, lambda: service.provide_session(DUMP)).start()
    assert service._ensure_credentials(timeout=5.0, poll=0.02) == ("", "")


# ---------------------------------------------------------------------- #
# provide_session / has_session / clear_session
# ---------------------------------------------------------------------- #
def test_provide_session_validates(service, store):
    with pytest.raises(ValueError):
        service.provide_session("")
    with pytest.raises(ValueError):
        service.provide_session("not json at all")
    with pytest.raises(ValueError):
        service.provide_session("[]")
    with pytest.raises(ValueError):
        service.provide_session('{"device_settings": {}}')  # no cookies

    service.provide_session(DUMP)
    assert service.has_session()
    assert json.loads(store.get(SESSION_KEY))["cookies"]["sessionid"]

    service.clear_session()
    assert not service.has_session()


def test_imported_session_picked_up_live(store, tmp_path):
    """A session imported AFTER construction is used without any credentials."""
    service = InstagramService(
        username="",
        password="",
        session_file=tmp_path / "auth" / "instagram_session.json",
        store=store,
    )
    fake = FakeSessionClient()
    service._client = fake
    service.provide_session(DUMP)

    service.login()

    assert fake.login_calls == []
    assert json.loads(store.get("bot:status"))["state"] == "online"


# ---------------------------------------------------------------------- #
# CLI: --login-only / --import-session / --clear-session
# ---------------------------------------------------------------------- #
def test_cli_login_only_saves_session(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IG_USERNAME", "cliuser")
    monkeypatch.setenv("IG_PASSWORD", "clipass")
    monkeypatch.setenv("IG_SESSION_FILE", str(tmp_path / "auth" / "session.json"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    calls = []

    def fake_login(self):
        calls.append(True)
        self._store_session_payload(DUMP)

    monkeypatch.setattr(InstagramService, "login", fake_login)
    assert app.main(["--login-only"]) == 0
    out = capsys.readouterr().out
    assert calls, "the login path must run"
    assert "Session saved to" in out
    assert "--import-session" in out
    assert (tmp_path / "auth" / "session.json").read_text()
    assert "clipass" not in out  # never echoed


def test_cli_login_only_without_credentials_explains(monkeypatch, capsys):
    monkeypatch.delenv("IG_USERNAME", raising=False)
    monkeypatch.delenv("IG_PASSWORD", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import app

    assert app.main(["--login-only"]) == 2
    assert "No Instagram credentials" in capsys.readouterr().err


def test_cli_import_and_clear_session(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IG_SESSION_FILE", str(tmp_path / "auth" / "session.json"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    dump_file = tmp_path / "ig_session.json"
    dump_file.write_text(DUMP, encoding="utf-8")

    import app

    assert app.main(["--import-session", str(dump_file)]) == 0
    assert "no password login" in capsys.readouterr().out

    store = FileSessionStore(tmp_path / "auth" / "state")
    assert store.get(SESSION_KEY)

    assert app.main(["--clear-session"]) == 0
    assert "cleared" in capsys.readouterr().out.lower()
    assert store.get(SESSION_KEY) is None

    assert app.main(["--import-session", str(tmp_path / "missing.json")]) == 2
