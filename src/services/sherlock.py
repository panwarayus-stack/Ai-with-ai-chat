"""Public username search for /Sherlock, via the official Sherlock package.

Project: https://github.com/sherlock-project/sherlock (PyPI: ``sherlock-project``)

Verified against the installed package (v0.16.x):

* ``sherlock_project.sherlock.sherlock(username, site_data, query_notify,
  timeout=...)`` performs the sweep and returns per-site
  ``QueryResult`` objects;
* ``sherlock_project.sites.SitesInformation`` loads the bundled site
  manifest. By default it also downloads an exclusions file from GitHub at
  construction time — we pass ``honor_exclusions=False`` so the bot makes no
  surprise network calls at startup, and we always drop NSFW sites the way
  upstream does;
* the CLI's ``--site`` option maps to filtering ``site_data`` ourselves.

Honesty rules (see /Sherlock help text too):

* A hit means "this public page exists", **not** that the same person owns
  every account. The bot says so in every reply.
* The bot only checks publicly accessible pages. It never logs in, never
  bypasses CAPTCHAs and never scrapes behind authentication.
* To stay friendly to small hosts, the default sweep is a curated subset of
  popular sites (``SHERLOCK_SITES``). Set it empty for the full list.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SITES: Tuple[str, ...] = (
    "GitHub",
    "Twitter",
    "Instagram",
    "YouTube",
    "Reddit",
    "TikTok",
    "Pinterest",
    "Twitch",
    "SoundCloud",
    "Medium",
    "Keybase",
    "HackerOne",
    "BuyMeACoffee",
    "Linktree",
    "Telegram",
    "LinkedIn",
    "VK",
    "Roblox",
    "Patreon",
    "Spotify",
    "DeviantArt",
    "Snapchat",
    "GitLab",
    "MyAnimeList",
    "Discord",
)


@dataclass
class SherlockMatch:
    site: str
    url: str


class SherlockError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SherlockService:
    """Lazy, bounded wrapper around the Sherlock engine."""

    def __init__(
        self,
        enabled: bool = True,
        sites: Tuple[str, ...] = (),
        timeout: int = 12,
        overall_deadline: int = 90,
    ):
        self.enabled = enabled
        self.configured_sites = tuple(sites)
        self.timeout = timeout
        self.overall_deadline = overall_deadline
        self._site_data: Optional[Dict[str, dict]] = None

    # ------------------------------------------------------------------ #
    def lookup(self, username: str) -> List[SherlockMatch]:
        """Return public profile URLs found for *username*."""
        if not self.enabled:
            raise SherlockError(
                "/Sherlock is disabled on this bot (SHERLOCK_ENABLED=false)."
            )
        sherlock, sites_information, query_notify_cls, query_status = self._load()
        site_data = self._sites(sites_information)

        result_box: dict = {}
        error_box: dict = {}

        def runner() -> None:
            try:
                result_box["data"] = sherlock(
                    username,
                    site_data,
                    query_notify_cls(),
                    dump_response=False,
                    timeout=self.timeout,
                )
            except BaseException as exc:  # noqa: BLE001 - forwarded below
                error_box["error"] = exc

        thread = threading.Thread(target=runner, name="sherlock-sweep", daemon=True)
        thread.start()
        thread.join(timeout=self.overall_deadline)
        if thread.is_alive():
            raise SherlockError(
                f"The username sweep exceeded {self.overall_deadline}s. "
                "Try again later or narrow SHERLOCK_SITES."
            )
        if "error" in error_box:
            raise SherlockError(f"Sherlock failed: {error_box['error']}")

        matches: List[SherlockMatch] = []
        for site, entry in (result_box.get("data") or {}).items():
            status = entry.get("status")
            if status is not None and getattr(status, "status", None) is not query_status.CLAIMED:
                continue
            url = entry.get("url_user") or entry.get("url_main") or ""
            if url:
                matches.append(SherlockMatch(site=site, url=str(url)))
        matches.sort(key=lambda m: m.site.lower())
        return matches

    # ------------------------------------------------------------------ #
    def _load(self):
        try:
            sherlock_mod = importlib.import_module("sherlock_project.sherlock")
            sites_mod = importlib.import_module("sherlock_project.sites")
            notify_mod = importlib.import_module("sherlock_project.notify")
            result_mod = importlib.import_module("sherlock_project.result")
        except ImportError as exc:
            raise SherlockError(
                "The Sherlock package is not installed. "
                "Run: pip install 'sherlock-project>=0.16,<1'"
            ) from exc
        return (
            sherlock_mod.sherlock,
            sites_mod.SitesInformation,
            notify_mod.QueryNotify,
            result_mod.QueryStatus,
        )

    def _sites(self, sites_information_cls) -> Dict[str, dict]:
        if self._site_data is not None:
            return self._site_data

        data_path = self._bundled_data_path()
        info = sites_information_cls(
            data_file_path=str(data_path),
            honor_exclusions=False,  # no surprise network call at startup
        )
        info.remove_nsfw_sites()  # same default as upstream sherlock
        all_sites = {site.name: site.information for site in info}

        wanted = self.configured_sites or DEFAULT_SITES
        filtered: Dict[str, dict] = {}
        missing: List[str] = []
        by_lower = {name.lower(): name for name in all_sites}
        for name in wanted:
            real = by_lower.get(name.strip().lower())
            if real is None:
                missing.append(name)
            else:
                filtered[real] = all_sites[real]
        if missing:
            logger.warning("SHERLOCK_SITES entries not in Sherlock's manifest: %s", missing)
        if not filtered:
            # Unknown names in config: better a full sweep than no answer.
            logger.warning("No configured sites matched; using the full Sherlock list")
            filtered = all_sites

        self._site_data = filtered
        logger.info("Sherlock configured with %d sites", len(filtered))
        return filtered

    @staticmethod
    def _bundled_data_path() -> Path:
        spec = importlib.util.find_spec("sherlock_project")
        if spec is None or not spec.origin:
            raise SherlockError("sherlock_project package could not be located")
        path = Path(spec.origin).parent / "resources" / "data.json"
        if not path.exists():
            raise SherlockError(f"Sherlock site manifest missing at {path}")
        return path
