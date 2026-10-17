"""Web dashboard: GUI login for credentials/OTP, status, settings, logs.

Everything the CLI could do live (``--set-credentials`` / ``--set-code``)
must work through the browser — writing the same store keys so the running
bot picks values up without a restart — and the dashboard itself must be
protected (admin login, CSRF, throttling, no secret echoes).
"""

from __future__ import annotations

import re

import pytest

from config import Config
from src.services.instagram import InstagramService
from src.services.session_store import FileSessionStore, load_json
from src.webui.app import WebUIServer, create_app

ADMIN_USER = "boss"
ADMIN_PASS = "correct-horse-battery"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "form must carry a CSRF token"
    return match.group(1)


@pytest.fixture
def store(tmp_path):
    return FileSessionStore(tmp_path / "state")


@pytest.fixture
def ig(tmp_path, store):
    return InstagramService(
        username="envuser",
        password="envpass",
        session_file=tmp_path / "session.json",
        store=store,
        verification_file=tmp_path / "code.txt",
    )


@pytest.fixture
def cfg(tmp_path):
    return Config.from_env(
        environ={
            "IG_USERNAME": "envuser",
            "IG_PASSWORD": "envpass",
            "IG_SESSION_FILE": str(tmp_path / "session.json"),
            "IG_VERIFICATION_FILE": str(tmp_path / "code.txt"),
        }
    )


@pytest.fixture
def client(cfg, store, ig):
    app = create_app(cfg, store, ig)
    app.config["TESTING"] = True
    return app.test_client()


def _setup_and_login(client, username=ADMIN_USER, password=ADMIN_PASS):
    page = client.get("/setup").get_data(as_text=True)
    client.post(
        "/setup",
        data={"csrf": _csrf(page), "username": username, "password": password, "confirm": password},
    )
    page = client.get("/login").get_data(as_text=True)
    response = client.post(
        "/login", data={"csrf": _csrf(page), "username": username, "password": password}
    )
    assert response.status_code == 302


# ---------------------------------------------------------------------- #
# Setup & login
# ---------------------------------------------------------------------- #
def test_first_run_setup_then_login(client):
    response = client.get("/setup")
    assert response.status_code == 200
    assert "Create the admin account" in response.get_data(as_text=True)

    _setup_and_login(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Status" in response.get_data(as_text=True)


def test_setup_redirects_once_admin_exists(client):
    _setup_and_login(client)
    response = client.get("/setup")
    assert response.status_code == 302  # -> /login


def test_env_password_needs_no_setup(tmp_path, store, ig):
    cfg = Config.from_env(
        environ={
            "IG_USERNAME": "u", "IG_PASSWORD": "p",
            "WEB_PASSWORD": "env-admin-pass",
            "IG_SESSION_FILE": str(tmp_path / "s.json"),
            "IG_VERIFICATION_FILE": str(tmp_path / "c.txt"),
        }
    )
    app = create_app(cfg, store, ig)
    app.config["TESTING"] = True
    client = app.test_client()
    assert client.get("/setup").status_code == 302  # setup not offered
    page = client.get("/login").get_data(as_text=True)
    response = client.post(
        "/login",
        data={"csrf": _csrf(page), "username": "admin", "password": "env-admin-pass"},
    )
    assert response.status_code == 302


def test_pages_require_login(client):
    for path in ("/", "/credentials", "/settings", "/logs"):
        response = client.get(path)
        assert response.status_code == 302 and "/login" in response.headers["Location"]
    # Health probes stay public so deploys pass without auth.
    for path in ("/health", "/healthz"):
        assert client.get(path).status_code == 200


def test_login_throttles_repeated_failures(client):
    _setup_and_login(client)
    page = client.get("/login").get_data(as_text=True)
    for _ in range(10):
        client.post("/login", data={"csrf": _csrf(page), "username": ADMIN_USER, "password": "wrong"})
    response = client.post(
        "/login", data={"csrf": _csrf(page), "username": ADMIN_USER, "password": ADMIN_PASS}
    )
    assert "Too many attempts" in response.get_data(as_text=True)


# ---------------------------------------------------------------------- #
# Credentials & one-time codes (the GUI login)
# ---------------------------------------------------------------------- #
def test_credentials_written_live_for_the_bot(client, ig):
    _setup_and_login(client)
    page = client.get("/credentials").get_data(as_text=True)
    client.post(
        "/credentials",
        data={
            "csrf": _csrf(page), "action": "save_credentials",
            "ig_username": "iammrweirdoo", "ig_password": "s3cret-pw",
        },
    )
    # Same resolution path the running bot uses — no restart involved.
    assert ig.resolve_credentials() == ("iammrweirdoo", "s3cret-pw")


def test_blank_password_keeps_current(client, ig):
    _setup_and_login(client)
    page = client.get("/credentials").get_data(as_text=True)
    client.post(
        "/credentials",
        data={"csrf": _csrf(page), "action": "save_credentials",
              "ig_username": "user_one", "ig_password": "first-pass"},
    )
    page = client.get("/credentials").get_data(as_text=True)
    client.post(
        "/credentials",
        data={"csrf": _csrf(page), "action": "save_credentials",
              "ig_username": "user_two", "ig_password": ""},
    )
    assert ig.resolve_credentials() == ("user_two", "first-pass")


def test_otp_set_and_clear(client, ig):
    _setup_and_login(client)
    page = client.get("/credentials").get_data(as_text=True)
    client.post("/credentials", data={"csrf": _csrf(page), "action": "save_code", "code": "123456"})
    assert ig.has_live_code()
    assert ig.resolve_verification_code() == "123456"

    page = client.get("/credentials").get_data(as_text=True)
    client.post("/credentials", data={"csrf": _csrf(page), "action": "clear_code"})
    assert not ig.has_live_code()


def test_rejects_bad_code(client, ig):
    _setup_and_login(client)
    page = client.get("/credentials").get_data(as_text=True)
    response = client.post(
        "/credentials", data={"csrf": _csrf(page), "action": "save_code", "code": "not-a-code"}
    )
    assert "numeric" in response.get_data(as_text=True)
    assert not ig.has_live_code()


def test_password_never_echoed_back(client, ig):
    _setup_and_login(client)
    page = client.get("/credentials").get_data(as_text=True)
    client.post(
        "/credentials",
        data={"csrf": _csrf(page), "action": "save_credentials",
              "ig_username": "someone", "ig_password": "super-secret-value"},
    )
    html = client.get("/credentials").get_data(as_text=True)
    assert "super-secret-value" not in html


def test_csrf_required_on_posts(client):
    _setup_and_login(client)
    response = client.post(
        "/credentials",
        data={"action": "save_code", "code": "123456", "csrf": "forged"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------- #
# Settings & status & logs
# ---------------------------------------------------------------------- #
def test_settings_round_trip(client, store):
    _setup_and_login(client)
    page = client.get("/settings").get_data(as_text=True)
    response = client.post(
        "/settings",
        data={
            "csrf": _csrf(page),
            "ig_poll_interval": "90",
            "ig_min_request_gap": "2.5",
            "ig_max_replies_per_hour": "5",
            "ig_mark_seen": "true",
            "ig_reply_in_groups": "false",
        },
    )
    assert response.status_code == 200
    saved = load_json(store, "bot:settings", {})
    assert saved["ig_poll_interval"] == 90
    assert saved["ig_min_request_gap"] == 2.5
    assert saved["ig_max_replies_per_hour"] == 5
    assert saved["ig_mark_seen"] is True
    assert saved["ig_reply_in_groups"] is False

    # Invalid values are rejected with a visible error.
    page = client.get("/settings").get_data(as_text=True)
    response = client.post(
        "/settings", data={"csrf": _csrf(page), "ig_poll_interval": "99999"}
    )
    assert "between" in response.get_data(as_text=True)

    # Reset clears overrides.
    page = client.get("/settings").get_data(as_text=True)
    client.post("/settings", data={"csrf": _csrf(page), "action": "reset"})
    assert load_json(store, "bot:settings", {}) == {}


def test_status_page_shows_bot_state(client, store):
    from src.lib.bot_state import publish_status

    publish_status(store, "waiting_code", "Instagram wants a one-time code")
    _setup_and_login(client)
    html = client.get("/").get_data(as_text=True)
    assert "waiting_code" in html
    assert "Instagram wants a one-time code" in html


def test_logs_page_shows_buffer(client):
    _setup_and_login(client)
    assert "Logs" in client.get("/logs").get_data(as_text=True)


# ---------------------------------------------------------------------- #
# Server
# ---------------------------------------------------------------------- #
def test_web_server_serves_health_and_dashboard(cfg, store, ig):
    import json
    import urllib.request

    app = create_app(cfg, store, ig)
    server = WebUIServer(app, 0)  # ephemeral port
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urllib.request.urlopen(base + "/health", timeout=5) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
        # No admin yet: the dashboard lands on the first-run setup form.
        with urllib.request.urlopen(base + "/login", timeout=5) as response:
            assert response.status == 200
            assert "Create the admin account" in response.read().decode("utf-8")
    finally:
        server.stop()
