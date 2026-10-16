"""``/Weather <city>`` — current weather + short forecast via Open-Meteo."""

from __future__ import annotations

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.validation import clean_city
from src.services.weather import WeatherError


def _fmt_temp(value, unit: str) -> str:
    return "?" if value is None else f"{value:g}{unit}"


def handle_weather(ctx: CommandContext, args: str) -> CommandResult:
    city = clean_city(args, "/Weather <city>")
    try:
        report = ctx.services.weather.lookup(city)
    except WeatherError as exc:
        return CommandResult(text=exc.message)

    loc = report.location
    where = loc.name + (f", {loc.admin1}" if loc.admin1 and loc.admin1 != loc.name else "")
    if loc.country:
        where += f", {loc.country}"

    lines = [f"Weather for {where}:"]
    lines.append(
        f"Now: {_fmt_temp(report.temperature, report.temp_unit)} — {report.description}"
    )
    if report.feels_like is not None:
        lines.append(f"Feels like: {_fmt_temp(report.feels_like, report.temp_unit)}")
    if report.wind_speed is not None:
        lines.append(f"Wind: {report.wind_speed:g} {report.wind_unit}")
    if report.humidity is not None:
        lines.append(f"Humidity: {report.humidity}%")

    for day in report.daily:
        date = str(day.get("date", ""))[:10]
        lines.append(
            f"{date}: {day['description']}, "
            f"{_fmt_temp(day['low'], report.temp_unit)} to {_fmt_temp(day['high'], report.temp_unit)}"
        )

    if report.local_time:
        lines.append("")
        lines.append(f"Local time there: {report.local_time.replace('T', ' ')}")
    lines.append("Source: open-meteo.com")
    return CommandResult(text="\n".join(lines))


register(
    Command(
        name="Weather",
        handler=handle_weather,
        usage="/Weather <city>",
        description="Current weather, feels-like temperature, wind and a 3-day forecast.",
        examples=["/Weather Mathura", "/Weather Tokyo", "/Weather Agra, India"],
        category="Lookups",
        troubleshooting="No API key needed. Try 'City, Country' if the city is ambiguous.",
    )
)
