"""Wikipedia + Wikimedia Commons client shared by /Wiki and /Celeb.

Docs:
* https://www.mediawiki.org/wiki/API:Main_page (MediaWiki action API)
* https://en.wikipedia.org/api/rest_v1/ (Page summary REST)
* https://commons.wikimedia.org/ (image metadata & attribution)

One :class:`WikipediaService` instance backs both commands:

* ``search`` / ``summary`` resolve articles (including disambiguation),
* ``lead_image`` resolves the article's lead image,
* ``commons_attribution`` looks up artist/license info on Wikimedia Commons,
* ``find_person`` is the /Celeb resolution strategy. It never invents an
  identity: it returns only what the article says, or reports that no
  reliable biography was found.

A descriptive ``User-Agent`` is required by the Wikimedia policy and is sent
with every request.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote, unquote, urlparse

from src.lib.http import HttpClient, HttpError, HttpStatusError

logger = logging.getLogger(__name__)

ACTION_API = "https://{lang}.wikipedia.org/w/api.php"
SUMMARY_API = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Cues that a Wikipedia extract is about a person (occupation phrases).
_PERSON_CUES = re.compile(
    r"\b(is|was)\s+(an?\s+|the\s+)?[a-z\- ]{0,40}"
    r"(actor|actress|singer|songwriter|musician|rapper|dancer|composer|"
    r"player|athlete|cricketer|footballer|boxer|wrestler|coach|"
    r"scientist|engineer|inventor|mathematician|physicist|chemist|"
    r"writer|author|novelist|poet|journalist|columnist|editor|"
    r"artist|painter|sculptor|photographer|illustrator|"
    r"director|producer|filmmaker|screenwriter|comedian|"
    r"politician|president|prime minister|governor|senator|minister|"
    r"monarch|king|queen|prince|princess|emperor|empress|leader|"
    r"activist|reformer|revolutionary|soldier|army officer|admiral|general|"
    r"founder|entrepreneur|businessperson|businessman|businesswoman|"
    r"model|designer|architect|chef|doctor|physician|surgeon|nurse|"
    r"lawyer|judge|diplomat|spy|explorer|pilot|astronaut|cosmonaut|"
    r"voice actor|youtuber|streamer|influencer|television presenter|host)\b",
    re.IGNORECASE,
)


class WikipediaError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class SearchResult:
    title: str
    snippet: str = ""


@dataclass
class ImageAttribution:
    url: str = ""
    description_url: str = ""
    artist: str = ""
    license: str = ""


@dataclass
class Article:
    title: str
    display_title: str = ""
    description: str = ""
    extract: str = ""
    url: str = ""
    image_url: str = ""
    image: ImageAttribution = field(default_factory=ImageAttribution)
    is_disambiguation: bool = False
    pageid: Optional[int] = None

    def is_about_person(self) -> bool:
        probe = f"{self.description} {self.extract[:400]}"
        return bool(_PERSON_CUES.search(probe))


@dataclass
class PersonLookup:
    article: Optional[Article]
    candidates: List[str] = field(default_factory=list)
    note: str = ""


class WikipediaService:
    def __init__(
        self,
        language: str = "en",
        user_agent: str = "InstagramBot/1.0",
        http: Optional[HttpClient] = None,
    ):
        self.language = language or "en"
        self.headers = {"User-Agent": user_agent}
        self.http = http or HttpClient(retries=1, user_agent=user_agent)

    # ------------------------------------------------------------------ #
    # Search + summary
    # ------------------------------------------------------------------ #
    def search(self, query: str, limit: int = 5) -> List[SearchResult]:
        try:
            data = self.http.get_json(
                ACTION_API.format(lang=self.language),
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": limit,
                    "srprop": "snippet",
                    "format": "json",
                    "utf8": 1,
                },
                headers=self.headers,
            )
        except HttpStatusError as exc:
            raise WikipediaError(f"Wikipedia search returned HTTP {exc.status}") from exc
        except HttpError as exc:
            raise WikipediaError(f"Wikipedia is unreachable ({exc})") from exc

        hits = ((data or {}).get("query") or {}).get("search") or []
        return [
            SearchResult(
                title=str(hit.get("title", "")),
                snippet=_strip_tags(hit.get("snippet", "")),
            )
            for hit in hits
            if hit.get("title")
        ]

    def summary(self, title: str) -> Optional[Article]:
        """Fetch the REST summary for *title*; None when the page is missing."""
        url = SUMMARY_API.format(lang=self.language, title=quote(title.replace(" ", "_"), safe=""))
        try:
            data = self.http.get_json(url, headers=self.headers)
        except HttpStatusError as exc:
            if exc.status == 404:
                return None
            raise WikipediaError(f"Wikipedia summary returned HTTP {exc.status}") from exc
        except HttpError as exc:
            raise WikipediaError(f"Wikipedia is unreachable ({exc})") from exc

        article = Article(
            title=data.get("title") or title,
            display_title=data.get("displaytitle") or data.get("title") or title,
            description=data.get("description") or "",
            extract=data.get("extract") or "",
            url=(data.get("content_urls") or {}).get("desktop", {}).get("page", "")
            or f"https://{self.language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            is_disambiguation=data.get("type") == "disambiguation",
            pageid=data.get("pageid"),
        )
        thumb = data.get("originalimage") or data.get("thumbnail") or {}
        article.image_url = thumb.get("source") or ""
        if article.image_url:
            article.image = self.commons_attribution(article.image_url)
        return article

    # ------------------------------------------------------------------ #
    # Image attribution (Wikimedia Commons)
    # ------------------------------------------------------------------ #
    def commons_attribution(self, image_url: str) -> ImageAttribution:
        attribution = ImageAttribution(url=image_url, description_url=image_url)
        file_title = _commons_file_title(image_url)
        if not file_title:
            return attribution
        try:
            data = self.http.get_json(
                COMMONS_API,
                params={
                    "action": "query",
                    "titles": file_title,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata",
                    "format": "json",
                },
                headers=self.headers,
            )
        except HttpError as exc:
            logger.debug("Commons attribution lookup failed: %s", exc)
            return attribution

        pages = ((data or {}).get("query") or {}).get("pages") or {}
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            attribution.description_url = info.get("descriptionurl") or attribution.description_url
            meta = info.get("extmetadata") or {}
            attribution.artist = _strip_tags(meta.get("Artist", {}).get("value", ""))
            attribution.license = meta.get("LicenseShortName", {}).get("value", "")
        return attribution

    # ------------------------------------------------------------------ #
    # /Wiki
    # ------------------------------------------------------------------ #
    def resolve(self, query: str) -> Optional[Article]:
        """Best article for *query* (exact search hit first)."""
        hits = self.search(query, limit=5)
        if not hits:
            return None
        for hit in hits:
            if hit.title.casefold() == query.strip().casefold():
                return self.summary(hit.title)
        return self.summary(hits[0].title)

    # ------------------------------------------------------------------ #
    # /Celeb
    # ------------------------------------------------------------------ #
    def find_person(self, name: str) -> PersonLookup:
        """Resolve a notable person without inventing anything.

        Strategy: search, then inspect up to 3 candidate summaries. Prefer a
        standard article whose extract matches person/occupation cues.
        Disambiguation pages surface their options. If nothing verifies, the
        lookup reports that honestly.
        """
        hits = self.search(name, limit=5)
        if not hits:
            return PersonLookup(
                article=None,
                note=f"No Wikipedia result found for '{name}'. "
                "Check the spelling or try the full professional name.",
            )

        disambiguation_options: List[str] = []
        fallback: Optional[Article] = None
        for hit in hits[:3]:
            article = self.summary(hit.title)
            if article is None:
                continue
            if article.is_disambiguation:
                disambiguation_options.extend(
                    s.title for s in hits if s.title != article.title
                )
                continue
            if article.is_about_person():
                return PersonLookup(article=article)
            if fallback is None:
                fallback = article

        if disambiguation_options and fallback is None:
            return PersonLookup(
                article=None,
                candidates=disambiguation_options[:5],
                note=f"'{name}' is ambiguous on Wikipedia. Try one of these:",
            )

        if fallback is not None:
            # Return the top match but never claim it is a verified person.
            return PersonLookup(
                article=fallback,
                note=(
                    "Top Wikipedia match (not verified as a biography — "
                    "showing the article as-is):"
                ),
            )

        return PersonLookup(
            article=None,
            candidates=[h.title for h in hits[:5]],
            note=f"No reliable biography found for '{name}'. Closest Wikipedia matches:",
        )


# ---------------------------------------------------------------------- #
def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _commons_file_title(image_url: str) -> Optional[str]:
    """Derive 'File:Foo.jpg' from an upload.wikimedia.org URL."""
    try:
        path = unquote(urlparse(image_url).path)
    except Exception:  # pragma: no cover - defensive
        return None
    if "/commons/" not in path:
        return None
    name = path.rsplit("/", 1)[-1]
    if not name:
        return None
    return f"File:{name}"
