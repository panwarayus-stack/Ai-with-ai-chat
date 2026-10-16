"""Anime lookups for /Anime — AniList primary, Jikan fallback.

Docs:
* https://docs.anilist.co/ (GraphQL, keyless, ~90 req/min)
* https://docs.api.jikan.moe/ (REST v4, keyless, ~3 req/s / 60 req/min)

Results carry only data the providers actually returned. Episode lists and
watch orders are **never** invented; relations are classified from AniList's
``relationType`` field so real seasons (PREQUEL/SEQUEL) are clearly separated
from spin-offs, side stories, alternative versions and movies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from src.lib.http import HttpClient, HttpError
from src.lib.utils import ellipsize, titles_match

logger = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"
JIKAN_ANIME_URL = "https://api.jikan.moe/v4/anime"

ANILIST_QUERY = """
query ($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
      id
      idMal
      title { romaji english native }
      description(asHtml: false)
      coverImage { large }
      genres
      averageScore
      meanScore
      status
      episodes
      format
      season
      seasonYear
      startDate { year }
      siteUrl
      characters(perPage: 8, sort: [ROLE, RELEVANCE, ID]) {
        edges { role node { name { full } } }
      }
      relations {
        edges {
          relationType
          node {
            id
            type
            format
            status
            episodes
            startDate { year }
            title { romaji english }
          }
        }
      }
    }
  }
}
"""

# relationType -> (bucket label, human label)
_RELATION_BUCKETS = {
    "PREQUEL": ("seasons", "Prequel"),
    "SEQUEL": ("seasons", "Sequel"),
    "SPIN_OFF": ("related", "Spin-off"),
    "SIDE_STORY": ("related", "Side story"),
    "ALTERNATIVE": ("related", "Alternative version"),
    "PARENT": ("related", "Parent story"),
    "SOURCE": ("related", "Source material"),
    "ADAPTATION": ("related", "Adaptation"),
    "CHARACTER": ("related", "Character spin-off"),
    "SUMMARY": ("related", "Summary"),
    "OTHER": ("related", "Other"),
}

_RELATION_TYPE_LABELS = {
    "PREQUEL": "Prequel",
    "SEQUEL": "Sequel",
    "SPIN_OFF": "Spin-off",
    "SIDE_STORY": "Side story",
    "ALTERNATIVE": "Alternative version",
    "PARENT": "Parent story",
    "SOURCE": "Source material",
    "ADAPTATION": "Adaptation",
    "CHARACTER": "Character spin-off",
    "SUMMARY": "Summary",
    "OTHER": "Other",
}


class AnimeError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class RelatedWork:
    title: str
    relation: str  # human label
    bucket: str  # "seasons" | "related"
    year: Optional[int] = None
    format: str = ""
    episodes: Optional[int] = None

    def render(self) -> str:
        bits = [f"{self.relation}: {self.title}"]
        meta = []
        if self.format:
            meta.append(self.format.replace("_", " ").title())
        if self.year:
            meta.append(str(self.year))
        if self.episodes:
            meta.append(f"{self.episodes} ep")
        if meta:
            bits.append(f"({', '.join(meta)})")
        return " ".join(bits)


@dataclass
class AnimeResult:
    title: str
    title_native: str = ""
    title_english: str = ""
    synopsis: str = ""
    poster_url: str = ""
    genres: List[str] = field(default_factory=list)
    score: str = ""
    status: str = ""
    format: str = ""
    episodes: Optional[int] = None
    season: str = ""
    season_year: Optional[int] = None
    characters: List[str] = field(default_factory=list)
    seasons: List[RelatedWork] = field(default_factory=list)
    related: List[RelatedWork] = field(default_factory=list)
    source_url: str = ""
    provider: str = ""  # "anilist" | "jikan"

    @property
    def display_title(self) -> str:
        return self.title_english or self.title


@dataclass
class AnimeSearchOutcome:
    result: Optional[AnimeResult]
    candidates: List[str] = field(default_factory=list)  # ambiguity suggestions
    provider_note: str = ""


class AnimeService:
    def __init__(
        self,
        anilist_enabled: bool = True,
        jikan_enabled: bool = True,
        http: Optional[HttpClient] = None,
    ):
        self.anilist_enabled = anilist_enabled
        self.jikan_enabled = jikan_enabled
        self.http = http or HttpClient(retries=1)

    # ------------------------------------------------------------------ #
    def search(self, query: str) -> AnimeSearchOutcome:
        """Resolve *query* to one anime, preferring AniList."""
        errors: List[str] = []
        outcome: Optional[AnimeSearchOutcome] = None

        if self.anilist_enabled:
            try:
                outcome = self._search_anilist(query)
            except AnimeError as exc:
                errors.append(f"AniList: {exc}")
            except HttpError as exc:
                errors.append(f"AniList: {exc}")

        if outcome is None and self.jikan_enabled:
            try:
                outcome = self._search_jikan(query)
                if outcome is not None:
                    outcome.provider_note = (
                        "Source: Jikan (MyAnimeList mirror) — AniList was unavailable."
                    )
            except AnimeError as exc:
                errors.append(f"Jikan: {exc}")
            except HttpError as exc:
                errors.append(f"Jikan: {exc}")

        if outcome is not None:
            return outcome
        if errors:
            raise AnimeError("Anime lookup failed — " + "; ".join(errors))
        raise AnimeError("No anime data source is enabled (ANILIST_ENABLED / JIKAN_ENABLED).")

    # ------------------------------------------------------------------ #
    # AniList
    # ------------------------------------------------------------------ #
    def _search_anilist(self, query: str) -> Optional[AnimeSearchOutcome]:
        data = self.http.post_graphql(
            ANILIST_URL,
            {"query": ANILIST_QUERY, "variables": {"search": query, "perPage": 5}},
        )
        if isinstance(data, dict) and data.get("errors"):
            message = data["errors"][0].get("message", "unknown error")
            raise AnimeError(f"GraphQL error: {message}")

        media_list = (
            ((data or {}).get("data") or {}).get("Page", {}).get("media") or []
        )
        if not media_list:
            return AnimeSearchOutcome(
                result=None,
                candidates=[],
                provider_note=f"No anime matched {query!r} on AniList.",
            )

        chosen, candidates = self._pick_media(media_list, query)
        result = self._from_anilist(chosen)
        return AnimeSearchOutcome(result=result, candidates=candidates)

    @staticmethod
    def _pick_media(media_list: List[dict], query: str):
        """Prefer an exact title match; otherwise best search match + hints."""
        titles = []
        for media in media_list:
            t = media.get("title") or {}
            titles.append(t.get("english") or t.get("romaji") or t.get("native") or "?")

        for media, title in zip(media_list, titles):
            t = media.get("title") or {}
            for variant in (t.get("english"), t.get("romaji"), t.get("native")):
                if variant and titles_match(variant, query):
                    others = [x for x in titles if x != title]
                    return media, others[:3]

        chosen = media_list[0]
        chosen_title = titles[0] if titles else "?"
        others = [x for x in titles[1:4] if x != chosen_title]
        return chosen, others

    @classmethod
    def _from_anilist(cls, media: dict) -> AnimeResult:
        title = media.get("title") or {}
        result = AnimeResult(
            title=title.get("romaji") or title.get("english") or title.get("native") or "Unknown",
            title_native=title.get("native") or "",
            title_english=title.get("english") or "",
            synopsis=(media.get("description") or "")[:1200],
            poster_url=(media.get("coverImage") or {}).get("large") or "",
            genres=list(media.get("genres") or []),
            status=_pretty_status(media.get("status")),
            format=(media.get("format") or "").replace("_", " ").title(),
            episodes=media.get("episodes"),
            season=(media.get("season") or "").title(),
            season_year=media.get("seasonYear") or (media.get("startDate") or {}).get("year"),
            source_url=media.get("siteUrl") or f"https://anilist.co/anime/{media.get('id', '')}",
            provider="anilist",
        )
        score = media.get("averageScore")
        if score is not None:
            result.score = f"{score}/100 (AniList)"

        for edge in (media.get("characters") or {}).get("edges") or []:
            name = ((edge.get("node") or {}).get("name") or {}).get("full")
            if name and name not in result.characters:
                result.characters.append(name)

        for edge in (media.get("relations") or {}).get("edges") or []:
            node = edge.get("node") or {}
            if node.get("type") != "ANIME":
                continue
            rel = (edge.get("relationType") or "OTHER").upper()
            bucket, label = _RELATION_BUCKETS.get(rel, ("related", _RELATION_TYPE_LABELS.get(rel, rel)))
            t = node.get("title") or {}
            work = RelatedWork(
                title=t.get("english") or t.get("romaji") or t.get("native") or "Unknown",
                relation=label,
                bucket=bucket,
                year=(node.get("startDate") or {}).get("year"),
                format=(node.get("format") or "").replace("_", " ").title(),
                episodes=node.get("episodes"),
            )
            target = result.seasons if bucket == "seasons" else result.related
            if all(w.title != work.title for w in target):
                target.append(work)
        return result

    # ------------------------------------------------------------------ #
    # Jikan (fallback)
    # ------------------------------------------------------------------ #
    def _search_jikan(self, query: str) -> Optional[AnimeSearchOutcome]:
        data = self.http.get_json(JIKAN_ANIME_URL, params={"q": query, "limit": 5})
        items = (data or {}).get("data") or []
        if not items:
            return AnimeSearchOutcome(
                result=None,
                provider_note=f"No anime matched {query!r} on Jikan.",
            )

        chosen = items[0]
        for item in items:
            titles = [item.get("title"), item.get("title_english"), item.get("title_japanese")]
            if any(t and titles_match(t, query) for t in titles):
                chosen = item
                break
        candidates = []
        for item in items:
            title = item.get("title_english") or item.get("title") or "?"
            if title != (chosen.get("title_english") or chosen.get("title")):
                candidates.append(title)
        return AnimeSearchOutcome(result=self._from_jikan(chosen), candidates=candidates[:3])

    @classmethod
    def _from_jikan(cls, item: dict) -> AnimeResult:
        aired = item.get("aired") or {}
        year = None
        from_date = aired.get("from") or ""
        if len(from_date) >= 4 and from_date[:4].isdigit():
            year = int(from_date[:4])

        result = AnimeResult(
            title=item.get("title") or "Unknown",
            title_native=item.get("title_japanese") or "",
            title_english=item.get("title_english") or "",
            synopsis=(item.get("synopsis") or "")[:1200],
            poster_url=(item.get("images") or {}).get("jpg", {}).get("large_image_url")
            or (item.get("images") or {}).get("jpg", {}).get("image_url", ""),
            genres=[g.get("name") for g in (item.get("genres") or []) if g.get("name")],
            status=_pretty_status(item.get("status")),
            format=(item.get("type") or "").replace("_", " ").title(),
            episodes=item.get("episodes"),
            season=(item.get("season") or "").title(),
            season_year=item.get("year") or year,
            source_url=item.get("url") or "",
            provider="jikan",
        )
        score = item.get("score")
        if score is not None:
            result.score = f"{score}/10 (MyAnimeList)"

        for entry in (item.get("themes") or []):
            if entry.get("name"):
                result.genres.append(entry["name"])

        # Jikan's relations endpoint is separate; classify by relation field.
        for relation in item.get("relations") or []:
            rel_name = (relation.get("relation") or "Other").replace("_", " ").title()
            for entry in relation.get("entry") or []:
                if (entry.get("type") or "").lower() != "anime":
                    continue
                bucket = "seasons" if rel_name in ("Prequel", "Sequel") else "related"
                work = RelatedWork(
                    title=entry.get("name") or "Unknown",
                    relation=rel_name,
                    bucket=bucket,
                )
                target = result.seasons if bucket == "seasons" else result.related
                if all(w.title != work.title for w in target):
                    target.append(work)
        return result


def _pretty_status(status: Optional[str]) -> str:
    if not status:
        return ""
    text = str(status).replace("_", " ").title()
    # AniList uses RELEASING / FINISHED / NOT_YET_RELEASED / CANCELLED / HIATUS
    mapping = {
        "Releasing": "Currently airing",
        "Finished": "Finished",
        "Not Yet Released": "Upcoming",
        "Cancelled": "Cancelled",
        "Hiatus": "On hiatus",
    }
    return mapping.get(text, text)


def render_result(result: AnimeResult, synopsis_limit: int = 420) -> str:
    """Format an AnimeResult as plain-text DM content."""
    lines: List[str] = [result.display_title]
    alt = [t for t in (result.title, result.title_native) if t and t != result.display_title]
    if alt:
        lines.append("Also known as: " + " / ".join(dict.fromkeys(alt)))

    meta = []
    if result.format:
        meta.append(result.format)
    if result.season and result.season_year:
        meta.append(f"{result.season} {result.season_year}")
    elif result.season_year:
        meta.append(str(result.season_year))
    if result.status:
        meta.append(result.status)
    if result.episodes:
        meta.append(f"{result.episodes} episodes")
    if meta:
        lines.append(" • ".join(meta))

    if result.score:
        lines.append(f"Score: {result.score}")
    if result.genres:
        lines.append("Genres: " + ", ".join(result.genres[:8]))
    if result.synopsis:
        lines.append("")
        lines.append(ellipsize(result.synopsis.replace("\n", " "), synopsis_limit))

    if result.characters:
        lines.append("")
        lines.append("Main characters: " + ", ".join(result.characters[:6]))
    if result.seasons:
        lines.append("")
        lines.append("Seasons / direct order:")
        for work in result.seasons[:6]:
            lines.append(f"  – {work.render()}")
    if result.related:
        lines.append("")
        lines.append("Related works (not seasons):")
        for work in result.related[:6]:
            lines.append(f"  – {work.render()}")
    if result.source_url:
        lines.append("")
        lines.append(f"Source: {result.source_url}")
    return "\n".join(lines)
