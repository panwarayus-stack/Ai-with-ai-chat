"""Command registry tests: 16 active commands, paused placeholders excluded."""

from __future__ import annotations

import pytest

from src.commands import load_all
from src.commands.base import COMMANDS, CommandResult, dispatch

EXPECTED_ACTIVE = {
    "info", "gender", "ai", "gf", "sherlock", "help", "weather", "anime",
    "pic", "roast", "ship", "truth", "dare", "quote", "celeb", "wiki",
}


@pytest.fixture(autouse=True)
def _commands_loaded():
    load_all()


def test_all_16_active_commands_registered():
    assert set(COMMANDS) == EXPECTED_ACTIVE
    assert len(COMMANDS) == 16


def test_paused_commands_not_in_registry():
    assert "num" not in COMMANDS
    assert "id" not in COMMANDS


def test_every_command_has_documentation():
    for command in COMMANDS.values():
        assert command.usage.startswith("/")
        assert command.description
        assert command.examples, f"/{command.name} has no examples"
        assert command.category


def test_registry_is_case_insensitive():
    from src.commands.base import get_command

    assert get_command("Info") is get_command("info")
    assert get_command("HELP") is get_command("help")


def test_dispatch_unknown_command_mentions_help(make_ctx):
    ctx = make_ctx()
    result = dispatch(ctx, "/DefinitelyNotACommand")
    assert result is not None
    assert "/Help" in result.text


def test_dispatch_non_command_returns_none(make_ctx):
    ctx = make_ctx()
    assert dispatch(ctx, "hello there") is None
    assert dispatch(ctx, "") is None


def test_all_active_commands_callable_without_crash(make_ctx):
    """Every registered handler runs and returns a CommandResult (services stubbed)."""
    http = make_ctx().services  # build a fresh context below per case
    cases = {
        "info": "",
        "gender": "Kim",
        "ai": "hello",
        "gf": "hi",
        "sherlock": "torvalds",
        "help": "",
        "weather": "Agra",
        "anime": "Naruto",
        "pic": "",
        "roast": "Alex",
        "ship": "Alex Sam",
        "truth": "",
        "dare": "",
        "quote": "",
        "celeb": "Marie Curie",
        "wiki": "Taj Mahal",
    }
    assert set(cases) == set(COMMANDS)

    from src.services.sherlock import SherlockMatch
    from tests.conftest import (
        ANILIST_OK,
        COMMONS_META_OK,
        FakeHttp,
        FakeResponse,
        GENDERIZE_OK,
        NATIONALIZE_OK,
        NEKOS_HUSBANDO_OK,
        OPENROUTER_OK,
        WAIFU_IM_OK,
        WIKI_SEARCH_OK,
        WIKI_SUMMARY_PERSON,
        WIKI_SUMMARY_TOPIC,
        StubSherlock,
    )

    for name, args in cases.items():
        http = FakeHttp()
        http.route("api.genderize.io", FakeResponse(json_data=GENDERIZE_OK))
        http.route("api.nationalize.io", FakeResponse(json_data=NATIONALIZE_OK))
        http.route("graphql.anilist.co", FakeResponse(json_data=ANILIST_OK))
        http.route("api.waifu.im", FakeResponse(json_data=WAIFU_IM_OK))
        http.route("nekos.best", FakeResponse(json_data=NEKOS_HUSBANDO_OK))
        http.route("openrouter.ai", FakeResponse(json_data=OPENROUTER_OK))
        http.route("geocoding-api.open-meteo.com", FakeResponse(json_data={
            "results": [{"name": "Agra", "country": "India", "latitude": 27.18, "longitude": 78.02, "timezone": "Asia/Kolkata"}]
        }))
        http.route("api.open-meteo.com", FakeResponse(json_data={
            "current": {"temperature_2m": 31.0, "apparent_temperature": 34.0, "wind_speed_10m": 8.0,
                        "weather_code": 0, "is_day": 1, "time": "2026-10-16T12:00", "relative_humidity_2m": 40},
            "daily": {"time": ["2026-10-16"], "weather_code": [1], "temperature_2m_max": [33], "temperature_2m_min": [22]},
        }))
        http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data=WIKI_SEARCH_OK))
        http.route("en.wikipedia.org/api/rest_v1/page/summary/Marie_Curie", FakeResponse(json_data=WIKI_SUMMARY_PERSON))
        http.route("en.wikipedia.org/api/rest_v1/page/summary/Taj_Mahal", FakeResponse(json_data=WIKI_SUMMARY_TOPIC))
        http.route("commons.wikimedia.org", FakeResponse(json_data=COMMONS_META_OK))
        http.route("upload.wikimedia.org", FakeResponse(content=b"\x89PNG fake", content_type="image/png"))
        http.route("cdn.waifu.im", FakeResponse(content=b"\x89PNG fake", content_type="image/png"))
        http.route("cdn.nekos.best", FakeResponse(content=b"\x89PNG fake", content_type="image/png"))
        http.route("img.anilist.co", FakeResponse(content=b"\x89PNG fake", content_type="image/png"))

        ctx = make_ctx(
            http,
            sherlock=StubSherlock(matches=[SherlockMatch("GitHub", "https://github.com/torvalds")]),
        )
        result = dispatch(ctx, f"/{name} {args}".strip())
        assert isinstance(result, CommandResult), f"/{name} did not return a CommandResult"
        # Either an answer or an honest error message — never an empty silence.
        assert result.text or result.image, f"/{name} returned nothing"
