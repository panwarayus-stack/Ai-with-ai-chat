"""Random anime artwork for /Pic.

Primary provider: **waifu.im** (docs: https://docs.waifu.im/).

Important API note (verified against the current docs, October 2026): the
old ``GET /search`` endpoint has been replaced by ``GET /images`` with
``IncludedTags`` / ``ExcludedTags`` / ``IsNsfw`` (``False`` | ``True`` |
``All``) filters and an ``items`` response envelope. This client targets the
current API.

Content policy enforced by this module:

* only ``IsNsfw=False`` is ever requested — explicit sexual/adult images are
  excluded at the API level, not filtered after the fact;
* mildly suggestive (but SFW) artwork comes only from the curated
  ``SUGGESTIVE_TAGS`` list (e.g. ``oppai``) and is mixed in occasionally;
* explicit tags are listed in ``EXCLUDED_TAGS`` as a second layer.

Male artwork: waifu.im's archive is female-oriented and offers no male
category. For male requests we use **nekos.best** (docs:
https://docs.nekos.best/) — a documented, keyless alternative with a
``husbando`` category — which is also the fallback when waifu.im fails.

Character / anime names are displayed **only** when the provider actually
returns them (verified character tags on waifu.im; ``anime_name`` from
nekos.best). Nothing is guessed.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import List, Optional

from src.lib.http import HttpClient, HttpError, HttpStatusError

logger = logging.getLogger(__name__)

WAIFU_IM_URL = "https://api.waifu.im/images"
NEKOS_BEST_URL = "https://nekos.best/api/v2/{category}"

# Normal (non-suggestive) waifu.im tags.
SAFE_TAGS = (
    "waifu",
    "maid",
    "uniform",
    "neko",
    "selfies",
    "marin-kitagawa",
    "mori-calliope",
    "raiden-shogun",
    "kamisato-ayaka",
    "elaina",
)
# Mildly suggestive but non-explicit tags. Always fetched with IsNsfw=False.
SUGGESTIVE_TAGS = ("oppai",)
# Explicit tags — never requested, and excluded defensively.
EXCLUDED_TAGS = ("hentai", "ecchi", "oral", "paizuri", "yuri", "yaoi")

# Tags whose waifu.im description names a specific character. Displayed as
# "Character: ..." only when the tag is present on the returned image.
VERIFIED_CHARACTER_TAGS = {
    "marin-kitagawa": "Marin Kitagawa (My Dress-Up Darling)",
    "mori-calliope": "Mori Calliope (Hololive)",
    "raiden-shogun": "Raiden Shogun (Genshin Impact)",
    "kamisato-ayaka": "Kamisato Ayaka (Genshin Impact)",
    "elaina": "Elaina (Wandering Witch: The Journey of Elaina)",
}

SUGGESTIVE_CHANCE = 0.30
USER_AGENT = "InstagramBot/1.0 (https://github.com/; Voroa Instagram bot)"


class ImageError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ImagePolicyError(ImageError):
    """A provider returned content that violates the /Pic content policy.

    These are never papered over with a provider fallback — the request fails
    loudly so a content-filter regression is visible.
    """


@dataclass
class Artwork:
    image_url: str
    source_url: str = ""
    artist: str = ""
    character: str = ""  # only when verified
    anime: str = ""  # only when verified
    tags: List[str] = field(default_factory=list)
    provider: str = ""
    note: str = ""

    def caption_lines(self) -> List[str]:
        lines = []
        if self.character and self.anime:
            lines.append(f"Character: {self.character} — {self.anime}")
        elif self.character:
            lines.append(f"Character: {self.character}")
        elif self.anime:
            lines.append(f"Anime: {self.anime}")
        if self.artist:
            lines.append(f"Artist: {self.artist}")
        if self.tags:
            lines.append("Tags: " + ", ".join(self.tags[:6]))
        if self.source_url:
            lines.append(f"Source: {self.source_url}")
        elif self.image_url:
            lines.append(f"Image: {self.image_url}")
        return lines


class ImageService:
    def __init__(self, waifu_im_enabled: bool = True, http: Optional[HttpClient] = None):
        self.waifu_im_enabled = waifu_im_enabled
        self.http = http or HttpClient(retries=1)

    # ------------------------------------------------------------------ #
    def random_artwork(self, gender: str = "random") -> Artwork:
        """Return one artwork.

        *gender* is ``male``, ``female`` or ``random`` (coin flip).
        """
        if gender == "random":
            gender = random.choice(("male", "female"))
        if gender == "male":
            return self._male_artwork()
        return self._female_artwork()

    # ------------------------------------------------------------------ #
    def _female_artwork(self) -> Artwork:
        errors: List[str] = []
        if self.waifu_im_enabled:
            try:
                return self._waifu_im()
            except ImagePolicyError:
                raise  # content-policy violations never fall back silently
            except (ImageError, HttpError) as exc:
                errors.append(f"waifu.im: {exc}")
                logger.warning("waifu.im failed, trying nekos.best fallback: %s", exc)
        try:
            artwork = self._nekos_best("waifu")
            artwork.note = (
                "Fallback provider: nekos.best (waifu.im was unavailable)."
            )
            return artwork
        except (ImageError, HttpError) as exc:
            errors.append(f"nekos.best: {exc}")

        raise ImageError(
            "Could not fetch anime artwork right now ("
            + "; ".join(errors)
            + "). Please try again later."
        )

    def _male_artwork(self) -> Artwork:
        # waifu.im has no male category; nekos.best 'husbando' is the
        # documented alternative (see module docstring).
        try:
            artwork = self._nekos_best("husbando")
            artwork.note = "Male artwork via nekos.best (waifu.im has no male category)."
            return artwork
        except (ImageError, HttpError) as exc:
            raise ImageError(
                f"Could not fetch male anime artwork right now ({exc}). Please try again later."
            ) from exc

    # ------------------------------------------------------------------ #
    def _waifu_im(self) -> Artwork:
        suggestive = random.random() < SUGGESTIVE_CHANCE
        tag = random.choice(SUGGESTIVE_TAGS if suggestive else SAFE_TAGS)
        params = [
            ("IncludedTags", tag),
            ("IsNsfw", "False"),  # hard content filter: SFW only
            ("PageSize", "1"),
        ]
        for excluded in EXCLUDED_TAGS:
            params.append(("ExcludedTags", excluded))

        try:
            data = self.http.get_json(WAIFU_IM_URL, params=params)
        except HttpStatusError as exc:
            raise ImageError(f"waifu.im returned HTTP {exc.status}") from exc
        except HttpError as exc:
            raise ImageError(f"waifu.im unreachable ({exc})") from exc

        items = (data or {}).get("items") or []
        if not items:
            raise ImageError("waifu.im returned no images for this request")
        item = items[0]
        if item.get("isNsfw"):
            # Second-layer safety net; should be impossible with IsNsfw=False.
            raise ImagePolicyError(
                "waifu.im returned an NSFW-flagged image; refusing to send it. "
                "Please report this so the content filter can be checked."
            )

        artists = item.get("artists") or []
        artist = ""
        if artists:
            artist = artists[0].get("name") or ""

        tag_names = [t.get("name") or t.get("slug") or "" for t in (item.get("tags") or [])]
        tag_slugs = [(t.get("slug") or "").lower() for t in (item.get("tags") or [])]

        character = ""
        for slug in tag_slugs:
            if slug in VERIFIED_CHARACTER_TAGS:
                character = VERIFIED_CHARACTER_TAGS[slug]
                break

        return Artwork(
            image_url=item.get("url") or "",
            source_url=item.get("source") or "",
            artist=artist,
            character=character,
            tags=[n for n in tag_names if n],
            provider="waifu.im",
        )

    def _nekos_best(self, category: str) -> Artwork:
        url = NEKOS_BEST_URL.format(category=category)
        try:
            data = self.http.get_json(url, params={"amount": 1}, headers={"User-Agent": USER_AGENT})
        except HttpStatusError as exc:
            raise ImageError(f"nekos.best returned HTTP {exc.status}") from exc
        except HttpError as exc:
            raise ImageError(f"nekos.best unreachable ({exc})") from exc

        results = (data or {}).get("results") or []
        if not results:
            raise ImageError("nekos.best returned no images")
        item = results[0]

        anime_name = (item.get("anime_name") or "").strip()
        character = ""
        anime = ""
        if anime_name:
            # nekos.best verifies the source series; split only when the
            # provider used the common "Character (Anime)" form.
            if "(" in anime_name and anime_name.endswith(")"):
                character = anime_name[: anime_name.find("(")].strip()
                anime = anime_name[anime_name.find("(") + 1 : -1].strip()
            else:
                anime = anime_name

        return Artwork(
            image_url=item.get("url") or "",
            source_url=(item.get("source_url") or "").strip(),
            artist=(item.get("artist_name") or "").strip(),
            character=character,
            anime=anime,
            tags=[category],
            provider="nekos.best",
        )

    # ------------------------------------------------------------------ #
    def download(self, url: str) -> bytes:
        """Download an image to memory (hard size-capped in HttpClient)."""
        if not url:
            raise ImageError("No image URL to download")
        try:
            body, content_type = self.http.get_bytes(url)
        except HttpError as exc:
            raise ImageError(f"Could not download the image ({exc})") from exc
        if content_type and not content_type.startswith(("image/", "application/octet-stream", "binary/")):
            raise ImageError(f"Unexpected content type '{content_type}' for an image")
        if not body:
            raise ImageError("The downloaded image was empty")
        return body
