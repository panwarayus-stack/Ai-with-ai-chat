"""/Anime: AniList primary, Jikan fallback, relations classification."""

from __future__ import annotations

from src.commands.base import dispatch
from src.lib.http import HttpStatusError
from tests.conftest import ANILIST_OK, JIKAN_OK, FakeHttp, FakeResponse


def _anilist_http():
    http = FakeHttp()
    http.route("graphql.anilist.co", FakeResponse(json_data=ANILIST_OK))
    http.route("img.anilist.co", FakeResponse(content=b"png", content_type="image/png"))
    return http


def test_anilist_is_primary_source(make_ctx):
    http = _anilist_http()
    result = dispatch(make_ctx(http), "/Anime Naruto")
    text = result.text
    assert "Naruto" in text
    assert "Action, Adventure" in text  # genres from the real payload
    assert "220 episodes" in text
    assert "Finished" in text
    assert "2002" in text
    assert "anilist.co/anime/20" in text  # real source link
    assert "Naruto Uzumaki, Sasuke Uchiha" in text  # characters
    assert result.image == b"png", "poster should be sent as an image"
    assert not any("jikan" in u for u in http.urls()), "Jikan must not be called when AniList works"


def test_seasons_distinguished_from_related_works(make_ctx):
    result = dispatch(make_ctx(_anilist_http()), "/Anime Naruto")
    text = result.text
    assert "Seasons / direct order:" in text
    assert "Sequel: Naruto Shippuden" in text
    assert "Related works (not seasons):" in text
    assert "Spin-off: Boruto" in text
    # The manga adaptation must not appear as an anime season.
    assert "Adaptation" not in text.split("Seasons")[1].split("Related")[0]


def test_jikan_fallback_when_anilist_fails(make_ctx):
    http = FakeHttp()
    http.route("graphql.anilist.co", FakeResponse(raise_exc=HttpStatusError(500, "graphql.anilist.co")))
    http.route("api.jikan.moe", FakeResponse(json_data=JIKAN_OK))
    http.route("cdn.mal", FakeResponse(content=b"jpg", content_type="image/jpeg"))
    result = dispatch(make_ctx(http), "/Anime Cowboy Bebop")
    text = result.text
    assert "Cowboy Bebop" in text
    assert "8.75/10" in text
    assert "Jikan" in text  # honest provider note
    assert "myanimelist.net" in text
    assert "Prequel" in text  # relation preserved


def test_no_results_honest_message(make_ctx):
    http = FakeHttp()
    http.route("graphql.anilist.co", FakeResponse(json_data={"data": {"Page": {"media": []}}}))
    http.route("api.jikan.moe", FakeResponse(json_data={"data": []}))
    result = dispatch(make_ctx(http), "/Anime Zzzznotarealshow")
    assert "no anime matched" in result.text.lower() or "no anime" in result.text.lower()


def test_ambiguity_lists_alternatives(make_ctx):
    payload = {
        "data": {
            "Page": {
                "media": [
                    {
                        "id": 1,
                        "title": {"romaji": "Fate/Zero", "english": "Fate/Zero"},
                        "description": "Fourth Holy Grail War.",
                        "coverImage": {"large": "x"},
                        "genres": ["Action"],
                        "status": "FINISHED",
                        "episodes": 13,
                        "format": "TV",
                        "siteUrl": "https://anilist.co/anime/1",
                        "characters": {"edges": []},
                        "relations": {"edges": []},
                    },
                    {
                        "id": 2,
                        "title": {"romaji": "Fate/stay night", "english": "Fate/stay night"},
                        "description": "Fifth Holy Grail War.",
                        "coverImage": {"large": "y"},
                        "genres": ["Action"],
                        "status": "FINISHED",
                        "episodes": 24,
                        "format": "TV",
                        "siteUrl": "https://anilist.co/anime/2",
                        "characters": {"edges": []},
                        "relations": {"edges": []},
                    },
                ]
            }
        }
    }
    http = FakeHttp()
    http.route("graphql.anilist.co", FakeResponse(json_data=payload))
    result = dispatch(make_ctx(http), "/Anime Fate")
    assert "Fate/Zero" in result.text
    assert "Other matches:" in result.text
    assert "Fate/stay night" in result.text


def test_no_episode_lists_invented(make_ctx):
    result = dispatch(make_ctx(_anilist_http()), "/Anime Naruto")
    # Only the provider's episode *count* appears — never a fabricated list.
    assert "Episode 1" not in result.text
    assert "watch order" not in result.text.lower()


def test_missing_argument(make_ctx):
    result = dispatch(make_ctx(), "/Anime")
    assert "Usage" in result.text
