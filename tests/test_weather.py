"""/Weather: Open-Meteo geocoding + forecast handling."""

from __future__ import annotations

from tests.conftest import FakeHttp, FakeResponse
from src.commands.base import dispatch


GEO = {
    "results": [
        {
            "name": "Mathura",
            "country": "India",
            "admin1": "Uttar Pradesh",
            "latitude": 27.49,
            "longitude": 77.67,
            "timezone": "Asia/Kolkata",
        }
    ]
}

FORECAST = {
    "current": {
        "time": "2026-10-16T14:30",
        "temperature_2m": 31.2,
        "apparent_temperature": 34.8,
        "relative_humidity_2m": 45,
        "is_day": 1,
        "weather_code": 0,
        "wind_speed_10m": 12.4,
    },
    "daily": {
        "time": ["2026-10-16", "2026-10-17", "2026-10-18"],
        "weather_code": [1, 61, 95],
        "temperature_2m_max": [33.0, 30.0, 28.0],
        "temperature_2m_min": [22.0, 21.0, 20.0],
    },
}


def _http():
    http = FakeHttp()
    http.route("geocoding-api.open-meteo.com", FakeResponse(json_data=GEO))
    http.route("api.open-meteo.com", FakeResponse(json_data=FORECAST))
    return http


def test_weather_shows_required_fields(make_ctx):
    http = _http()
    result = dispatch(make_ctx(http), "/Weather Mathura")
    text = result.text
    assert "Mathura" in text
    assert "31.2°C" in text  # temperature
    assert "34.8°C" in text  # apparent temperature
    assert "clear sky" in text  # description
    assert "12.4 km/h" in text  # wind
    assert "open-meteo.com" in text
    # Both endpoints were used.
    assert any("geocoding" in u for u in http.urls())
    assert any("api.open-meteo.com" in u for u in http.urls())


def test_forecast_days_shown(make_ctx):
    result = dispatch(make_ctx(_http()), "/Weather Mathura")
    assert "2026-10-16" in result.text
    assert "2026-10-17" in result.text
    assert "rain" in result.text  # code 61
    assert "thunderstorm" in result.text  # code 95


def test_local_time_and_timezone_shown(make_ctx):
    result = dispatch(make_ctx(_http()), "/Weather Mathura")
    assert "2026-10-16 14:30" in result.text


def test_city_not_found_helpful_message(make_ctx):
    http = FakeHttp()
    http.route("geocoding-api.open-meteo.com", FakeResponse(json_data={"results": []}))
    result = dispatch(make_ctx(http), "/Weather Nowhereville")
    assert "couldn't find a city" in result.text.lower()
    assert "Nowhereville" in result.text


def test_missing_argument(make_ctx):
    result = dispatch(make_ctx(), "/Weather")
    assert "Usage" in result.text


def test_bad_characters_rejected(make_ctx):
    result = dispatch(make_ctx(), "/Weather <script>alert(1)</script>")
    assert "city" in result.text.lower()


def test_imperial_units(make_ctx):
    from src.services.weather import WeatherService

    http = _http()
    svc = WeatherService(units="imperial", http=http)
    report = svc.lookup("Mathura")
    assert report.temp_unit == "°F"
    assert report.wind_unit == "mph"
    forecast_call = [c for c in http.calls if "://api.open-meteo.com" in c.url][0]
    assert forecast_call.params["temperature_unit"] == "fahrenheit"
    assert forecast_call.params["wind_speed_unit"] == "mph"
