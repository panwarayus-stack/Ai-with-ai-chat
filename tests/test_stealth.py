"""Anti-suspension ("gentle mode") behaviour: pacing, budgets, quiet-until-active.

These tests lock in the account-safety features: minimal polling that slows
down when idle, request pacing, per-hour reply budgets, human-like reply
delays, and group-thread filtering.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.lib.utils import HourlyBudget
from src.services.instagram import InstagramService
from src.services.session_store import FileSessionStore
from tests.conftest import StubInstagram


# ---------------------------------------------------------------------- #
# HourlyBudget
# ---------------------------------------------------------------------- #
def test_hourly_budget_allows_up_to_limit():
    clock = {"now": 0.0}
    budget = HourlyBudget(limit=3, window_seconds=3600, clock=lambda: clock["now"])
    assert [budget.allow() for _ in range(4)] == [True, True, True, False]
    assert budget.remaining() == 0


def test_hourly_budget_resets_after_window():
    clock = {"now": 0.0}
    budget = HourlyBudget(limit=1, window_seconds=100, clock=lambda: clock["now"])
    assert budget.allow() is True
    assert budget.allow() is False
    clock["now"] = 101.0
    assert budget.allow() is True  # window rolled


def test_hourly_budget_reset():
    budget = HourlyBudget(limit=1)
    assert budget.allow() is True
    assert budget.allow() is False
    budget.reset()
    assert budget.allow() is True


# ---------------------------------------------------------------------- #
# Polling: quiet when idle, active around conversations
# ---------------------------------------------------------------------- #
def test_poll_delay_active_vs_idle(config):
    import app as app_mod

    application = app_mod.Application(config)
    now = time.monotonic()
    active = application._poll_delay(now)  # activity "just happened"
    idle = application._poll_delay(now - config.ig_idle_after_minutes * 60 - 1)

    # Jittered, but must stay within ±15% of the configured bases.
    assert config.ig_poll_interval * 0.85 <= active <= config.ig_poll_interval * 1.15
    assert config.ig_idle_poll_interval * 0.85 <= idle <= config.ig_idle_poll_interval * 1.15
    assert idle > active


def test_mark_seen_off_by_default(config):
    assert config.ig_mark_seen is False


def test_poll_once_respects_mark_seen_flag(config):
    import app as app_mod

    class FakeInboxInstagram(StubInstagram):
        def __init__(self):
            super().__init__()
            self.seen_calls = []
            self.processed = set()

        def fetch_unread(self, amount=20):
            return [
                SimpleNamespace(
                    thread_id=7, message_id="m1", user_id=5, username="u", text="hello", timestamp=1
                )
            ]

        def is_processed(self, message_id):
            return message_id in self.processed

        def mark_processed(self, message_id):
            self.processed.add(message_id)

        def mark_thread_seen(self, thread_id):
            self.seen_calls.append(thread_id)

    # Flag off (default): no seen-requests at all.
    application = app_mod.Application(config)
    application.build()
    stub = FakeInboxInstagram()
    application.services.instagram = stub
    assert application._poll_once() is True
    assert stub.seen_calls == []
    # Second poll: message already processed -> no new activity.
    assert application._poll_once() is False

    # Flag on: threads get marked seen.
    cfg_seen = config.__class__.from_env(
        environ={
            "IG_USERNAME": "t", "IG_PASSWORD": "p",
            "IG_MARK_SEEN": "true",
            "IG_REPLY_DELAY_MIN": "0", "IG_REPLY_DELAY_MAX": "0", "IG_MIN_REQUEST_GAP": "0",
        }
    )
    application2 = app_mod.Application(cfg_seen)
    application2.build()
    stub2 = FakeInboxInstagram()
    application2.services.instagram = stub2
    application2._poll_once()
    assert stub2.seen_calls == [7]


# ---------------------------------------------------------------------- #
# Replies: budget + human-like delay
# ---------------------------------------------------------------------- #
def _application_with_stub(config, monkeypatch):
    import app as app_mod

    application = app_mod.Application(config)
    application.build()
    stub = StubInstagram()
    application.services.instagram = stub
    return application, stub


def test_replies_are_budgeted_per_hour(config, monkeypatch):
    cfg = config.__class__.from_env(
        environ={
            "IG_USERNAME": "t", "IG_PASSWORD": "p",
            "IG_MAX_REPLIES_PER_HOUR": "1",
            "IG_REPLY_DELAY_MIN": "0", "IG_REPLY_DELAY_MAX": "0", "IG_MIN_REQUEST_GAP": "0",
        }
    )
    application, stub = _application_with_stub(cfg, monkeypatch)

    msg = SimpleNamespace(thread_id=1, message_id="a", user_id=2, username="u", text="/Quote", timestamp=1)
    application.handle_message(msg)
    application.handle_message(SimpleNamespace(thread_id=1, message_id="b", user_id=2, username="u", text="/Truth", timestamp=2))
    # Only one reply went out — the account stays quiet beyond its budget.
    assert len(stub.sent_texts) == 1
    assert application._reply_budget.remaining() == 0


def test_human_like_delay_before_reply(config, monkeypatch):
    cfg = config.__class__.from_env(
        environ={
            "IG_USERNAME": "t", "IG_PASSWORD": "p",
            "IG_REPLY_DELAY_MIN": "5", "IG_REPLY_DELAY_MAX": "5",
            "IG_MIN_REQUEST_GAP": "0",
        }
    )
    application, stub = _application_with_stub(cfg, monkeypatch)

    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    application.handle_message(
        SimpleNamespace(thread_id=1, message_id="a", user_id=2, username="u", text="/Quote", timestamp=1)
    )
    assert 5.0 in slept, "reply must wait the human-like pause first"
    assert stub.sent_texts


def test_non_commands_cost_nothing(config, monkeypatch):
    application, stub = _application_with_stub(config, monkeypatch)
    application.handle_message(
        SimpleNamespace(thread_id=1, message_id="a", user_id=2, username="u", text="just chatting", timestamp=1)
    )
    assert stub.sent_texts == []
    assert application._reply_budget.remaining() == config.ig_max_replies_per_hour


# ---------------------------------------------------------------------- #
# Request pacing + group filtering (InstagramService)
# ---------------------------------------------------------------------- #
class FakeClient:
    def __init__(self):
        self.user_id = 1
        self.send_times = []
        self.threads = []

    def direct_send(self, text, user_ids=None, thread_ids=None):
        self.send_times.append(time.monotonic())
        return SimpleNamespace()

    def direct_threads(self, amount=20, selected_filter=None, box=None, thread_message_limit=None):
        return self.threads


def _service(tmp_path, **kwargs):
    return InstagramService(
        username="t",
        password="p",
        session_file=tmp_path / "s.json",
        store=FileSessionStore(tmp_path / "state"),
        verification_file=tmp_path / "code.txt",
        **kwargs,
    )


def test_request_gap_paces_instagram_calls(tmp_path):
    service = _service(tmp_path, min_request_gap=0.05)
    client = FakeClient()
    service._client = client

    start = time.monotonic()
    service.send_text(1, "one")
    service.send_text(1, "two")
    service.send_text(1, "three")
    elapsed = time.monotonic() - start

    assert len(client.send_times) == 3
    assert elapsed >= 0.10, "two gaps of 50ms minimum between three calls"
    for earlier, later in zip(client.send_times, client.send_times[1:]):
        assert later - earlier >= 0.045  # small tolerance for clock granularity


def test_no_pacing_when_gap_zero(tmp_path):
    service = _service(tmp_path, min_request_gap=0)
    client = FakeClient()
    service._client = client
    start = time.monotonic()
    service.send_text(1, "one")
    service.send_text(1, "two")
    assert time.monotonic() - start < 0.05


def test_group_threads_skipped_by_default(tmp_path):
    service = _service(tmp_path, min_request_gap=0, reply_in_groups=False)
    client = FakeClient()
    solo = SimpleNamespace(
        id=10, is_group=False, thread_type="private", users=[],
        messages=[SimpleNamespace(id="m1", user_id=2, text="hi", timestamp=1)],
    )
    group = SimpleNamespace(
        id=11, is_group=True, thread_type="group", users=[],
        messages=[SimpleNamespace(id="m2", user_id=3, text="hi all", timestamp=2)],
    )
    client.threads = [solo, group]
    service._client = client

    messages = service.fetch_unread()
    assert [m.message_id for m in messages] == ["m1"], "group traffic must be ignored"


def test_group_threads_used_when_enabled(tmp_path):
    service = _service(tmp_path, min_request_gap=0, reply_in_groups=True)
    client = FakeClient()
    client.threads = [
        SimpleNamespace(
            id=11, is_group=True, thread_type="group", users=[],
            messages=[SimpleNamespace(id="m2", user_id=3, text="hi all", timestamp=2)],
        )
    ]
    service._client = client
    messages = service.fetch_unread()
    assert [m.message_id for m in messages] == ["m2"]
