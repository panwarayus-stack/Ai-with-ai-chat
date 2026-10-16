"""/Wiki and /Celeb: real Wikipedia payloads, attribution, no invention."""

from __future__ import annotations

from src.commands.base import dispatch
from src.lib.http import HttpStatusError
from tests.conftest import (
    COMMONS_META_OK,
    WIKI_SEARCH_OK,
    WIKI_SUMMARY_PERSON,
    WIKI_SUMMARY_TOPIC,
    FakeHttp,
    FakeResponse,
)

PNG = b"\x89PNG\r\n\x1a\n fake"


def _http_topic():
    http = FakeHttp()
    http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data={
        "query": {"search": [{"title": "Taj Mahal", "snippet": "mausoleum in Agra"}]}
    }))
    http.route("en.wikipedia.org/api/rest_v1/page/summary/Taj_Mahal", FakeResponse(json_data=WIKI_SUMMARY_TOPIC))
    http.route("commons.wikimedia.org", FakeResponse(json_data=COMMONS_META_OK))
    http.route("upload.wikimedia.org", FakeResponse(content=PNG, content_type="image/png"))
    return http


def _http_person():
    http = FakeHttp()
    http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data=WIKI_SEARCH_OK))
    http.route("en.wikipedia.org/api/rest_v1/page/summary/Marie_Curie", FakeResponse(json_data=WIKI_SUMMARY_PERSON))
    http.route("commons.wikimedia.org", FakeResponse(json_data=COMMONS_META_OK))
    http.route("upload.wikimedia.org", FakeResponse(content=PNG, content_type="image/png"))
    return http


def test_wiki_returns_real_matching_source(make_ctx):
    http = _http_topic()
    result = dispatch(make_ctx(http), "/Wiki Taj Mahal")
    text = result.text
    assert "Taj Mahal" in text
    assert "ivory-white marble mausoleum" in text  # from the payload's extract
    assert "https://en.wikipedia.org/wiki/Taj_Mahal" in text  # real link from payload
    assert result.image == PNG  # lead image sent


def test_wiki_lead_image_has_attribution(make_ctx):
    result = dispatch(make_ctx(_http_topic()), "/Wiki Taj Mahal")
    assert "Wikimedia Commons" in result.text
    assert "Public domain" in result.text
    assert "commons.wikimedia.org" in result.text


def test_wiki_no_result_says_so(make_ctx):
    http = FakeHttp()
    http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data={"query": {"search": []}}))
    result = dispatch(make_ctx(http), "/Wiki Zzzxqc")
    assert "no wikipedia article found" in result.text.lower()


def test_wiki_disambiguation_handled(make_ctx):
    http = FakeHttp()
    http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data={
        "query": {"search": [{"title": "Mercury (planet)"}]}
    }))
    http.route(
        "en.wikipedia.org/api/rest_v1/page/summary/",
        FakeResponse(json_data={
            "title": "Mercury (planet)",
            "extract": "Mercury may refer to several topics.",
            "type": "disambiguation",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Mercury"}},
        }),
    )
    result = dispatch(make_ctx(http), "/Wiki Mercury")
    assert "several wikipedia topics" in result.text.lower() or "disambiguation" in result.text.lower()


def test_celeb_finds_biography_and_image(make_ctx):
    http = _http_person()
    result = dispatch(make_ctx(http), "/Celeb Marie Curie")
    text = result.text
    assert "Marie Curie" in text
    assert "physicist and chemist" in text  # verbatim from the article
    assert "https://en.wikipedia.org/wiki/Marie_Curie" in text
    assert result.image == PNG
    assert "not verified" not in text  # person cues matched -> clean result


def test_celeb_never_invents_identity(make_ctx):
    """When the extract has no person cues, the reply must not claim it's a person."""
    http = FakeHttp()
    http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data={
        "query": {"search": [{"title": "Some Article"}]}
    }))
    http.route("en.wikipedia.org/api/rest_v1/page/summary/", FakeResponse(json_data={
        "title": "Some Article",
        "description": "A list of things",
        "extract": "This article lists things that exist in no particular order.",
        "type": "standard",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Some_Article"}},
    }))
    result = dispatch(make_ctx(http), "/Celeb Some Name")
    assert "not verified as a biography" in result.text
    assert "Some Article" in result.text


def test_celeb_no_result_says_so(make_ctx):
    http = FakeHttp()
    http.route("en.wikipedia.org/w/api.php", FakeResponse(json_data={"query": {"search": []}}))
    result = dispatch(make_ctx(http), "/Celeb Zzz Nobody")
    assert "no wikipedia result" in result.text.lower()


def test_api_failure_reported(make_ctx):
    http = FakeHttp()
    http.route("en.wikipedia.org", FakeResponse(raise_exc=HttpStatusError(503, "en.wikipedia.org")))
    result = dispatch(make_ctx(http), "/Wiki Taj Mahal")
    assert "wikipedia" in result.text.lower()


def test_user_agent_sent(make_ctx):
    http = _http_topic()
    ctx = make_ctx(http)
    ctx.services.wikipedia.headers["User-Agent"] = "InstagramBot/1.0 (test)"
    dispatch(ctx, "/Wiki Taj Mahal")
    api_calls = [c for c in http.calls if "wikipedia.org/w/api.php" in c.url]
    assert api_calls and api_calls[0].headers.get("User-Agent")
