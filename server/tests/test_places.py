"""His places: saved by voice (Google's Geocoding API, faked), known by name to her and the travel tools, and sent to the
device that checks them."""
import asyncio
import json
from datetime import datetime

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from conftest import FakeClient, run
from natro_agent import log
from natro_agent.agent import Agent, personal_or_risky
from natro_agent.memory import Memory
from natro_agent.places import Places, name_key
from natro_agent.server import Server
from natro_agent.tools import Tool, ToolError
from natro_agent.travel import Travel
from test_travel import DRIVE, Routes

GYM = {"formatted_address": "Fitness Park, 12 Kostava St, Rustavi", "geometry": {"location": {"lat": 41.54, "lng": 45.0}}}


class Geocoding:
    """Stands in for the Geocoding API: records each lookup, answers with these results (or this answer)."""

    def __init__(self, *results, answer=None):
        self.answer = answer or {"status": "OK" if results else "ZERO_RESULTS", "results": list(results)}
        self.asked = []

    def __call__(self, params, key):
        self.asked.append(params)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Phone:
    name = "phone"
    label = "phone"
    places = True

    def __init__(self, says="41.69339,44.80153 (within 12 m, 5 s ago)"):
        self.says = says
        self.posted = []

    def tools(self):
        async def location(args):
            return self.says
        return [Tool("phone_location", "Where the phone is.", {"type": "object", "properties": {}}, location, "phone")]

    def post(self, **message):
        self.posted.append(message)


def make(tmp_path, *results, devices=None, key="maps-key", answer=None):
    geocoding = Geocoding(*results, answer=answer)
    return Places(tmp_path / "places.json", {} if devices is None else devices, key=key, get=geocoding), geocoding


def saved(places, *args, **kwargs):
    return run(places.save(*args, **kwargs))


def test_an_address_gives_a_place_its_point_and_it_is_kept(tmp_path):
    places, geocoding = make(tmp_path, GYM)
    said = saved(places, "Gym", "12 Kostava St, Rustavi", names=["the gym club"])
    assert said.splitlines()[0] == "Saved Gym: Fitness Park, 12 Kostava St, Rustavi."
    assert geocoding.asked == [{"address": "12 Kostava St, Rustavi", "language": "en"}]
    kept = Places(tmp_path / "places.json", {}).items
    assert [(item["name"], item["names"], item["lat"], item["lng"], item["radius"]) for item in kept] == [
        ("Gym", ["the gym club"], 41.54, 45.0, 150)]


def test_here_is_where_his_phone_is_and_gets_its_address(tmp_path):
    places, geocoding = make(tmp_path, GYM, devices={"phone": Phone()})
    assert "Fitness Park" in saved(places, "Gym", "here")
    assert (places.items[0]["lat"], places.items[0]["lng"]) == (41.69339, 44.80153)
    assert geocoding.asked == [{"latlng": "41.69339,44.80153", "language": "en"}]


def test_here_with_the_phone_offline_or_unsure_asks_for_the_address(tmp_path):
    places, _ = make(tmp_path)
    with pytest.raises(ToolError, match="phone is offline"):
        saved(places, "Gym", "here")
    places, _ = make(tmp_path, devices={"phone": Phone(says="Location is off.")})
    with pytest.raises(ToolError, match="can't say where it is"):
        saved(places, "Gym", "where I am")
    with pytest.raises(ToolError, match="can't say where it is"):
        saved(places, "Gym", "my location")
    assert places.items == []


def test_a_point_with_no_address_from_google_is_still_saved(tmp_path, log_dir):
    places, _ = make(tmp_path, devices={"phone": Phone()}, answer={"status": "REQUEST_DENIED", "error_message": "Off."})
    assert saved(places, "Gym", "here").splitlines()[0] == "Saved Gym: 41.69339,44.80153."
    assert places.prompt_lines() == ["Gym: 41.69339,44.80153"]


@pytest.mark.parametrize("answer, words", [
    ({"status": "REQUEST_DENIED", "error_message": "This API is not activated."}, "This API is not activated"),
    (OSError("no network"), "Couldn't reach"),
    ({"status": "ZERO_RESULTS", "results": []}, "Google found nothing"),
])
def test_an_address_google_cannot_find_saves_nothing(tmp_path, answer, words):
    places, _ = make(tmp_path, answer=answer)
    with pytest.raises(ToolError, match=words):
        saved(places, "Gym", "12 Kostava St")
    assert places.items == [] and not (tmp_path / "places.json").exists()


def test_with_no_key_an_address_cant_be_looked_up_but_here_can_be_saved(tmp_path, monkeypatch, log_dir):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    places, _ = make(tmp_path, devices={"phone": Phone()}, key=None)
    with pytest.raises(ToolError, match="GOOGLE_MAPS_API_KEY"):
        saved(places, "Gym", "12 Kostava St")
    assert saved(places, "Gym", "here").startswith("Saved Gym")


@pytest.mark.parametrize("words", ["Gym", "gym", "the gym", "My  Gym", "the club"])
def test_known_by_any_of_his_names(tmp_path, words):
    places, _ = make(tmp_path, GYM)
    saved(places, "Gym", "12 Kostava St", names=["the club"])
    assert places.named(words)["name"] == "Gym"


@pytest.mark.parametrize("words", ["", "  ", "the", "the gym in Vake", "Work"])
def test_not_known_by_others(tmp_path, words):
    places, _ = make(tmp_path, GYM)
    saved(places, "Gym", "12 Kostava St")
    assert places.named(words) is None


def test_name_key():
    assert name_key("  The   GYM ") == "gym" and name_key("Theatre") == "theatre"


def test_saving_a_name_again_moves_it_and_keeps_the_rest(tmp_path):
    places, geocoding = make(tmp_path, GYM)
    saved(places, "Gym", "12 Kostava St", names=["the club"], radius=300)
    first = places.items[0]["id"]
    geocoding.answer = {"status": "OK", "results": [
        {"formatted_address": "New St", "geometry": {"location": {"lat": 41.0, "lng": 44.0}}}]}
    said = saved(places, "the gym", "New St")
    assert said.startswith("Moved Gym: New St.") and len(places.items) == 1
    assert places.items[0] == {"id": first, "name": "Gym", "names": ["the club"], "radius": 300, "lat": 41.0,
                               "lng": 44.0, "address": "New St"}


def test_names_and_radius_change_without_looking_anything_up(tmp_path):
    places, geocoding = make(tmp_path, GYM)
    saved(places, "Gym", "12 Kostava St")
    geocoding.asked.clear()
    assert saved(places, "Gym", names=["the gym", "the club"], radius=500).startswith("Changed Gym")
    assert places.items[0]["radius"] == 500 and places.items[0]["names"] == ["the club"]  # its own name isn't another
    assert geocoding.asked == []


def test_a_new_place_needs_where_a_name_and_a_name_of_its_own(tmp_path):
    places, _ = make(tmp_path, GYM)
    with pytest.raises(ToolError, match="Say where Gym is"):
        saved(places, "Gym")
    with pytest.raises(ToolError, match="needs a name"):
        saved(places, " ", "12 Kostava St")
    saved(places, "Gym", "12 Kostava St", names=["the club"])
    with pytest.raises(ToolError, match="Gym goes by one of those names"):
        saved(places, "Work", "1 Main St", names=["the club"])
    assert [item["name"] for item in places.items] == ["Gym"]


@pytest.mark.parametrize("radius", [50, 6000, "near"])
def test_a_radius_out_of_range_is_refused(tmp_path, radius):
    places, _ = make(tmp_path, GYM)
    with pytest.raises(ToolError, match="radius"):
        saved(places, "Gym", "12 Kostava St", radius=radius)
    assert places.items == []


def test_forget_and_list(tmp_path):
    places, _ = make(tmp_path, GYM)
    tools = {tool.name: tool for tool in places.tools()}
    assert run(tools["places_list"].run({})) == "He hasn't saved any places."
    saved(places, "Gym", "12 Kostava St", names=["the club"])
    assert run(tools["places_list"].run({})) == "- Gym (the club): Fitness Park, 12 Kostava St, Rustavi; within 150 m"
    with pytest.raises(ToolError, match="no place called"):
        run(tools["places_forget"].run({"name": "Work"}))
    assert run(tools["places_forget"].run({"name": "the club"})) == "Forgot Gym."
    assert places.items == [] and Places(tmp_path / "places.json", {}).items == []


def test_a_file_that_cant_be_read_or_with_a_bad_line_keeps_what_it_can(tmp_path):
    (tmp_path / "places.json").write_text("not json", encoding="utf-8")
    assert Places(tmp_path / "places.json", {}).items == []
    good = {"id": "pl1", "name": "Gym", "names": [], "lat": 1.0, "lng": 2.0, "radius": 150, "address": ""}
    (tmp_path / "places.json").write_text(json.dumps([good, {"id": "pl2"}, "x"]), encoding="utf-8")
    assert Places(tmp_path / "places.json", {}).items == [good]


# Known by name

def test_her_memory_block_lists_them(tmp_path):
    places, _ = make(tmp_path, GYM)
    memory = Memory(tmp_path / "memory", places)
    assert memory.prompt_section() == "Nothing yet."
    saved(places, "Gym", "12 Kostava St", names=["the club"])
    memory.remember("Tuxa's sister is Nino.")
    section = memory.prompt_section()
    assert "Tuxa's sister is Nino." in section
    assert "- Gym (the club): Fitness Park, 12 Kostava St, Rustavi" in section


def travel_with(tmp_path, *answers, home="12 Chavchavadze Ave, Tbilisi"):
    places, _ = make(tmp_path, GYM)
    saved(places, "Gym", "12 Kostava St", names=["the club"])
    routes = Routes(*answers)
    return Travel(key="maps-key", home=home, post=routes, places=places), routes, places


GYM_POINT = {"location": {"latLng": {"latitude": 41.54, "longitude": 45.0}}}


def test_the_travel_tools_take_his_names_to_their_points(tmp_path):
    travel, routes, _ = travel_with(tmp_path, DRIVE)
    run(travel.travel_time({"destination": "the club"}))
    body = routes.requests[0][1]
    assert body["destination"] == GYM_POINT and body["origin"] == {"address": "12 Chavchavadze Ave, Tbilisi"}


def test_a_place_that_isnt_his_goes_to_google_as_it_was_said(tmp_path):
    travel, routes, _ = travel_with(tmp_path, DRIVE)
    run(travel.travel_time({"destination": "Tbilisi airport", "origin": "the gym"}))
    body = routes.requests[0][1]
    assert body["destination"] == {"address": "Tbilisi airport"} and body["origin"] == GYM_POINT


def test_the_routes_words_say_his_name_and_the_link_its_point(tmp_path):
    travel, _, _ = travel_with(tmp_path, DRIVE)
    said = run(travel.travel_routes({"destination": "the gym", "mode": "drive"}))
    assert "to Gym, leaving now:" in said and "41.54" not in said.split("Google Maps:")[0]
    assert "destination=41.54%2C45.0" in said


def test_a_plan_goes_through_his_places(tmp_path):
    travel, routes, _ = travel_with(tmp_path, DRIVE)
    said = run(travel.travel_plan({"legs": [{"to": "the gym", "mode": "drive"}, {"to": "Vake Park", "mode": "drive"}]}))
    assert routes.requests[0][1]["destination"] == GYM_POINT
    assert routes.requests[1][1]["origin"] == GYM_POINT and routes.requests[1][1]["destination"] == {"address": "Vake Park"}
    assert "to Gym:" in said and "destination=41.54%2C45.0" in said


def test_home_once_saved_is_where_trips_start(tmp_path):
    travel, routes, places = travel_with(tmp_path, DRIVE)
    run(travel.travel_time({"destination": "the gym"}))
    assert routes.requests[-1][1]["origin"] == {"address": "12 Chavchavadze Ave, Tbilisi"}  # the fallback, until then
    places.items.append({"id": "pl9", "name": "Home", "names": [], "lat": 41.7, "lng": 44.8, "radius": 150, "address": ""})
    said = run(travel.travel_time({"destination": "the gym"}))
    assert routes.requests[-1][1]["origin"] == {"location": {"latLng": {"latitude": 41.7, "longitude": 44.8}}}
    assert "from home" in said
    run(travel.travel_time({"destination": "my house", "origin": "the gym"}))
    assert routes.requests[-1][1]["destination"] == {"location": {"latLng": {"latitude": 41.7, "longitude": 44.8}}}


def test_home_saved_with_no_home_address_set(tmp_path):
    travel, routes, places = travel_with(tmp_path, DRIVE, home="")
    with pytest.raises(ToolError, match="doesn't know where he is"):
        run(travel.travel_time({"destination": "the gym"}))
    places.items.append({"id": "pl9", "name": "Home", "names": [], "lat": 41.7, "lng": 44.8, "radius": 150, "address": ""})
    assert "from home" in run(travel.travel_time({"destination": "the gym"}))


@pytest.mark.parametrize("request_text", ["save where I am as the gym", "what places do you know", "how long to the gym",
                                          "mark this as Work", "save this place as Carrefour"])
def test_place_requests_are_personal(request_text):
    assert personal_or_risky(request_text)


# On a device

def test_a_device_that_checks_them_is_sent_them_and_each_change(tmp_path):
    phone = Phone()
    places, _ = make(tmp_path, GYM, devices={"phone": phone})
    places.connected(phone)
    assert phone.posted == [{"type": "place_rules", "places": []}]
    saved(places, "Gym", "12 Kostava St", names=["the club"])
    rule = {"id": places.items[0]["id"], "name": "Gym", "lat": 41.54, "lng": 45.0, "radius": 150}
    assert phone.posted[-1] == {"type": "place_rules", "places": [rule]}  # no address, names or anything else
    places.forget("Gym")
    assert phone.posted[-1] == {"type": "place_rules", "places": []}


def test_a_device_that_doesnt_check_them_isnt_sent_them(tmp_path):
    pc = Phone()
    pc.places = False
    places, _ = make(tmp_path, GYM, devices={"pc": pc})
    places.connected(pc)
    saved(places, "Gym", "12 Kostava St")
    assert pc.posted == []


def test_through_the_server_rules_go_out_and_events_are_logged_without_where(tmp_path, log_dir):
    places, _ = make(tmp_path, GYM)
    saved(places, "Gym", "12 Kostava St")
    agent = Agent(client=FakeClient())
    server = Server(agent, token="secret", features=[places])
    assert {"places_save", "places_list", "places_forget"} <= {tool.name for tool in agent.own_tools}
    id = places.items[0]["id"]

    async def device():
        async with serve(server.handle, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "hello", "device": "phone", "label": "phone", "token": "secret",
                                          "places": True}))
                while (data := json.loads(await asyncio.wait_for(ws.recv(), 5)))["type"] != "place_rules":
                    pass
                await ws.send(json.dumps({"type": "place_event", "id": id, "event": "arrived", "at": 1761000000000}))
                await ws.send(json.dumps({"type": "place_event", "id": "gone", "event": "arrived"}))
                await ws.send(json.dumps({"type": "place_event", "id": id, "event": "danced"}))
                await ws.send(json.dumps({"type": "chat_clear"}))
                while not any(entry.get("event") == "cleared" for entry in logged()):
                    await asyncio.sleep(0.01)
                return data

    def logged():
        return list(log.entries(f"{datetime.now():%Y-%m}"))

    rules = run(device())
    assert rules["places"][0]["name"] == "Gym"
    events = [entry for entry in logged() if entry["kind"] == "place" and entry["event"] != "saved"]
    assert len(events) == 1 and events[0]["place"] == "Gym" and events[0]["event"] == "arrived"
    assert events[0]["at"] == 1761000000000 and not {"lat", "lng", "address"} & set(events[0])
