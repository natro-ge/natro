"""The weather (Open-Meteo) and the briefing: his day gathered in one tool call."""
import json
from datetime import datetime, timedelta

import pytest

from conftest import run
from natro_agent.briefing import AGAIN_MINUTES, Briefing
from natro_agent.config import timezone
from natro_agent.google_tools import CALENDAR, GMAIL, TASKS
from natro_agent.reminders import Reminders
from natro_agent.tools import ToolError
from natro_agent.weather import FORECAST, GEOCODING, Weather, likely_hours
from test_google import google

# A Sunday morning where he is.
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone())

TBILISI = {"results": [{"name": "Tbilisi", "country": "Georgia", "country_code": "GE", "admin1": "Tbilisi",
                        "latitude": 41.69, "longitude": 44.83}]}
BATUMI = {"results": [
    {"name": "Batumi", "country": "Somewhere Else", "country_code": "XX", "latitude": 1.0, "longitude": 2.0},
    {"name": "Batumi", "country": "Georgia", "country_code": "GE", "admin1": "Adjara", "latitude": 41.64,
     "longitude": 41.64}]}


def forecast(days=2):
    hours = [f"2026-10-{18 + day}T{hour:02d}:00" for day in range(days) for hour in range(24)]
    chances = [0] * len(hours)
    for hour in (14, 15, 16, 20):  # today: rain likely 14:00-16:00 and around 20:00
        chances[hour] = 60
    chances[8] = 90  # this morning, before now: not worth saying
    return {
        "current": {"time": "2026-10-18T09:00", "temperature_2m": 14.6, "apparent_temperature": 13.2,
                    "weather_code": 2, "wind_speed_10m": 7.4},
        "hourly": {"time": hours, "precipitation_probability": chances},
        # Today rainy, then clear days.
        "daily": {"time": [f"2026-10-{18 + day}" for day in range(days)], "weather_code": [61] + [0] * (days - 1),
                  "temperature_2m_max": [19.6] + [22.0] * (days - 1),
                  "temperature_2m_min": [11.2] + [12.0] * (days - 1),
                  "precipitation_probability_max": [60] + [0] * (days - 1),
                  "precipitation_sum": [3.24] + [0.0] * (days - 1),
                  "sunset": [f"2026-10-{18 + day}T18:15" for day in range(days)]},
    }


class FakeOpenMeteo:
    def __init__(self, places=None, fail=False):
        self.places = places or {"Tbilisi": TBILISI, "Batumi": BATUMI}
        self.fail = fail
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, params))
        if self.fail:
            raise OSError("timed out")
        if url == GEOCODING:
            return self.places.get(params["name"], {})
        assert url == FORECAST
        return forecast(params["forecast_days"])


# --- The weather --------------------------------------------------------------------------------

def test_weather_at_home_now_and_today():
    meteo = FakeOpenMeteo()
    text = run(Weather(home="Tbilisi", get=meteo).report())
    assert text == ("Tbilisi, Georgia, now (09:00): 15°C (feels like 13°C), partly cloudy, wind 7 km/h.\n"
                    "Today (Sun 18 Oct): light rain, 11°C to 20°C, rain chance 60% (3.2 mm), likeliest "
                    "14:00-16:00, around 20:00, sunset 18:15.")
    url, params = meteo.calls[-1]
    assert (params["latitude"], params["longitude"], params["forecast_days"]) == (41.69, 44.83, 1)


def test_tomorrow_and_a_place_found_once():
    meteo = FakeOpenMeteo()
    weather = Weather(home="Tbilisi", get=meteo)
    text = run(weather.report("Batumi, Georgia", days=2))
    assert text.startswith("Batumi, Georgia, now")
    assert "Tomorrow (Mon 19 Oct): clear, 12°C to 22°C, rain chance 0%." in text
    assert meteo.calls[-1][1]["latitude"] == 41.64  # the Batumi in Georgia, not the first one found
    run(weather.report("batumi, georgia"))
    assert [url for url, _ in meteo.calls].count(GEOCODING) == 1


def test_days_are_kept_within_a_week():
    meteo = FakeOpenMeteo()
    run(Weather(get=meteo).report(days=30))
    assert meteo.calls[-1][1]["forecast_days"] == 7


def test_unknown_places_and_failures_say_so():
    with pytest.raises(ToolError, match="Couldn't find a place called 'Atlantis'"):
        run(Weather(get=FakeOpenMeteo()).report("Atlantis"))
    with pytest.raises(ToolError, match="Couldn't get the weather: timed out"):
        run(Weather(get=FakeOpenMeteo(fail=True)).report())


def test_likely_hours():
    hours = [datetime(2026, 10, 18, hour) for hour in range(24)]
    chances = [50 if hour in (6, 7, 8, 12, 22, 23) else 10 for hour in range(24)]
    day = hours[0].date()
    assert likely_hours(hours, chances, day) == ["06:00-08:00", "around 12:00", "22:00-23:00"]
    assert likely_hours(hours, chances, day, after=datetime(2026, 10, 18, 9)) == ["around 12:00", "22:00-23:00"]


def test_the_weather_tool_is_free_for_gemini():
    tool, = Weather(home="Tbilisi", get=FakeOpenMeteo()).tools()
    assert tool.name == "weather" and not tool.personal and not tool.confirm
    assert "(Tbilisi)" in tool.description


# --- The briefing -------------------------------------------------------------------------------

EVENT = {"id": "e1", "summary": "Dentist", "start": {"dateTime": "2026-10-18T11:00:00+04:00"},
         "end": {"dateTime": "2026-10-18T12:00:00+04:00"}}
TASK_LIST = {"items": [
    {"id": "t1", "title": "Pay rent", "due": "2026-10-15T00:00:00.000Z"},
    {"id": "t2", "title": "Buy milk", "due": "2026-10-18T00:00:00.000Z"},
    {"id": "t3", "title": "Call the bank", "due": "2026-10-19T00:00:00.000Z"},
    {"id": "t4", "title": "Read a book"},
    {"id": "t5", "title": "Renew passport", "due": "2026-11-30T00:00:00.000Z"},
]}
MAIL = {("GET", f"{GMAIL}/messages"): (200, {"messages": [{"id": "m1"}]}),
        ("GET", f"{GMAIL}/messages/m1"): (200, {"id": "m1", "labelIds": ["UNREAD"], "snippet": "Are we still on?",
                                                 "payload": {"headers": [{"name": "From", "value": "Nino"},
                                                                         {"name": "Subject", "value": "Dinner"}]}})}


def make_briefing(tmp_path, answers=None, reminders=None, now=NOW):
    answers = {("GET", f"{CALENDAR}/events"): (200, {"items": [EVENT]}),
               ("GET", f"{TASKS}/lists/@default/tasks"): (200, TASK_LIST), **MAIL, **(answers or {})}
    g, fake = google(answers)
    briefing = Briefing(tmp_path / "briefing.json", g, Weather(home="Tbilisi", get=FakeOpenMeteo()), reminders)
    briefing.now = lambda: now
    return briefing, fake


def gmail_query(fake):
    return next(params["q"] for method, url, params, _ in fake.requests if url == f"{GMAIL}/messages")


def test_everything_at_once(tmp_path):
    briefing, fake = make_briefing(tmp_path)
    text = run(briefing.run({}))
    assert text.startswith("Briefing for Sunday 18 October, 09:00.")
    assert "Weather:\nTbilisi, Georgia, now (09:00)" in text and "Tomorrow (Mon 19 Oct)" in text
    assert "Calendar, the rest of today:\nSun 18 Oct, 11:00-12:00: Dentist [id e1]" in text
    assert ("Tasks:\nOverdue:\n- Pay rent (was due Thu 15 Oct)\nDue today:\n- Buy milk\nDue tomorrow:\n"
            "- Call the bank\nOther open tasks (no date, or due later): 2.") in text
    assert "Email, unread in Primary since Sat 17 Oct 09:00:\n- From Nino: Dinner (unread)" in text
    rest, tomorrow = [params for method, url, params, _ in fake.requests if url == f"{CALENDAR}/events"]
    assert rest["timeMin"] == "2026-10-18T09:00:00+04:00" and rest["timeMax"] == "2026-10-18T23:59:00+04:00"
    assert tomorrow["timeMin"] == "2026-10-19T00:00:00+04:00" and tomorrow["timeMax"] == "2026-10-20T00:00:00+04:00"


def test_email_since_the_last_briefing(tmp_path):
    briefing, fake = make_briefing(tmp_path)
    run(briefing.run({}))
    first = NOW - timedelta(days=1)  # the first time: the last day
    assert gmail_query(fake) == f"in:inbox category:primary is:unread after:{int(first.timestamp())}"
    assert json.loads((tmp_path / "briefing.json").read_text())["at"] == NOW.isoformat()

    # Asked again soon (he didn't hear it): the same emails, not only what came in between.
    soon = NOW + timedelta(minutes=AGAIN_MINUTES - 5)
    briefing, fake = make_briefing(tmp_path, now=soon)
    run(briefing.run({}))
    assert gmail_query(fake).endswith(f"after:{int(first.timestamp())}")

    # The next day: from that last one on.
    later = NOW + timedelta(days=1)
    briefing, fake = make_briefing(tmp_path, now=later)
    run(briefing.run({}))
    assert gmail_query(fake).endswith(f"after:{int(soon.timestamp())}")

    # Weeks later: a week back at most.
    much_later = NOW + timedelta(days=30)
    briefing, fake = make_briefing(tmp_path, now=much_later)
    run(briefing.run({}))
    assert gmail_query(fake).endswith(f"after:{int((much_later - timedelta(days=7)).timestamp())}")


def test_a_part_that_fails_says_so_and_the_rest_come(tmp_path):
    briefing, _ = make_briefing(tmp_path, {("GET", f"{GMAIL}/messages"): (500, {"error": {"message": "Backend"}})})
    text = run(briefing.run({}))
    assert "Email: couldn't get it (Google said: Backend (HTTP 500))." in text
    assert "Dentist" in text and "Buy milk" in text
    assert not (tmp_path / "briefing.json").exists()  # the emails weren't seen: the next one starts as before


def test_empty_parts(tmp_path):
    briefing, _ = make_briefing(tmp_path, {("GET", f"{CALENDAR}/events"): (200, {"items": []}),
                                           ("GET", f"{TASKS}/lists/@default/tasks"): (200, {"items": []}),
                                           ("GET", f"{GMAIL}/messages"): (200, {})},
                                reminders=Reminders(tmp_path / "reminders.json", {}))
    text = run(briefing.run({}))
    assert "Calendar, the rest of today:\nNothing more today.\nCalendar, tomorrow:\nNothing." in text
    assert "Nothing due today or tomorrow, nothing overdue.\nOther open tasks (no date, or due later): 0." in text
    assert "Reminders still to come today:\nNone." in text
    assert text.endswith(":\nNone.")


def test_without_google(tmp_path):
    briefing = Briefing(tmp_path / "briefing.json", None, Weather(home="Tbilisi", get=FakeOpenMeteo()))
    briefing.now = lambda: NOW
    text = run(briefing.run({}))
    assert "Calendar, tasks and email: Google isn't signed in." in text and "Tbilisi" in text
    assert not (tmp_path / "briefing.json").exists()


def test_reminders_still_to_come_today(tmp_path):
    reminders = Reminders(tmp_path / "reminders.json", {})
    reminders.now = lambda: NOW
    run(reminders.set({"text": "Take the pills.", "at": "2026-10-18T10:00", "repeat": "FREQ=HOURLY;INTERVAL=6"}))
    run(reminders.set({"text": "Call Nino.", "at": "2026-10-18T18:30"}))
    run(reminders.set({"text": "Water the plants.", "at": "2026-10-19T09:00"}))
    run(reminders.set({"text": "Back up the photos.", "when_online": "pc"}))
    briefing, _ = make_briefing(tmp_path, reminders=reminders)
    text = run(briefing.run({}))
    assert ("Reminders still to come today:\n- 10:00: \"Take the pills.\"\n- 16:00: \"Take the pills.\"\n"
            "- 18:30: \"Call Nino.\"\n- 22:00: \"Take the pills.\"\n"
            "- When his PC is back: \"Back up the photos.\"") in text
    assert "Water the plants" not in text


def test_the_briefing_is_personal(tmp_path):
    briefing, _ = make_briefing(tmp_path)
    tool, = briefing.tools()
    assert tool.name == "briefing" and tool.personal and not tool.confirm and not tool.changes
