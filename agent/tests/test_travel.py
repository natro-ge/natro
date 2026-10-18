"""Travel time (Google's Routes API, faked) and the meeting watcher's word on when to leave."""
import io
import json
import urllib.error
from datetime import datetime, timedelta

import pytest

from conftest import run
from natro_agent.config import timezone
from natro_agent.google_tools import CALENDAR
from natro_agent.tools import ToolError
from natro_agent.travel import URL, Travel, minutes_words
from test_watchers import Shown, event, make as make_watchers

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone())
HOME = "12 Chavchavadze Ave, Tbilisi"

DRIVE = {"routes": [{"duration": "1520s", "staticDuration": "1100s", "distanceMeters": 18350}]}
TRANSIT = {"routes": [{"duration": "1860s", "distanceMeters": 9100, "legs": [{"steps": [
    {"travelMode": "WALK"},
    {"travelMode": "TRANSIT", "transitDetails": {
        "stopDetails": {"departureStop": {"name": "Rustaveli"}, "arrivalStop": {"name": "Station Square"}},
        "localizedValues": {"departureTime": {"time": {"text": "15:07"}}},
        "transitLine": {"nameShort": "1", "vehicle": {"name": {"text": "Metro"}}}, "stopCount": 4}},
    {"travelMode": "TRANSIT", "transitDetails": {
        "stopDetails": {"departureStop": {"name": "Station Square"}, "arrivalStop": {"name": "Airport"}},
        "transitLine": {"name": "Airport Express", "vehicle": {"name": {"text": "Bus"}}}}},
]}]}]}


class Routes:
    """Stands in for the Routes API: records each request, answers with the next of answers (an exception is
    raised)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, url, body, key):
        self.requests.append((url, body, key))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def make(*answers, home=HOME, key="maps-key"):
    routes = Routes(*answers)
    return Travel(key=key, home=home, post=routes), routes


def ask(travel, **args):
    return run(travel.travel_time(args))


def test_by_car_with_traffic_from_home():
    travel, routes = make(DRIVE)
    assert ask(travel, destination="Tbilisi airport") == ("25 minutes by car from home, 18.4 km (traffic adds 7 "
                                                         "minutes).")
    url, body, key = routes.requests[0]
    assert (url, key) == (URL, "maps-key")
    assert body == {"origin": {"address": HOME}, "destination": {"address": "Tbilisi airport"}, "travelMode": "DRIVE",
                    "languageCode": "en", "units": "METRIC", "routingPreference": "TRAFFIC_AWARE"}


def test_by_public_transport_with_its_lines_from_somewhere_else():
    travel, routes = make(TRANSIT)
    said = ask(travel, destination="Tbilisi airport", origin="Rustaveli Avenue", mode="transit")
    assert said == ("31 minutes by public transport from Rustaveli Avenue, 9.1 km.\n"
                    "- Metro 1 from Rustaveli at 15:07 to Station Square (4 stops)\n"
                    "- Bus Airport Express from Station Square to Airport")
    assert "routingPreference" not in routes.requests[0][1]


def test_leaving_later():
    travel, routes = make(DRIVE)
    later = (datetime.now(timezone()) + timedelta(days=1)).replace(microsecond=0)
    ask(travel, destination="Mtskheta", depart_at=later.replace(tzinfo=None).isoformat(timespec="minutes"))
    assert routes.requests[0][1]["departureTime"] == later.replace(second=0).isoformat()
    with pytest.raises(ToolError, match="depart_at is a time"):
        ask(travel, destination="Mtskheta", depart_at="after lunch")


def test_what_goes_wrong_says_so():
    travel, _ = make({})
    with pytest.raises(ToolError, match="no route by car from home to Atlantis"):
        ask(travel, destination="Atlantis")
    refused = urllib.error.HTTPError(URL, 403, "Forbidden", {}, io.BytesIO(json.dumps(
        {"error": {"message": "Routes API has not been used in project 123 before or it is disabled."}}).encode()))
    travel, _ = make(refused)
    with pytest.raises(ToolError, match=r"has not been used in project 123.*\(HTTP 403\)"):
        ask(travel, destination="Mtskheta")
    nowhere, _ = make(DRIVE, home="")
    with pytest.raises(ToolError, match="doesn't know where he is"):
        ask(nowhere, destination="Mtskheta")
    assert "from Vake" in ask(nowhere, destination="Mtskheta", origin="Vake")
    with pytest.raises(ToolError, match="mode is one of"):
        ask(travel, destination="Mtskheta", mode="helicopter")


def test_offered_only_with_a_key():
    assert Travel(key="", home=HOME).tools() == []
    tool, = make(DRIVE)[0].tools()
    assert tool.name == "travel_time" and tool.personal and not tool.confirm
    assert "(by default from his home)" in tool.description


def test_minutes_in_words():
    assert [minutes_words(m) for m in (1, 25, 60, 95)] == ["1 minute", "25 minutes", "1 hour", "1 hour 35 minutes"]


# --- When to leave for a meeting ----------------------------------------------------------------

def watching(tmp_path, events, travel, now=NOW):
    watchers, _ = make_watchers(tmp_path, {("GET", f"{CALENDAR}/events"): (200, {"items": events})}, now=now)
    watchers.travel = travel
    return watchers


def test_a_meeting_further_away_says_when_to_leave_instead(tmp_path):
    start = NOW + timedelta(minutes=40)
    travel, routes = make(DRIVE)  # 25 minutes
    watchers = watching(tmp_path, [event("e1", start, location="City clinic")], travel)
    run(watchers.check())
    assert watchers.deliver.alerts == []  # leave at 15:10 (25 minutes and 5 to spare before 15:40)
    watchers.now = lambda: NOW + timedelta(minutes=10)
    run(watchers.check())
    alert, = watchers.deliver.alerts
    assert alert["text"] == ("Time to leave for Dentist at City clinic: about 25 minutes by car from home; it starts "
                             "at 15:40.")
    assert alert["urgent"]
    watchers.now = lambda: NOW + timedelta(minutes=31)  # 9 minutes before: no heads-up as well
    run(watchers.check())
    assert len(watchers.deliver.alerts) == 1
    assert len(routes.requests) == 1  # looked up once (again only after half an hour)


def test_online_close_or_unknown_places_get_the_heads_up(tmp_path):
    start = NOW + timedelta(minutes=8)
    events = [event("e1", start, summary="Standup", location="https://meet.google.com/abc-defg-hij"),
              event("e2", start + timedelta(minutes=1), summary="Coffee", location="Corner cafe"),
              event("e3", start + timedelta(minutes=2), summary="Lunch", location="Atlantis")]
    close = {"routes": [{"duration": "180s", "distanceMeters": 900}]}
    travel, routes = make(close, {})  # Corner cafe is 3 minutes away; Atlantis can't be found
    watchers = watching(tmp_path, events, travel)
    run(watchers.check())
    assert [alert["text"] for alert in watchers.deliver.alerts] == [
        "Standup at https://meet.google.com/abc-defg-hij in 8 minutes.", "Coffee at Corner cafe in 9 minutes.",
        "Lunch at Atlantis in 10 minutes."]
    assert [body["destination"]["address"] for _, body, _ in routes.requests] == ["Corner cafe", "Atlantis"]


def test_without_a_home_address_only_the_heads_up(tmp_path):
    travel, routes = make(DRIVE, home="")
    watchers = watching(tmp_path, [event("e1", NOW + timedelta(minutes=9), location="City clinic")], travel)
    run(watchers.check())
    assert [alert["text"] for alert in watchers.deliver.alerts] == ["Dentist at City clinic in 9 minutes."]
    assert routes.requests == []
