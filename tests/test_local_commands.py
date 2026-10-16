"""Local commands: /Help, /Roast, /Ship, /Truth, /Dare, /Quote (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from src.commands.base import dispatch

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_help_lists_all_active_commands(make_ctx):
    result = dispatch(make_ctx(), "/Help")
    text = result.text
    for name in ["Info", "Gender", "AI", "Gf", "Sherlock", "Help", "Weather", "Anime",
                 "Pic", "Roast", "Ship", "Truth", "Dare", "Quote", "Celeb", "Wiki"]:
        assert f"/{name}" in text, f"/{name} missing from help"


def test_help_excludes_paused_commands(make_ctx):
    result = dispatch(make_ctx(), "/Help")
    assert "/Num" not in result.text
    assert "/Id" not in result.text


def test_help_question_matches_troubleshooting(make_ctx):
    result = dispatch(make_ctx(), "/Help rate limit 429")
    assert "rate limit" in result.text.lower()
    result = dispatch(make_ctx(), "/Help instagram login challenge")
    assert "challenge" in result.text.lower() or "checkpoint" in result.text.lower()
    result = dispatch(make_ctx(), "/Help deploy voroa")
    assert "voroa" in result.text.lower()


def test_help_unknown_question_guides_user(make_ctx):
    result = dispatch(make_ctx(), "/Help quantum banana xyzzy")
    assert "don't have a help topic" in result.text.lower() or "help topic" in result.text.lower()


def test_roast_uses_name_and_stays_playful(make_ctx):
    result = dispatch(make_ctx(), "/Roast Alex")
    assert "Alex" in result.text
    assert "playful" in result.text.lower()
    # Templates must not contain hateful slurs — sanity check the bank.
    bank = json.loads((DATA_DIR / "roasts.json").read_text())
    banned = ["hate", "kill yourself", "racist"]
    for item in bank:
        low = item["template"].lower()
        assert not any(b in low for b in banned)


def test_ship_is_deterministic(make_ctx):
    ctx = make_ctx()
    first = dispatch(ctx, "/Ship Alex Sam").text
    second = dispatch(ctx, "/Ship Sam Alex").text  # score is order-independent
    score_line = [l for l in first.splitlines() if l.startswith("Compatibility")]
    assert score_line and score_line[0] in second
    assert "/100" in first
    assert "entertainment" in first.lower()


def test_ship_requires_two_names(make_ctx):
    result = dispatch(make_ctx(), "/Ship Alex")
    assert "two names" in result.text.lower() or "usage" in result.text.lower()


def test_truth_and_dare_from_local_bank(make_ctx):
    truth = dispatch(make_ctx(), "/Truth").text
    dares = dispatch(make_ctx(), "/Dare").text
    assert "truth question" in truth.lower()
    assert "dare" in dares.lower()
    questions = json.loads((DATA_DIR / "truths.json").read_text())
    assert any(q["question"] in truth for q in questions)
    dare_bank = json.loads((DATA_DIR / "dares.json").read_text())
    assert any(d["dare"] in dares for d in dare_bank)
    assert "skip anything" in dares.lower()  # safe-dare reminder


def test_quote_has_honest_attribution(make_ctx):
    result = dispatch(make_ctx(), "/Quote").text
    quotes = json.loads((DATA_DIR / "quotes.json").read_text())
    assert any(f"“{q['text']}”" in result for q in quotes)
    # Every quote in the bank must have an author or a source.
    for q in quotes:
        assert q.get("author") or q.get("source"), f"quote lacks attribution: {q}"


def test_local_commands_make_no_http_calls(make_ctx):
    ctx = make_ctx()
    for text in ["/Roast Sam", "/Ship A B", "/Truth", "/Dare", "/Quote", "/Help"]:
        dispatch(ctx, text)
    http = ctx.services.demographics.http  # the shared FakeHttp
    assert http.calls == [], "local commands must not touch the network"


def test_data_banks_are_well_formed():
    for filename, required in [
        ("roasts.json", ["template"]),
        ("truths.json", ["id", "question"]),
        ("dares.json", ["id", "dare", "difficulty"]),
        ("quotes.json", ["text"]),
    ]:
        items = json.loads((DATA_DIR / filename).read_text())
        assert len(items) >= 15, f"{filename} too small"
        for item in items:
            for key in required:
                assert item.get(key), f"{filename} entry missing {key}: {item}"
        if filename == "roasts.json":
            assert all("{name}" in item["template"] for item in items)
