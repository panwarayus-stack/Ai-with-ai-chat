"""Genderize + Nationalize clients for /Gender.

Docs: https://genderize.io/documentation and https://nationalize.io/

Both endpoints accept anonymous requests on a small free quota and accept an
optional ``apikey`` query parameter (one key from genderize.io works for both
services). Quota exhaustion answers HTTP 429.

The service combines both results and enforces the display rule: when the two
highest country probabilities are within 5 percentage points, both are shown.
Partial failures are reported alongside whichever half succeeded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from src.lib.http import HttpClient, HttpError, HttpStatusError

logger = logging.getLogger(__name__)

GENDERIZE_URL = "https://api.genderize.io/"
NATIONALIZE_URL = "https://api.nationalize.io/"

# Display rule from the spec: if the top-2 country probabilities differ by
# <= 5 percentage points, show both.
COUNTRY_TIE_GAP = 0.05


@dataclass
class GenderResult:
    name: str
    gender: Optional[str] = None
    probability: Optional[float] = None
    count: Optional[int] = None
    error: Optional[str] = None


@dataclass
class NationalityResult:
    name: str
    countries: List[Tuple[str, float]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def top(self) -> Optional[Tuple[str, float]]:
        return self.countries[0] if self.countries else None

    def top_pair(self) -> List[Tuple[str, float]]:
        """Top country(s) to display, honouring the 5pp tie rule."""
        if not self.countries:
            return []
        shown = [self.countries[0]]
        if len(self.countries) > 1:
            gap = self.countries[0][1] - self.countries[1][1]
            if gap <= COUNTRY_TIE_GAP:
                shown.append(self.countries[1])
        return shown


class DemographicsService:
    """Combines Genderize and Nationalize into one analysis."""

    def __init__(
        self,
        genderize_key: str = "",
        nationalize_key: str = "",
        http: Optional[HttpClient] = None,
    ):
        self.genderize_key = genderize_key
        self.nationalize_key = nationalize_key
        self.http = http or HttpClient(retries=1)

    # ------------------------------------------------------------------ #
    def predict_gender(self, name: str) -> GenderResult:
        params = {"name": name}
        if self.genderize_key:
            params["apikey"] = self.genderize_key
        result = GenderResult(name=name)
        try:
            data = self.http.get_json(GENDERIZE_URL, params=params)
        except HttpStatusError as exc:
            result.error = self._quota_message(exc) or f"genderize.io returned HTTP {exc.status}"
            return result
        except HttpError as exc:
            result.error = f"genderize.io unreachable ({exc})"
            return result

        if not isinstance(data, dict):
            result.error = "genderize.io returned an unexpected response"
            return result
        if data.get("error"):
            result.error = f"genderize.io error: {data['error']}"
            return result

        result.gender = data.get("gender")  # may be None for unknown names
        result.probability = _as_float(data.get("probability"))
        result.count = _as_int(data.get("count"))
        return result

    def predict_nationality(self, name: str) -> NationalityResult:
        params = {"name": name}
        if self.nationalize_key:
            params["apikey"] = self.nationalize_key
        result = NationalityResult(name=name)
        try:
            data = self.http.get_json(NATIONALIZE_URL, params=params)
        except HttpStatusError as exc:
            result.error = self._quota_message(exc) or f"nationalize.io returned HTTP {exc.status}"
            return result
        except HttpError as exc:
            result.error = f"nationalize.io unreachable ({exc})"
            return result

        if not isinstance(data, dict):
            result.error = "nationalize.io returned an unexpected response"
            return result
        if data.get("error"):
            result.error = f"nationalize.io error: {data['error']}"
            return result

        countries = []
        for entry in data.get("country") or []:
            code = entry.get("country_id")
            prob = _as_float(entry.get("probability"))
            if code and prob is not None:
                countries.append((str(code).upper(), prob))
        countries.sort(key=lambda item: item[1], reverse=True)
        result.countries = countries[:5]
        return result

    def analyze(self, name: str) -> Tuple[GenderResult, NationalityResult]:
        """Run both lookups (sequentially; both are tiny GETs)."""
        return self.predict_gender(name), self.predict_nationality(name)

    @staticmethod
    def _quota_message(exc: HttpStatusError) -> Optional[str]:
        if exc.status == 429:
            return (
                "quota reached (free tier is limited; add GENDERIZE_API_KEY / "
                "NATIONALIZE_API_KEY or try again later)"
            )
        return None


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
