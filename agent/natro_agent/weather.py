"""The weather from Open-Meteo (free, no key, no sign-up): https://open-meteo.com

weather tells now and the days ahead for a place (his home, NATRO_WEATHER_PLACE, unless another is named):
the conditions, the lowest and highest temperatures, and the chance of rain with the hours it's likeliest.
A place's name is found with Open-Meteo's geocoding ("Batumi, Georgia" picks the Batumi in Georgia) and
kept for the next time. Not personal, so the free Gemini tier may use it too.
"""
import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime

from natro_agent.tools import Tool, ToolError

GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 15
HOME = "Tbilisi"
MOST_DAYS = 7
# Hours with at least this chance of rain (or snow) are named.
LIKELY = 50

# WMO weather codes, as Open-Meteo gives them.
CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


def get(url, params):
    """GET JSON (blocking)."""
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "Natro"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def degrees(value):
    return f"{round(value)}°C"


def conditions(code):
    return CODES.get(code, "unknown weather")


def likely_hours(hours, chances, day, after=None):
    """The hours of day (from after on) with a likely chance of rain, as ranges: "14:00-17:00", "around 20:00"."""
    likely = [moment.hour for moment, chance in zip(hours, chances)
              if moment.date() == day and (after is None or moment >= after) and (chance or 0) >= LIKELY]
    ranges = []
    for hour in likely:
        if ranges and ranges[-1][1] == hour - 1:
            ranges[-1][1] = hour
        else:
            ranges.append([hour, hour])
    return [f"around {start:02d}:00" if start == end else f"{start:02d}:00-{end:02d}:00" for start, end in ranges]


def day_name(day, today):
    if day == today:
        return "Today"
    if (day - today).days == 1:
        return "Tomorrow"
    return f"{day:%A}"


def format_forecast(place, data):
    """Open-Meteo's forecast as a few lines for the model."""
    now = data.get("current") or {}
    hourly = data.get("hourly") or {}
    hours = [datetime.fromisoformat(moment) for moment in hourly.get("time", [])]
    chances = hourly.get("precipitation_probability", [])
    at = datetime.fromisoformat(now["time"]) if now.get("time") else None
    lines = []
    if at:
        lines.append(f"{place}, now ({at:%H:%M}): {degrees(now['temperature_2m'])} (feels like "
                     f"{degrees(now['apparent_temperature'])}), {conditions(now.get('weather_code'))}, wind "
                     f"{round(now.get('wind_speed_10m') or 0)} km/h.")
    else:
        lines.append(f"{place}:")
    daily = data.get("daily") or {}
    today = at.date() if at else None
    for i, day_text in enumerate(daily.get("time", [])):
        day = date.fromisoformat(day_text)
        chance = daily["precipitation_probability_max"][i]
        rain = f"rain chance {chance or 0}%"
        amount = daily.get("precipitation_sum", [0] * (i + 1))[i] or 0
        if amount >= 0.1:
            rain += f" ({amount:.1f} mm)"
        if spans := likely_hours(hours, chances, day, at.replace(minute=0) if day == today else None):
            rain += f", likeliest {', '.join(spans)}"
        sunset = daily.get("sunset", [None] * (i + 1))[i]
        sunset = f", sunset {sunset[-5:]}" if sunset and day == today else ""
        lines.append(f"{day_name(day, today or day)} ({day:%a %d %b}): {conditions(daily['weather_code'][i])}, "
                     f"{degrees(daily['temperature_2m_min'][i])} to {degrees(daily['temperature_2m_max'][i])}, "
                     f"{rain}{sunset}.")
    return "\n".join(lines)


class Weather:
    def __init__(self, home=None, get=get):
        self.home = home or os.environ.get("NATRO_WEATHER_PLACE") or HOME
        self._get = get
        self._places = {}  # name as asked -> (name to say, latitude, longitude)

    async def report(self, place=None, days=1):
        """The weather now and for days days (today first), as text."""
        try:
            days = max(1, min(int(days or 1), MOST_DAYS))
        except (TypeError, ValueError):
            raise ToolError("days is a number from 1 to 7.")
        name, latitude, longitude = await self._find(" ".join(str(place or "").split()) or self.home)
        params = {"latitude": latitude, "longitude": longitude, "timezone": "auto", "forecast_days": days,
                  "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                  "hourly": "precipitation_probability",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                           "precipitation_sum,sunset"}
        data = await self._fetch(FORECAST, params)
        return format_forecast(name, data)

    async def _find(self, place):
        key = place.casefold()
        if key in self._places:
            return self._places[key]
        name, _, within = (part.strip() for part in place.partition(","))
        data = await self._fetch(GEOCODING, {"name": name, "count": 10, "language": "en", "format": "json"})
        results = data.get("results") or []
        if within:
            wanted = within.casefold()
            results = [found for found in results if any(
                wanted in str(found.get(field, "")).casefold() for field in ("country", "admin1", "country_code"))
            ] or results
        if not results:
            raise ToolError(f"Couldn't find a place called {place!r}.")
        found = results[0]
        said = ", ".join(part for part in (found.get("name"), found.get("country")) if part)
        self._places[key] = (said, found["latitude"], found["longitude"])
        return self._places[key]

    async def _fetch(self, url, params):
        try:
            return await asyncio.to_thread(self._get, url, params)
        except OSError as e:  # includes HTTP errors and timeouts
            raise ToolError(f"Couldn't get the weather: {e}")
        except ValueError:
            raise ToolError("The weather service sent something unreadable.")

    def tools(self):
        async def run(args):
            return await self.report(args.get("place"), args.get("days", 1))

        return [Tool(
            name="weather",
            description=(f"The weather now and for the coming days: conditions, temperatures and the chance of rain "
                         f"with its likeliest hours. Without place, where he lives ({self.home})."),
            input_schema={"type": "object", "properties": {
                "place": {"type": "string", "description": "A town or city, with its country if it could be "
                                                           "another (\"Batumi, Georgia\")."},
                "days": {"type": "integer", "description": "How many days from today, 1 to 7 (2 for tomorrow; "
                                                          "default 1)."}}},
            run=run, source="natro")]
