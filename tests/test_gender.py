""" /Gender: combined Genderize + Nationalize output and display rules. """

from __future__ import annotations

from src.commands.base import dispatch
from src.lib.http import HttpStatusError
from tests.conftest import (
    GENDERIZE_OK,
    NATIONALIZE_OK,
    FakeHttp,
    FakeResponse,
)


def _http_with_both():
    http = FakeHttp()
    http.route("api.genderize.io", FakeResponse(json_data=dict(GENDERIZE_OK)))
    http.route("api.nationalize.io", FakeResponse(json_data={
        "name": "kim",
        "country": [
            {"country_id": "US", "probability": 0.22},
            {"country_id": "GB", "probability": 0.19},
            {"country_id": "AU", "probability": 0.07},
        ],
    }))
    return http


def test_combines_both_providers_in_one_response(make_ctx):
    http = _http_with_both()
    ctx = make_ctx(http)
    result = dispatch(ctx, "/Gender Kim")
    text = result.text
    # Genderize half
    assert "female" in text
    assert "94.0%" in text
    assert "1,098" in text  # sample count
    # Nationalize half
    assert "US" in text
    assert "22.0%" in text
    assert "nationality" in text.lower()
    # Both APIs were called.
    assert any("genderize" in u for u in http.urls())
    assert any("nationalize" in u for u in http.urls())


def test_two_top_countries_shown_when_within_five_points(make_ctx):
    http = _http_with_both()  # US 22% vs GB 19% -> gap 3pp -> both shown
    ctx = make_ctx(http)
    result = dispatch(ctx, "/Gender Kim")
    assert "US" in result.text
    assert "GB" in result.text
    assert "within 5 points" in result.text


def test_only_top_country_shown_when_gap_exceeds_five_points(make_ctx):
    http = FakeHttp()
    http.route("api.genderize.io", FakeResponse(json_data=dict(GENDERIZE_OK)))
    http.route("api.nationalize.io", FakeResponse(json_data={
        "name": "kim",
        "country": [
            {"country_id": "US", "probability": 0.50},
            {"country_id": "GB", "probability": 0.10},
        ],
    }))
    result = dispatch(make_ctx(http), "/Gender Kim")
    assert "US" in result.text
    assert "50.0%" in result.text
    assert "within 5 points" not in result.text


def test_partial_failure_shows_other_result(make_ctx):
    http = FakeHttp()
    http.route("api.genderize.io", FakeResponse(raise_exc=HttpStatusError(429, "https://api.genderize.io/")))
    http.route("api.nationalize.io", FakeResponse(json_data=dict(NATIONALIZE_OK)))
    result = dispatch(make_ctx(http), "/Gender Kim")
    assert "unavailable" in result.text.lower()
    assert "quota" in result.text.lower()
    assert "US" in result.text  # nationality still shown

    http = FakeHttp()
    http.route("api.genderize.io", FakeResponse(json_data=dict(GENDERIZE_OK)))
    http.route("api.nationalize.io", FakeResponse(raise_exc=HttpStatusError(500, "https://api.nationalize.io/")))
    result = dispatch(make_ctx(http), "/Gender Kim")
    assert "female" in result.text
    assert "nationality: unavailable" in result.text.lower()


def test_both_failures_reported(make_ctx):
    http = FakeHttp()
    http.route("api.genderize.io", FakeResponse(raise_exc=HttpStatusError(429, "x")))
    http.route("api.nationalize.io", FakeResponse(raise_exc=HttpStatusError(429, "x")))
    result = dispatch(make_ctx(http), "/Gender Kim")
    assert "both providers failed" in result.text.lower()


def test_unknown_name_gets_honest_answer(make_ctx):
    http = FakeHttp()
    http.route("api.genderize.io", FakeResponse(json_data={"name": "zzzz", "gender": None, "probability": None, "count": 0}))
    http.route("api.nationalize.io", FakeResponse(json_data={"name": "zzzz", "country": []}))
    result = dispatch(make_ctx(http), "/Gender Zzzz")
    assert "unknown" in result.text.lower()
    assert "not a fact" in result.text.lower()


def test_api_keys_optional(make_ctx):
    http = _http_with_both()
    ctx = make_ctx(http)
    dispatch(ctx, "/Gender Kim")
    for call in http.calls:
        assert "apikey" not in (call.params or {})  # anonymous mode works


def test_missing_argument():
    # Validation errors happen before any context use.
    from src.commands.base import CommandContext, Services

    ctx = CommandContext(services=Services(), user_id=1, username="t", config=None)
    result = dispatch(ctx, "/Gender")
    assert "Usage" in result.text
