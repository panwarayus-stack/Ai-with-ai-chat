"""Shared test fixtures: fake HTTP layer, canned provider payloads, app wiring.

No test performs real network calls: every service receives a FakeHttp or a
stub, so the suite runs offline and deterministically.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

import pytest

from config import Config
from src.commands.base import CommandContext, Services
from src.lib.ai_queue import AIQueue
from src.services.anime import AnimeService
from src.services.demographics import DemographicsService
from src.services.images import ImageService
from src.services.openrouter import ConversationStore, OpenRouterClient
from src.services.weather import WeatherService
from src.services.wikipedia import WikipediaService


# ---------------------------------------------------------------------- #
# Fake HTTP
# ---------------------------------------------------------------------- #
class HttpCall:
    def __init__(self, method: str, url: str, params=None, body=None, headers=None):
        self.method = method
        self.url = url
        self.params = params
        self.body = body
        self.headers = headers


class FakeResponse:
    def __init__(self, json_data=None, content=b"", content_type="application/json", status=200, raise_exc=None):
        self.json_data = json_data
        self.content = content
        self.content_type = content_type
        self.status = status
        self.raise_exc = raise_exc


class FakeHttp:
    """URL-substring routed fake of src.lib.http.HttpClient."""

    def __init__(self):
        self.calls: List[HttpCall] = []
        self._routes: List[Tuple[str, Callable[[HttpCall], FakeResponse]]] = []

    def route(self, url_substring: str, response: FakeResponse) -> None:
        self._routes.append((url_substring, lambda call: response))

    def route_fn(self, url_substring: str, fn: Callable[[HttpCall], FakeResponse]) -> None:
        self._routes.append((url_substring, fn))

    def _dispatch(self, call: HttpCall) -> Any:
        self.calls.append(call)
        # Longest substring wins so "cdn.nekos.best" beats "nekos.best".
        best = None
        for substring, factory in self._routes:
            if substring in call.url and (best is None or len(substring) > len(best[0])):
                best = (substring, factory)
        if best is None:
            raise AssertionError(f"FakeHttp: no route for {call.method} {call.url}")
        response = best[1](call)
        if response.raise_exc is not None:
            raise response.raise_exc
        return response

    def get_json(self, url, *, params=None, headers=None):
        response = self._dispatch(HttpCall("GET", url, params=params, headers=headers))
        return response.json_data

    def post_json(self, url, payload, *, params=None, headers=None, timeout=None):
        response = self._dispatch(HttpCall("POST", url, params=params, body=payload, headers=headers))
        return response.json_data

    def post_graphql(self, url, data, *, headers=None):
        response = self._dispatch(HttpCall("POST", url, body=data, headers=headers))
        return response.json_data

    def get_bytes(self, url, *, params=None, headers=None):
        response = self._dispatch(HttpCall("GET", url, params=params, headers=headers))
        return response.content, response.content_type

    def urls(self) -> List[str]:
        return [c.url for c in self.calls]


class StubInstagram:
    def __init__(self):
        self.sent_texts: List[Tuple[int, str]] = []
        self.sent_photos: List[Tuple[int, bytes]] = []
        self.snapshot = {
            "username": "testbot",
            "full_name": "Test Bot",
            "biography": "A test bot",
            "follower_count": 10,
            "following_count": 5,
            "media_count": 2,
            "is_private": False,
            "is_verified": False,
            "pk": 12345,
        }

    def account_snapshot(self):
        return dict(self.snapshot)

    def send_text(self, thread_id, text):
        self.sent_texts.append((thread_id, text))

    def send_photo(self, thread_id, path, caption=""):
        self.sent_photos.append((thread_id, bytes(path.read_bytes())))


class StubSherlock:
    def __init__(self, matches=None, error=None):
        self.matches = matches or []
        self.error = error
        self.calls = []

    def lookup(self, username):
        self.calls.append(username)
        if self.error:
            raise self.error
        return self.matches


# ---------------------------------------------------------------------- #
# Canned provider payloads (shapes mirror the real APIs)
# ---------------------------------------------------------------------- #
GENDERIZE_OK = {"name": "kim", "gender": "female", "probability": 0.94, "count": 1098}
NATIONALIZE_OK = {
    "name": "kim",
    "country": [
        {"country_id": "US", "probability": 0.22},
        {"country_id": "GB", "probability": 0.19},
        {"country_id": "AU", "probability": 0.07},
    ],
}

ANILIST_OK = {
    "data": {
        "Page": {
            "media": [
                {
                    "id": 20,
                    "idMal": 20,
                    "title": {"romaji": "NARUTO", "english": "Naruto", "native": "NARUTO -ナルト-"},
                    "description": "Naruto Uzumaki is a young ninja who seeks recognition.",
                    "coverImage": {"large": "https://img.anilist.co/naruto.jpg"},
                    "genres": ["Action", "Adventure"],
                    "averageScore": 81,
                    "status": "FINISHED",
                    "episodes": 220,
                    "format": "TV",
                    "season": "FALL",
                    "seasonYear": 2002,
                    "startDate": {"year": 2002},
                    "siteUrl": "https://anilist.co/anime/20",
                    "characters": {
                        "edges": [
                            {"role": "MAIN", "node": {"name": {"full": "Naruto Uzumaki"}}},
                            {"role": "MAIN", "node": {"name": {"full": "Sasuke Uchiha"}}},
                        ]
                    },
                    "relations": {
                        "edges": [
                            {
                                "relationType": "SEQUEL",
                                "node": {
                                    "id": 269,
                                    "type": "ANIME",
                                    "format": "TV",
                                    "status": "FINISHED",
                                    "episodes": 500,
                                    "startDate": {"year": 2007},
                                    "title": {"romaji": "NARUTO SHIPPUDEN", "english": "Naruto Shippuden"},
                                },
                            },
                            {
                                "relationType": "SPIN_OFF",
                                "node": {
                                    "id": 34572,
                                    "type": "ANIME",
                                    "format": "ONA",
                                    "status": "FINISHED",
                                    "episodes": 4,
                                    "startDate": {"year": 2017},
                                    "title": {"romaji": "Boruto", "english": "Boruto: Naruto Next Generations"},
                                },
                            },
                            {
                                "relationType": "ADAPTATION",
                                "node": {"id": 30011, "type": "MANGA", "title": {"romaji": "Naruto"}},
                            },
                        ]
                    },
                }
            ]
        }
    }
}

JIKAN_OK = {
    "data": [
        {
            "mal_id": 1,
            "title": "Cowboy Bebop",
            "title_english": "Cowboy Bebop",
            "title_japanese": "カウボーイビバップ",
            "synopsis": "In the year 2071, humanity has colonized several of the planets.",
            "images": {"jpg": {"image_url": "https://cdn.mal/bebop.jpg", "large_image_url": "https://cdn.mal/bebop_large.jpg"}},
            "genres": [{"name": "Action"}],
            "type": "TV",
            "episodes": 26,
            "status": "Finished Airing",
            "score": 8.75,
            "season": "spring",
            "year": 1998,
            "aired": {"from": "1998-04-03T00:00:00+00:00"},
            "url": "https://myanimelist.net/anime/1/Cowboy_Bebop",
            "themes": [],
            "relations": [
                {"relation": "Prequel", "entry": [{"type": "anime", "name": "Cowboy Bebop: Tengoku no Tobira"}]}
            ],
        }
    ]
}

WAIFU_IM_OK = {
    "items": [
        {
            "id": 6758,
            "isNsfw": False,
            "url": "https://cdn.waifu.im/6758.png",
            "source": "https://www.pixiv.net/en/artworks/81300646",
            "artists": [{"name": "Neko-san"}],
            "tags": [{"name": "Raiden Shogun", "slug": "raiden-shogun", "description": "Genshin Impact"}],
        }
    ]
}

NEKOS_HUSBANDO_OK = {
    "results": [
        {
            "url": "https://cdn.nekos.best/husbando.png",
            "artist_name": "Artist",
            "artist_href": "https://example.com/artist",
            "source_url": "https://example.com/source",
            "anime_name": "Levi Ackerman (Attack on Titan)",
        }
    ]
}

WIKI_SEARCH_OK = {
    "query": {
        "search": [
            {"title": "Marie Curie", "snippet": "Polish-French <span>physicist</span>"},
            {"title": "Curie (crater)", "snippet": "crater on the Moon"},
        ]
    }
}

WIKI_SUMMARY_PERSON = {
    "title": "Marie Curie",
    "displaytitle": "Marie Curie",
    "description": "Polish-French physicist and chemist (1867–1934)",
    "extract": "Maria Salomea Skłodowska-Curie was a physicist and chemist who conducted pioneering research on radioactivity.",
    "type": "standard",
    "pageid": 712,
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Marie_Curie"}},
    "originalimage": {"source": "https://upload.wikimedia.org/wikipedia/commons/7/7e/Marie_Curie_c._1920s.jpg"},
}

WIKI_SUMMARY_TOPIC = {
    "title": "Taj Mahal",
    "displaytitle": "Taj Mahal",
    "description": "Ivory-white marble mausoleum in Agra, India",
    "extract": "The Taj Mahal is an ivory-white marble mausoleum on the right bank of the river Yamuna in Agra, India.",
    "type": "standard",
    "pageid": 534,
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Taj_Mahal"}},
    "thumbnail": {"source": "https://upload.wikimedia.org/wikipedia/commons/d/da/Taj-Mahal.jpg"},
}

COMMONS_META_OK = {
    "query": {
        "pages": {
            "-1": {
                "imageinfo": [
                    {
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Marie_Curie_c._1920s.jpg",
                        "extmetadata": {
                            "Artist": {"value": "<a href='//commons.wikimedia.org/wiki/User:X'>X</a>"},
                            "LicenseShortName": {"value": "Public domain"},
                        },
                    }
                ]
            }
        }
    }
}

OPENROUTER_OK = {
    "choices": [{"message": {"role": "assistant", "content": "Hello from the fake model."}}]
}


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Keep tests hermetic: never read the developer's local .env file."""
    import config as config_module

    if config_module.load_dotenv is not None:
        monkeypatch.setattr(config_module, "load_dotenv", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _commands_loaded():
    from src.commands import load_all

    load_all()


@pytest.fixture
def config() -> Config:
    return Config.from_env(
        environ={
            "IG_USERNAME": "testbot",
            "IG_PASSWORD": "secret",
            "OPENROUTER_API_KEY": "sk-or-test",
            "AI_REQUEST_TIMEOUT": "10",
            "LOG_LEVEL": "DEBUG",
            # Keep timing tests fast: no artificial delays or pacing.
            "IG_REPLY_DELAY_MIN": "0",
            "IG_REPLY_DELAY_MAX": "0",
            "IG_MIN_REQUEST_GAP": "0",
            "IG_MARK_SEEN": "false",
            # Never bind ports during tests.
            "HEALTH_PORT": "0",
        }
    )


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def stub_instagram() -> StubInstagram:
    return StubInstagram()


@pytest.fixture
def make_ctx(config, stub_instagram):
    """Factory building a CommandContext with real services over FakeHttp."""

    def _make(http: Optional[FakeHttp] = None, *, openrouter_key: Optional[str] = "sk-or-test",
              sherlock=None, ai_queue: Optional[AIQueue] = None) -> CommandContext:
        http = http or FakeHttp()
        services = Services(
            openrouter=OpenRouterClient(
                api_key=config.openrouter_api_key if openrouter_key is None else openrouter_key,
                model=config.openrouter_model,
                timeout=config.ai_request_timeout,
                http=http,
            ),
            ai_queue=ai_queue or AIQueue(max_size=5),
            conversations=ConversationStore(memory_turns=2),
            demographics=DemographicsService(http=http),
            weather=WeatherService(http=http),
            anime=AnimeService(http=http),
            images=ImageService(http=http),
            wikipedia=WikipediaService(http=http),
            instagram=stub_instagram,
            sherlock=sherlock or StubSherlock(),
        )
        return CommandContext(services=services, user_id=42, username="tester", config=config)

    return _make
