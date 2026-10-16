"""/Pic: content policy, male/female sources, verifiable names only."""

from __future__ import annotations

from src.commands.base import dispatch
from src.lib.http import HttpStatusError
from tests.conftest import NEKOS_HUSBANDO_OK, WAIFU_IM_OK, FakeHttp, FakeResponse

PNG = b"\x89PNG\r\n\x1a\n fake"


def _http():
    http = FakeHttp()
    http.route("api.waifu.im", FakeResponse(json_data=WAIFU_IM_OK))
    http.route("nekos.best", FakeResponse(json_data=NEKOS_HUSBANDO_OK))
    http.route("cdn.waifu.im", FakeResponse(content=PNG, content_type="image/png"))
    http.route("cdn.nekos.best", FakeResponse(content=PNG, content_type="image/png"))
    return http


def test_female_pic_uses_waifu_im_and_sends_image(make_ctx):
    http = _http()
    result = dispatch(make_ctx(http), "/Pic female")
    assert result.image == PNG
    assert "waifu.im" in result.text or "pixiv" in result.text  # source URL
    waifu_calls = [c for c in http.calls if "api.waifu.im" in c.url]
    assert waifu_calls, "waifu.im must be queried for female art"
    assert not any("nekos.best" in c.url for c in http.calls)


def test_male_pic_uses_nekos_best(make_ctx):
    http = _http()
    result = dispatch(make_ctx(http), "/Pic male")
    assert result.image == PNG
    assert any("nekos.best" in u for u in http.urls())
    assert not any("api.waifu.im" in c.url for c in http.calls)


def test_nsfw_excluded_at_api_level(make_ctx):
    http = _http()
    dispatch(make_ctx(http), "/Pic female")
    call = [c for c in http.calls if "api.waifu.im" in c.url][0]
    pairs = call.params  # list of tuples for multi-value tag parameters
    assert ("IsNsfw", "False") in pairs, "explicit content must be filtered by the API"
    excluded = [v for (k, v) in pairs if k == "ExcludedTags"]
    assert "hentai" in excluded


def test_nsfw_flagged_image_refused(make_ctx):
    bad = dict(WAIFU_IM_OK)
    bad = {"items": [dict(WAIFU_IM_OK["items"][0], isNsfw=True)]}
    http = _http()
    http._routes = [(s, f) for (s, f) in http._routes if "api.waifu.im" not in s]
    http.route("api.waifu.im", FakeResponse(json_data=bad))
    result = dispatch(make_ctx(http), "/Pic female")
    assert result.image is None
    assert "refus" in result.text.lower() or "nsfw" in result.text.lower()


def test_character_names_only_when_verified(make_ctx):
    # waifu.im payload has tag 'raiden-shogun' -> verified character name shown
    result = dispatch(make_ctx(_http()), "/Pic female")
    assert "Raiden Shogun" in result.text

    # nekos.best payload has anime_name -> shown
    result = dispatch(make_ctx(_http()), "/Pic male")
    assert "Levi Ackerman" in result.text
    assert "Attack on Titan" in result.text


def test_waifu_im_fallback_to_nekos_best(make_ctx):
    http = _http()
    http._routes = [(s, f) for (s, f) in http._routes if "api.waifu.im" not in s]
    http.route("api.waifu.im", FakeResponse(raise_exc=HttpStatusError(503, "api.waifu.im")))
    result = dispatch(make_ctx(http), "/Pic female")
    assert result.image == PNG
    assert "fallback" in result.text.lower()


def test_all_providers_down_honest_error(make_ctx):
    http = FakeHttp()
    http.route("api.waifu.im", FakeResponse(raise_exc=HttpStatusError(500, "x")))
    http.route("nekos.best", FakeResponse(raise_exc=HttpStatusError(500, "x")))
    result = dispatch(make_ctx(http), "/Pic male")
    assert result.image is None
    assert "could not fetch" in result.text.lower()


def test_random_is_one_or_the_other(make_ctx):
    http = _http()
    result = dispatch(make_ctx(http), "/Pic")
    assert result.image == PNG
