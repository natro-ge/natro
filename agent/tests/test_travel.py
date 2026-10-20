"""Travel time (Google's Routes API, faked) and the meeting watcher's word on when to leave."""
import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from conftest import run
from natro_agent.config import timezone
from natro_agent.google_tools import CALENDAR
from natro_agent.tools import Tool, ToolError
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
    tools = make(DRIVE)[0].tools()
    assert [tool.name for tool in tools] == ["travel_time", "travel_routes", "travel_plan"]
    assert all(tool.personal and not tool.confirm for tool in tools)
    assert all("(by default from his home)" in tool.description for tool in tools[:2])


def test_minutes_in_words():
    assert [minutes_words(m) for m in (1, 25, 60, 95)] == ["1 minute", "25 minutes", "1 hour", "1 hour 35 minutes"]


# --- The ways compared, and his own route leg by leg ---------------------------------------------

TOMORROW = (datetime.now(timezone()) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
LEAVING = TOMORROW.replace(tzinfo=None).isoformat(timespec="minutes")  # "...T15:00", as Claude gives it


def at(minutes):
    """Tomorrow at 15:00 and minutes, as Google gives a time (UTC)."""
    return (TOMORROW + timedelta(minutes=minutes)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def walk(secs, meters, words="Head north on Tbilisi Street"):
    return {"travelMode": "WALK", "staticDuration": f"{secs}s", "distanceMeters": meters,
            "navigationInstruction": {"instructions": words}}


def line(name, start, end, leaves, arrives, vehicle="BUS", stops=5, towards="", every=None):
    details = {"stopDetails": {"departureStop": {"name": start}, "arrivalStop": {"name": end},
                               "departureTime": at(leaves), "arrivalTime": at(arrives)},
               "localizedValues": {"departureTime": {"time": {"text": "8:11 PM"}}},
               "transitLine": {"nameShort": name, "vehicle": {"type": vehicle, "name": {"text": "Subway"}}},
               "stopCount": stops, "headsign": towards}
    if every:
        details["headway"] = f"{every * 60}s"
    return {"travelMode": "TRANSIT", "transitDetails": details}


def route(*steps, minutes=90, km=30.0, **more):
    return {"duration": f"{minutes * 60}s", "distanceMeters": km * 1000, "legs": [{"steps": list(steps)}], **more}


BUS_23 = route(walk(420, 530), line("23", "Post Office", "Gulia Square", 10, 45, towards="Tbilisi", stops=13),
               walk(25, 30), line("337", "Gulia Square", "Airport", 50, 90), walk(120, 150))
BUS_23_LATER = route(walk(420, 530), line("23", "Post Office", "Gulia Square", 30, 65), walk(25, 30),
                     line("337", "Gulia Square", "Airport", 70, 110), walk(120, 150))
BY_METRO = route(walk(300, 400), line("21", "Post Office", "Varketili", 5, 35), walk(240, 300),
                 line("1", "Varketili", "Station Square", 40, 60, vehicle="SUBWAY", every=5),
                 line("337", "Station Square", "Airport", 65, 100), walk(120, 150),
                 localizedValues={"transitFare": {"text": "GEL 2.00"}})


def routes(*found):
    return {"routes": list(found)}


def test_the_ways_by_public_transport_compared_step_by_step():
    travel, requests = make(routes(BUS_23_LATER, BY_METRO, BUS_23))
    said = run(travel.travel_routes({"destination": "Tbilisi airport", "mode": "transit", "depart_at": LEAVING}))
    assert said.splitlines() == [
        "2 ways by public transport from home to Tbilisi airport, leaving at 15:00 tomorrow:",
        "1. 1 hour 29 minutes, leave 15:03 tomorrow, arrive 16:32: Bus 23, then Bus 337; 9 minutes on foot, "
        "1 change, 30.0 km.",
        "   - Walk 7 minutes (530 m) to Post Office.",
        "   - Bus 23 towards Tbilisi: from Post Office at 15:10 to Gulia Square at 15:45, 13 stops.",
        "   - Bus 337: from Gulia Square at 15:50 to Airport at 16:30, 5 stops.",
        "   - Walk 2 minutes (150 m) to Tbilisi airport.",
        "   The same way also leaves at 15:23 tomorrow (arrives 16:52).",
        "2. 1 hour 42 minutes, leave 15:00 tomorrow, arrive 16:42: Bus 21, then Metro 1, then Bus 337; 11 minutes on "
        "foot, 2 changes, 30.0 km, fare GEL 2.00.",
        "   - Walk 5 minutes (400 m) to Post Office.",
        "   - Bus 21: from Post Office at 15:05 to Varketili at 15:35, 5 stops.",
        "   - Walk 4 minutes (300 m) to Varketili.",
        "   - Metro 1: from Varketili at 15:40 to Station Square at 16:00, 5 stops, every 5 minutes.",
        "   - Bus 337: from Station Square at 16:05 to Airport at 16:40, 5 stops.",
        "   - Walk 2 minutes (150 m) to Tbilisi airport.",
        "Google Maps: https://www.google.com/maps/dir/?api=1&origin=12+Chavchavadze+Ave%2C+Tbilisi&destination="
        "Tbilisi+airport&travelmode=transit"]
    body = requests.requests[0][1]
    assert body["computeAlternativeRoutes"] and body["travelMode"] == "TRANSIT" and "transitPreferences" not in body
    assert body["departureTime"] == TOMORROW.isoformat()


def test_the_kinds_he_wants_and_fewer_changes():
    travel, requests = make(routes(BUS_23, BY_METRO))
    said = run(travel.travel_routes({"destination": "Tbilisi airport", "use": ["metro"], "prefer": "fewer_changes",
                                     "depart_at": LEAVING}))
    assert requests.requests[0][1]["transitPreferences"] == {"allowedTravelModes": ["SUBWAY"],
                                                             "routingPreference": "FEWER_TRANSFERS"}
    assert said.startswith("1 way by public transport") and "Metro 1" in said and "Bus 23" not in said
    assert "1 other way Google found doesn't take the metro." in said
    travel, _ = make(routes(BUS_23))
    said = run(travel.travel_routes({"destination": "Tbilisi airport", "use": ["metro", "train"]}))
    assert "None of Google's routes take the metro or train" in said and "travel_plan" in said
    with pytest.raises(ToolError, match="use is some of: bus, metro, train, tram"):
        run(travel.travel_routes({"destination": "Tbilisi airport", "use": ["rocket"]}))
    with pytest.raises(ToolError, match="prefer is one of"):
        run(travel.travel_routes({"destination": "Tbilisi airport", "prefer": "scenic"}))


def test_arriving_by_a_time():
    travel, requests = make(routes(BUS_23, BUS_23_LATER))
    arrive = (TOMORROW + timedelta(hours=2)).replace(tzinfo=None).isoformat(timespec="minutes")
    said = run(travel.travel_routes({"destination": "Tbilisi airport", "mode": "transit", "arrive_by": arrive}))
    body = requests.requests[0][1]
    assert body["arrivalTime"] == (TOMORROW + timedelta(hours=2)).isoformat() and "departureTime" not in body
    assert "arriving by 17:00 tomorrow:" in said
    assert "leave 15:23 tomorrow" in said and "also leaves at 15:03 tomorrow (arrives 16:32)" in said  # latest first
    with pytest.raises(ToolError, match="not both"):
        run(travel.travel_routes({"destination": "Tbilisi airport", "depart_at": LEAVING, "arrive_by": arrive}))
    with pytest.raises(ToolError, match="has passed: say the day too"):
        run(travel.travel_routes({"destination": "Tbilisi airport", "mode": "transit", "arrive_by": "2026-01-01T10:00"}))


def test_the_roads_by_car_and_every_turn():
    turns = route(walk(66, 94, "Head southeast on Tbilisi Street"),
                  walk(600, 9000, "Turn left onto the E60\nDestination will be on the left"), minutes=40, km=28.5,
                  staticDuration="2100s", description="Tbilisi Bypass Rd/E60")
    other = route(minutes=43, km=29.7, staticDuration="2500s", description="Kakheti Hwy")
    travel, requests = make(routes(turns, other))
    said = run(travel.travel_routes({"destination": "Tbilisi airport", "mode": "drive", "depart_at": LEAVING,
                                     "turns": True}))
    assert said.splitlines()[:5] == [
        "2 ways by car from home to Tbilisi airport, leaving at 15:00 tomorrow:",
        "1. 40 minutes via Tbilisi Bypass Rd/E60, 28.5 km, traffic adds 5 minutes: leave 15:00 tomorrow, arrive 15:40.",
        "   - Head southeast on Tbilisi Street (94 m)",
        "   - Turn left onto the E60 Destination will be on the left (9.0 km)",
        "2. 43 minutes via Kakheti Hwy, 29.7 km: leave 15:00 tomorrow, arrive 15:43."]
    assert requests.requests[0][1]["routingPreference"] == "TRAFFIC_AWARE"
    assert said.endswith("travelmode=driving")


def test_his_own_route_leg_by_leg():
    to_metro = route(walk(300, 400), line("21", "Post Office", "Varketili", 5, 35), walk(240, 300))
    metro = route(line("1", "Varketili", "Station Square", 45, 65, vehicle="SUBWAY"))
    bus = route(walk(60, 80), line("337", "Station Square", "Airport", 80, 115), walk(120, 150))
    travel, requests = make(routes(to_metro), routes(metro), routes(bus), routes(BUS_23))
    said = run(travel.travel_plan({"depart_at": LEAVING, "legs": [
        {"to": "Varketili metro station", "use": ["bus"]},
        {"to": "Station Square metro station", "use": ["metro"], "stay_minutes": 5},
        {"to": "Tbilisi airport", "mode": "transit"}]}))
    lines = said.splitlines()
    assert lines[0] == ("His route from home to Tbilisi airport in 3 legs: leave 15:00 tomorrow, arrive 16:57 "
                        "(1 hour 57 minutes in all, 15 minutes of it waiting).")
    assert lines[1] == ("Leg 1, by public transport (bus) to Varketili metro station: 39 minutes, leave 15:00 "
                        "tomorrow, arrive 15:39: Bus 21; 9 minutes on foot, 30.0 km.")
    assert ("Leg 2, by public transport (metro) to Station Square metro station: wait 6 minutes, then 20 minutes, "
            "leave 15:45 tomorrow, arrive 16:05: Metro 1; 30.0 km.") in lines
    assert "   Then 5 minutes there." in lines
    assert any(line.startswith("Leg 3, by public transport to Tbilisi airport: wait 9 minutes, then") for line in lines)
    # Google's own quickest way, for him to set his against; the same departure, straight there.
    assert lines[-2] == ("To compare, Google's quickest way by public transport straight there: 1 hour 29 minutes, "
                         "leave 15:03 tomorrow, arrive 16:32: Bus 23, then Bus 337; 9 minutes on foot, 1 change, "
                         "30.0 km.")
    assert lines[-1].startswith("Google Maps, leg by leg: 1. https://www.google.com/maps/dir/?api=1&origin=12+")
    assert " 2. https://www.google.com/maps/dir/?api=1&origin=Varketili+metro+station&" in lines[-1]
    # Each leg leaves when he gets there: the first at 15:00, the next when the last arrived (and he stayed).
    departures = [body.get("departureTime") for _, body, _ in requests.requests]
    assert departures == [TOMORROW.isoformat(), (TOMORROW + timedelta(minutes=39)).isoformat(),
                          (TOMORROW + timedelta(minutes=70)).isoformat(), TOMORROW.isoformat()]
    assert [(body["origin"]["address"], body["destination"]["address"]) for _, body, _ in requests.requests] == [
        (HOME, "Varketili metro station"), ("Varketili metro station", "Station Square metro station"),
        ("Station Square metro station", "Tbilisi airport"), (HOME, "Tbilisi airport")]
    assert [body.get("transitPreferences") for _, body, _ in requests.requests] == [
        {"allowedTravelModes": ["BUS"]}, {"allowedTravelModes": ["SUBWAY"]}, None, None]


def test_his_own_route_back_from_when_he_must_be_there():
    taxi = route(minutes=20, km=12.0)
    metro = route(walk(120, 150), line("1", "Varketili", "Station Square", 60, 80, vehicle="SUBWAY"))
    travel, requests = make(routes(metro), routes(taxi))
    arrive = (TOMORROW + timedelta(minutes=90)).replace(tzinfo=None).isoformat(timespec="minutes")
    said = run(travel.travel_plan({"arrive_by": arrive, "legs": [
        {"to": "Varketili metro station", "mode": "drive"}, {"to": "Station Square", "use": ["metro"]}]}))
    # The metro first (there by 16:30: it arrives 16:20, leaving 15:58), then the taxi, there by 15:58.
    metro_body, taxi_body, quickest_body = (body for _, body, _ in requests.requests)
    assert quickest_body["arrivalTime"] == metro_body["arrivalTime"] and quickest_body["travelMode"] == "TRANSIT"
    assert metro_body["arrivalTime"] == (TOMORROW + timedelta(minutes=90)).isoformat()
    assert "arrivalTime" not in taxi_body
    assert taxi_body["departureTime"] == (TOMORROW + timedelta(minutes=58)).isoformat()  # the traffic about then
    assert said.splitlines()[0] == ("His route from home to Station Square in 2 legs: leave 15:38 tomorrow, arrive "
                                    "16:20 (42 minutes in all).")
    assert "Leg 1, by car to Varketili metro station: 20 minutes, 12.0 km: leave 15:38 tomorrow, arrive 15:58." in said


HERE = "41.69339,44.80153"  # as the phone's location tool says where it is


def test_from_where_his_phone_is():
    travel, requests = make(routes(BUS_23), routes(BUS_23), routes(BUS_23))
    said = run(travel.travel_routes({"destination": "Vake Park", "origin": HERE, "mode": "transit"}))
    assert said.startswith(f"1 way by public transport from {HERE} to Vake Park, leaving now:")
    assert said.endswith("origin=41.69339%2C44.80153&destination=Vake+Park&travelmode=transit")
    run(travel.travel_plan({"origin": " 41.69339, 44.80153 ", "legs": [{"to": "Vake Park"}]}))
    run(travel.travel_time({"destination": "-33.86882,151.20929", "origin": "Mtskheta"}))
    point = {"location": {"latLng": {"latitude": 41.69339, "longitude": 44.80153}}}
    assert [body["origin"] for _, body, _ in requests.requests] == [point, point, {"address": "Mtskheta"}]
    assert requests.requests[2][1]["destination"] == {"location": {"latLng": {"latitude": -33.86882,
                                                                              "longitude": 151.20929}}}
    # Numbers that aren't a place on Earth, or without their decimals, are an address for Google to find.
    for place in ("95.1,45.2", "41,45", "12 Chavchavadze Ave, 0179"):
        run(travel.travel_time({"destination": "Vake Park", "origin": place}))
        assert requests.requests[-1][1]["origin"] == {"address": place}


def test_a_plan_that_cant_be_made_says_so():
    travel, _ = make(routes(BUS_23), {})
    with pytest.raises(ToolError, match="Leg 2: Google found no route by public transport from Gulia Square to Mars"):
        run(travel.travel_plan({"legs": [{"to": "Gulia Square", "mode": "transit"}, {"to": "Mars", "mode": "transit"}]}))
    for legs, error in [([], "legs is a list"), ([{"to": "A"}] * 6, "At most 5 legs"),
                        ([{"mode": "walk"}], "Each leg needs its to"), ([{"to": "A", "stay_minutes": "a while"}],
                                                                         "stay_minutes is a number")]:
        with pytest.raises(ToolError, match=error):
            run(travel.travel_plan({"legs": legs}))
    nowhere, _ = make(routes(BUS_23), home="")
    with pytest.raises(ToolError, match="doesn't know where he is"):
        run(nowhere.travel_plan({"legs": [{"to": "A"}]}))


# --- When to leave for a meeting ----------------------------------------------------------------

def watching(tmp_path, events, travel, now=NOW):
    watchers, _ = make_watchers(tmp_path, {("GET", f"{CALENDAR}/events"): (200, {"items": events})}, now=now)
    watchers.travel = travel
    return watchers


def test_a_meeting_further_away_gives_the_heads_up_before_he_has_to_leave(tmp_path):
    start = NOW + timedelta(minutes=60)
    travel, routes = make(DRIVE)  # 25 minutes
    watchers = watching(tmp_path, [event("e1", start, location="City clinic")], travel)
    run(watchers.check())
    assert watchers.deliver.alerts == []  # leave at 15:30 (25 minutes and 5 to spare before 16:00)
    watchers.now = lambda: NOW + timedelta(minutes=20)  # the heads-up's 10 minutes before that
    run(watchers.check())
    alert, = watchers.deliver.alerts
    assert alert["text"] == ("Leave in 10 minutes for Dentist at City clinic: about 25 minutes by car from home; it "
                             "starts at 16:00.")
    assert alert["urgent"]
    watchers.now = lambda: NOW + timedelta(minutes=51)  # 9 minutes before: no heads-up as well
    run(watchers.check())
    assert len(watchers.deliver.alerts) == 1
    assert len(routes.requests) == 1  # looked up once (again only after a while)


def test_seen_after_the_time_to_leave_it_says_so(tmp_path):
    travel, _ = make(DRIVE)
    watchers = watching(tmp_path, [event("e1", NOW + timedelta(minutes=25), location="City clinic")], travel)
    run(watchers.check())
    assert [alert["text"] for alert in watchers.deliver.alerts] == [
        "Time to leave for Dentist at City clinic: about 25 minutes by car from home; it starts at 15:25."]


class Phone:
    """His phone, connected, as the watchers see it: its location tool says where it is (or fails)."""

    name = "phone"

    def __init__(self, where="41.71520,44.76900 (within 12 m, just now)"):
        self.where = where
        self.asked = 0

    def tools(self):
        async def location(args):
            self.asked += 1
            if isinstance(self.where, Exception):
                raise self.where
            return self.where
        return [Tool("phone_location", "Where the phone is.", {"type": "object"}, location, "phone", personal=True)]


def test_the_trip_starts_where_his_phone_is(tmp_path):
    travel, routes = make(DRIVE, home="")  # no home address needed
    phone = Phone()
    watchers = watching(tmp_path, [event("e1", NOW + timedelta(minutes=40), location="City clinic"),
                                   event("e2", NOW + timedelta(minutes=50), summary="Padel", location="Vake")],
                        travel)
    watchers.devices["phone"] = phone
    run(watchers.check())
    assert [alert["text"] for alert in watchers.deliver.alerts] == [
        "Leave in 10 minutes for Dentist at City clinic: about 25 minutes by car from where you are; it starts at "
        "15:40."]
    assert all(body["origin"] == {"location": {"latLng": {"latitude": 41.7152, "longitude": 44.769}}}
               for _, body, _ in routes.requests)
    assert phone.asked == 1  # once for both meetings


def test_without_the_phones_location_the_trip_starts_at_home(tmp_path):
    travel, routes = make(DRIVE)
    watchers = watching(tmp_path, [event("e1", NOW + timedelta(minutes=40), location="City clinic")], travel)
    watchers.devices["phone"] = Phone(ToolError("The phone's location is off."))
    run(watchers.check())
    assert watchers.deliver.alerts[0]["text"].endswith("by car from home; it starts at 15:40.")
    assert routes.requests[0][1]["origin"] == {"address": HOME}


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


def test_home_is_his_home_address():
    travel, routes = make(DRIVE)
    ask(travel, destination="home", origin="Didube metro station, Tbilisi")
    ask(travel, destination="Tbilisi airport", origin="My home")
    assert routes.requests[0][1]["destination"] == {"address": HOME}
    assert routes.requests[1][1]["origin"] == {"address": HOME}
