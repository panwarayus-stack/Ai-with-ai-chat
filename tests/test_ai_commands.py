"""AI commands (/AI, /Gf): shared queue, separate memory, honest errors."""

from __future__ import annotations

import threading
import time
from typing import List

from src.commands.base import dispatch
from src.lib.ai_queue import AIQueue
from src.lib.http import HttpStatusError
from tests.conftest import OPENROUTER_OK, FakeHttp, FakeResponse


def _openrouter_route(http: FakeHttp) -> None:
    http.route("openrouter.ai", FakeResponse(json_data=OPENROUTER_OK))


def test_ai_and_gf_share_one_queue_and_one_client(make_ctx):
    http = FakeHttp()
    _openrouter_route(http)

    shared_queue = AIQueue(max_size=5)
    ctx = make_ctx(http, ai_queue=shared_queue)

    submissions: List[str] = []
    original_submit = shared_queue.submit

    def spy(fn, *args, **kwargs):
        submissions.append(getattr(fn, "__qualname__", str(fn)))
        return original_submit(fn, *args, **kwargs)

    shared_queue.submit = spy  # type: ignore[method-assign]

    assert dispatch(ctx, "/AI what is 2+2?")
    assert dispatch(ctx, "/Gf hello there")

    # Both commands went through the SAME queue object.
    assert len(submissions) == 2
    # And the same client object handled both.
    calls = [c for c in http.calls if "openrouter.ai" in c.url]
    assert len(calls) == 2
    assert calls[0].body["model"] == calls[1].body["model"]
    shared_queue.close()


def test_ai_and_gf_use_separate_system_prompts_and_memory(make_ctx):
    http = FakeHttp()
    _openrouter_route(http)
    ctx = make_ctx(http)

    dispatch(ctx, "/AI remember the number 42")
    dispatch(ctx, "/Gf remember the number 42")

    calls = [c for c in http.calls if "openrouter.ai" in c.url]
    assert len(calls) == 2
    messages_ai = calls[0].body["messages"]
    messages_gf = calls[1].body["messages"]
    system_ai = messages_ai[0]["content"]
    system_gf = messages_gf[0]["content"]
    assert system_ai != system_gf, "/AI and /Gf must not share a system prompt"
    assert "helpful" in system_ai.lower()
    assert "aria" in system_gf.lower()

    # Memories are separate: a follow-up /AI call must not contain the /Gf reply.
    dispatch(ctx, "/AI what number did I say?")
    follow_up = [c for c in http.calls if "openrouter.ai" in c.url][2].body["messages"]
    role_contents = [m["content"] for m in follow_up]
    assert system_gf not in role_contents
    assert any("42" in content for content in role_contents)


def test_memory_is_bounded_per_user(make_ctx):
    http = FakeHttp()
    _openrouter_route(http)
    ctx = make_ctx(http)  # memory_turns=2 in conftest -> max 4 history messages

    for i in range(6):
        dispatch(ctx, f"/AI question number {i}")

    calls = [c for c in http.calls if "openrouter.ai" in c.url]
    last_messages = calls[-1].body["messages"]
    # system + at most 4 history + the new prompt = 6
    assert len(last_messages) <= 6


def test_single_flight_enforced_for_concurrent_commands(make_ctx):
    http = FakeHttp()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_route(call):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return FakeResponse(json_data=OPENROUTER_OK)

    http.route_fn("openrouter.ai", slow_route)
    shared_queue = AIQueue(max_size=10)
    ctx = make_ctx(http, ai_queue=shared_queue)

    threads = [threading.Thread(target=dispatch, args=(ctx, f"/AI q{i}")) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_active == 1
    shared_queue.close()


def test_missing_api_key_returns_error_not_fake_answer(make_ctx):
    http = FakeHttp()
    ctx = make_ctx(http, openrouter_key="")
    result = dispatch(ctx, "/AI hello")
    assert "OPENROUTER_API_KEY" in result.text
    assert http.calls == [], "must not call OpenRouter without a key"


def test_openrouter_rate_limit_reports_real_error(make_ctx):
    http = FakeHttp()
    http.route(
        "openrouter.ai",
        FakeResponse(raise_exc=HttpStatusError(429, "https://openrouter.ai/api/v1/chat/completions")),
    )
    ctx = make_ctx(http)
    result = dispatch(ctx, "/AI hello")
    assert "rate limit" in result.text.lower()
    assert "fake" not in result.text.lower()


def test_openrouter_model_error_mentions_model_config(make_ctx):
    http = FakeHttp()
    http.route(
        "openrouter.ai",
        FakeResponse(
            raise_exc=HttpStatusError(
                404,
                "https://openrouter.ai/api/v1/chat/completions",
                body='{"error": {"message": "model not found"}}',
            )
        ),
    )
    ctx = make_ctx(http)
    result = dispatch(ctx, "/AI hello")
    assert "OPENROUTER_MODEL" in result.text


def test_empty_prompt_rejected(make_ctx):
    ctx = make_ctx()
    result = dispatch(ctx, "/AI")
    assert "Usage" in result.text
    result = dispatch(ctx, "/Gf")
    assert "Usage" in result.text


def test_overlong_prompt_rejected(make_ctx, config):
    ctx = make_ctx()
    result = dispatch(ctx, "/AI " + "x" * (config.ai_max_prompt_chars + 10))
    assert "too long" in result.text.lower()
