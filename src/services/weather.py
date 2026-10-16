"""Open-Meteo weather client for /Weather.

Docs: https://open-meteo.com/

Both endpoints are free for non-commercial use without an API key:

* Geocoding: ``https://geocoding-api.open-meteo.com/v1/search``
* Forecast:  ``https://api.open-meteo.com/v1/forecast``

``timezone=auto`` makes the API return times in the location's local timezone,
so the reply can show local time without any timezone database of our own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from src.lib.http import HttpClient, HttpError, HttpStatusError

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes (public table from Open-Meteo docs).
WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow fall",
    73: "moderate snow fall",
    75: "heavy snow fall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def weather_description(code: Optional[int]) -> str:
    try:
        return WEATHER_CODES.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


@dataclass
class Location:
    name: str
    country: str
    latitude: float
    longitude: float
    timezone: str = "auto"
    admin1: str = ""


@dataclass
class WeatherReport:
    location: Location
    temperature: Optional[float] = None
    feels_like: Optional[float] = None
    wind_speed: Optional[float] = None
    humidity: Optional[int] = None
    description: str = ""
    is_day: Optional[bool] = None
    local_time: str = ""
    daily: List[dict] = field(default_factory=list)
    units: str = "metric"

    @property
    def temp_unit(self) -> str:
        return "°F" if self.units == "imperial" else "°C"

    @property
    def wind_unit(self) -> str:
        return "mph" if self.units == "imperial" else "km/h"


class WeatherError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class WeatherService:
    def __init__(self, units: str = "metric", http: Optional[HttpClient] = None):
        self.units = units if units in ("metric", "imperial") else "metric"
        self.http = http or HttpClient(retries=1)

    # ------------------------------------------------------------------ #
    def geocode(self, city: str) -> Location:
        try:
            data = self.http.get_json(
                GEOCODE_URL,
                params={
                    "name": city,
                    "count": 1,
                    "language": "en",
                    "format": "json",
                },
            )
        except HttpStatusError as exc:
            raise WeatherError(f"The geocoding service returned HTTP {exc.status}.") from exc
        except HttpError as exc:
            raise WeatherError(f"Could not look up that city ({exc}).") from exc

        results = (data or {}).get("results") or []
        if not results:
            raise WeatherError(
                f"I couldn't find a city named '{city}'. Check the spelling or "
                "try adding the country (e.g. 'Agra, India')."
            )
        top = results[0]
        try:
            return Location(
                name=str(top.get("name", city)),
                country=str(top.get("country", "") or ""),
                latitude=float(top["latitude"]),
                longitude=float(top["longitude"]),
                timezone=str(top.get("timezone", "auto") or "auto"),
                admin1=str(top.get("admin1", "") or ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherError("The geocoding service returned incomplete data.") from exc

    def fetch(self, location: Location) -> WeatherReport:
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "is_day",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "forecast_days": 3,
            "timezone": "auto",
        }
        if self.units == "imperial":
            params["temperature_unit"] = "fahrenheit"
            params["wind_speed_unit"] = "mph"

        try:
            data = self.http.get_json(FORECAST_URL, params=params)
        except HttpStatusError as exc:
            raise WeatherError(f"The weather service returned HTTP {exc.status}.") from exc
        except HttpError as exc:
            raise WeatherError(f"Could not fetch the weather ({exc}).") from exc

        current = (data or {}).get("current") or {}
        daily = (data or {}).get("daily") or {}
        report = WeatherReport(location=location, units=self.units)
        report.temperature = _as_float(current.get("temperature_2m"))
        report.feels_like = _as_float(current.get("apparent_temperature"))
        report.wind_speed = _as_float(current.get("wind_speed_10m"))
        report.humidity = _as_int(current.get("relative_humidity_2m"))
        report.description = weather_description(current.get("weather_code"))
        is_day = current.get("is_day")
        report.is_day = bool(is_day) if is_day is not None else None
        report.local_time = str(current.get("time", "") or "")

        times = daily.get("time") or []
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        for i, day in enumerate(times[:3]):
            report.daily.append(
                {
                    "date": str(day),
                    "description": weather_description(codes[i] if i < len(codes) else None),
                    "high": _as_float(highs[i]) if i < len(highs) else None,
                    "low": _as_float(lows[i]) if i < len(lows) else None,
                }
            )
        return report

    def lookup(self, city: str) -> WeatherReport:
        """Geocode + fetch in one call."""
        return self.fetch(self.geocode(city))


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
