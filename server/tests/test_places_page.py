"""The dashboard's Places page: its own policy, who may open its socket, and saving over it (Google's Geocoding API
faked, the map's scripts not loaded: the page's own script is tried by hand)."""
import asyncio
import json
import re
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from conftest import FakeClient, run
from natro_agent.agent import Agent
from natro_agent.dashboard import Dashboard, Logs
from natro_agent.places import Places
from natro_agent.places_page import PlacesPage
from natro_agent.server import Server, health
from test_places import GYM, Geocoding, Phone


def setting_up(tmp_path, *results, key="map-key", devices=None, answer=None):
    geocoding = Geocoding(*results, answer=answer)
    places = Places(tmp_path / "places.json", {} if devices is None else devices, key="maps-key", get=geocoding)
    server = Server(Agent(client=FakeClient()), token="secret")
    return Dashboard(server, "127.0.0.1", Logs(tmp_path), places=PlacesPage(places, key=key)), places, geocoding


async def serving(dashboard, play):
    async with serve(dashboard.websocket, "127.0.0.1", 0,
                     process_request=lambda c, r: health(c, r) or dashboard.http(c, r)) as listener:
        return await play(listener.sockets[0].getsockname()[1])


async def get(port, path, host=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host} if host else {})

    def go():
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers, response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode("utf-8")
    return await asyncio.to_thread(go)


def page_socket(port, origin=None):
    return connect(f"ws://127.0.0.1:{port}/dashboard/socket", origin=f"http://127.0.0.1:{port}" if origin is None else origin)


async def take(ws, kind):
    while True:
        data = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if data["type"] == kind:
            return data


async def say(ws, **message):
    await ws.send(json.dumps(message))


GOOD = {"name": "Gym", "names": ["the club"], "lat": 41.54, "lng": 45.0, "radius": 200}


# The page

def test_the_page_has_a_policy_of_its_own_with_a_new_nonce_each_time(tmp_path):
    dashboard, _, _ = setting_up(tmp_path)

    async def play(port):
        return (await get(port, "/dashboard/places"), await get(port, "/dashboard/places"), await get(port, "/dashboard"),
                await get(port, "/dashboard/places.js"), await get(port, "/dashboard/places.css"))

    first, second, other, script, style = run(serving(dashboard, play))
    assert first[0] == 200 and first[1]["Content-Type"].startswith("text/html")
    policy = first[1]["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([\w-]+)'", policy).group(1)
    assert f'src="/dashboard/places.js" nonce="{nonce}"' in first[2] and f'href="/dashboard/style.css" nonce="{nonce}"' in first[2]
    assert "{{" not in first[2] and nonce not in second[2]
    assert "'strict-dynamic'" in policy and "frame-ancestors 'none'" in policy and "'unsafe-inline'" not in policy
    assert first[1]["Referrer-Policy"] == "strict-origin-when-cross-origin" and first[1]["Cache-Control"] == "no-store"
    assert '<meta name="maps-key" content="map-key">' in first[2]
    # Every other page keeps the dashboard's.
    assert "script-src 'self'" in other[1]["Content-Security-Policy"] and "google" not in other[1]["Content-Security-Policy"]
    assert other[1]["Referrer-Policy"] == "no-referrer" and "/dashboard/places" in other[2]
    assert script[0] == 200 and script[1]["Content-Type"].startswith("text/javascript")
    assert style[0] == 200 and style[1]["Content-Type"].startswith("text/css")


def test_a_key_is_escaped_and_with_none_the_page_has_none(tmp_path):
    dashboard, _, _ = setting_up(tmp_path, key='a"><script>')
    assert '&quot;&gt;&lt;script&gt;' in run(serving(dashboard, lambda port: get(port, "/dashboard/places")))[2]
    dashboard, _, _ = setting_up(tmp_path, key="")
    assert '<meta name="maps-key" content="">' in run(serving(dashboard, lambda port: get(port, "/dashboard/places")))[2]


def test_another_host_gets_neither_the_page_nor_the_socket(tmp_path):
    dashboard, _, _ = setting_up(tmp_path)

    async def play(port):
        page = await get(port, "/dashboard/places", host=f"natro.attacker.example:{port}")
        with pytest.raises(InvalidStatus) as refused:
            # A site that points a name of its own at the address: its Origin and Host agree.
            async with connect(f"ws://natro.attacker.example:{port}/dashboard/socket", host="127.0.0.1", port=port,
                               origin=f"http://natro.attacker.example:{port}"):
                pass
        return page, refused.value.response.status_code

    page, status = run(serving(dashboard, play))
    assert page[0] == 403 and status == 403


def test_without_places_the_dashboard_has_neither(tmp_path):
    dashboard = Dashboard(Server(Agent(client=FakeClient()), token="secret"), "127.0.0.1", Logs(tmp_path))

    async def play(port):
        return (await get(port, "/dashboard/places"))[0], (await get(port, "/dashboard/socket"))[0]

    assert run(serving(dashboard, play)) == (404, 404)


# Who may open the socket

@pytest.mark.parametrize("origin", ["https://attacker.example", "http://127.0.0.1:1", "null", "file://", ""])
def test_only_the_dashboards_own_page_opens_the_socket(tmp_path, origin):
    dashboard, _, _ = setting_up(tmp_path)

    async def play(port):
        with pytest.raises(InvalidStatus) as refused:
            async with page_socket(port, origin):
                pass
        return refused.value.response.status_code

    assert run(serving(dashboard, play)) == 403


def test_with_no_origin_at_all_the_socket_is_refused(tmp_path):
    dashboard, _, _ = setting_up(tmp_path)

    async def play(port):
        with pytest.raises(InvalidStatus) as refused:
            async with connect(f"ws://127.0.0.1:{port}/dashboard/socket"):
                pass
        return refused.value.response.status_code

    assert run(serving(dashboard, play)) == 403


def test_the_devices_websocket_is_as_it_was_beside_it(tmp_path):
    dashboard, _, _ = setting_up(tmp_path)

    async def play(port):
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await say(ws, type="hello", device="pc", token="secret")
            return json.loads(await ws.recv())["type"]

    assert run(serving(dashboard, play)) == "welcome"


# Saving

def test_a_page_gets_them_all_when_it_opens(tmp_path):
    dashboard, places, _ = setting_up(tmp_path, GYM)
    run(places.save("Gym", "12 Kostava St", names=["the club"]))

    async def play(port):
        async with page_socket(port) as ws:
            return await take(ws, "places")

    shown = run(serving(dashboard, play))["places"]
    assert [(item["name"], item["names"], item["address"]) for item in shown] == [
        ("Gym", ["the club"], "Fitness Park, 12 Kostava St, Rustavi")]


def test_saving_a_new_place_looks_its_address_up_and_tells_every_open_page_and_the_phone(tmp_path):
    phone = Phone()
    dashboard, places, geocoding = setting_up(tmp_path, GYM, devices={"phone": phone})

    async def play(port):
        async with page_socket(port) as first, page_socket(port) as second:
            await take(first, "places")
            await take(second, "places")
            await say(first, type="place_save", **GOOD)
            return await take(first, "place_saved"), await take(second, "places")

    done, seen = run(serving(dashboard, play))
    item = places.items[0]
    assert done["id"] == item["id"] and done["name"] == "Gym"
    assert seen["places"] == [{**item, "happens": []}] and item["address"] == "Fitness Park, 12 Kostava St, Rustavi"
    assert (item["lat"], item["lng"], item["radius"], item["names"]) == (41.54, 45.0, 200, ["the club"])
    assert geocoding.asked == [{"latlng": "41.54,45.0", "language": "en"}]
    assert phone.posted[-1]["type"] == "place_rules" and phone.posted[-1]["places"][0]["id"] == item["id"]
    assert json.loads((tmp_path / "places.json").read_text(encoding="utf-8")) == [item]


def test_an_address_the_search_found_is_kept_rather_than_looked_up(tmp_path):
    dashboard, places, geocoding = setting_up(tmp_path, GYM)

    async def play(port):
        async with page_socket(port) as ws:
            await say(ws, type="place_save", **GOOD, address="Fitness Park, 12 Kostava St, Rustavi")
            await take(ws, "place_saved")

    run(serving(dashboard, play))
    assert places.items[0]["address"] == "Fitness Park, 12 Kostava St, Rustavi" and geocoding.asked == []


def test_saving_it_again_only_looks_it_up_when_it_moved(tmp_path):
    dashboard, places, geocoding = setting_up(tmp_path, GYM)
    run(places.save("Gym", "12 Kostava St", names=["the club"]))
    id = places.items[0]["id"]
    geocoding.asked.clear()

    async def play(port):
        async with page_socket(port) as ws:
            await say(ws, type="place_save", id=id, name="Fitness", names=[], lat=41.54, lng=45.0, radius=300)
            await take(ws, "place_saved")
            asked_then = list(geocoding.asked)
            await say(ws, type="place_save", id=id, name="Fitness", names=[], lat=41.6, lng=45.1, radius=300)
            await take(ws, "place_saved")
            return asked_then

    assert run(serving(dashboard, play)) == []
    assert geocoding.asked == [{"latlng": "41.6,45.1", "language": "en"}]
    assert len(places.items) == 1
    assert (places.items[0]["id"], places.items[0]["name"], places.items[0]["names"]) == (id, "Fitness", [])


@pytest.mark.parametrize("change, words", [
    ({"name": " "}, "needs a name"),
    ({"name": "Work"}, "Work goes by one of those names"),
    ({"names": ["work"]}, "Work goes by one of those names"),
    ({"lat": "north"}, "isn't a place on Earth"),
    ({"lat": 95}, "isn't a place on Earth"),
    ({"lng": None}, "isn't a place on Earth"),
    ({"radius": 20}, "radius is from"),
    ({"id": "gone"}, "That place is gone"),
])
def test_what_cant_be_saved_is_said_and_changes_nothing(tmp_path, change, words):
    dashboard, places, geocoding = setting_up(tmp_path, GYM)
    run(places.save("Work", "1 Main St"))
    before = json.dumps(places.items)

    async def play(port):
        async with page_socket(port) as ws:
            await say(ws, type="place_save", **{**GOOD, **change})
            return await take(ws, "place_error")

    said = run(serving(dashboard, play))
    assert said["for"] == "place_save" and words in said["message"]
    assert json.dumps(places.items) == before


def test_no_address_from_google_still_saves(tmp_path, log_dir):
    dashboard, places, _ = setting_up(tmp_path, answer={"status": "REQUEST_DENIED", "error_message": "Off."})

    async def play(port):
        async with page_socket(port) as ws:
            await say(ws, type="place_save", **GOOD)
            return await take(ws, "place_saved")

    run(serving(dashboard, play))
    assert places.items[0]["address"] == ""


def test_deleting_tells_the_pages_and_a_gone_one_is_said(tmp_path):
    dashboard, places, _ = setting_up(tmp_path, GYM)
    run(places.save("Gym", "12 Kostava St"))
    id = places.items[0]["id"]

    async def play(port):
        async with page_socket(port) as ws:
            await take(ws, "places")
            await say(ws, type="place_delete", id=id)
            shown = await take(ws, "places")
            await say(ws, type="place_delete", id=id)
            return shown, await take(ws, "place_error")

    shown, said = run(serving(dashboard, play))
    assert shown["places"] == [] and places.items == [] and "gone" in said["message"]


def test_a_place_saved_by_voice_shows_on_an_open_page(tmp_path):
    dashboard, places, _ = setting_up(tmp_path, GYM)

    async def play(port):
        async with page_socket(port) as ws:
            await take(ws, "places")
            await places.save("Gym", "12 Kostava St")
            return await take(ws, "places")

    assert run(serving(dashboard, play))["places"][0]["name"] == "Gym"


def test_searching_finds_what_google_finds_and_says_when_it_cant(tmp_path):
    dashboard, _, geocoding = setting_up(tmp_path, GYM)

    async def play(port):
        async with page_socket(port) as ws:
            await say(ws, type="place_search", text="  Fitness   Park ")
            found = await take(ws, "place_found")
            geocoding.answer = {"status": "REQUEST_DENIED", "error_message": "This API is not activated."}
            await say(ws, type="place_search", text="Fitness Park")
            refused = await take(ws, "place_error")
            await say(ws, type="place_search", text="   ")
            return found, refused, await take(ws, "place_found")

    found, refused, nothing = run(serving(dashboard, play))
    assert found["text"] == "Fitness Park" and geocoding.asked[0] == {"address": "Fitness Park", "language": "en"}
    assert found["found"] == [{"address": "Fitness Park, 12 Kostava St, Rustavi", "lat": 41.54, "lng": 45.0}]
    assert refused["for"] == "place_search" and "not activated" in refused["message"]
    assert nothing["found"] == []


def test_what_isnt_a_message_is_passed_over(tmp_path):
    dashboard, _, _ = setting_up(tmp_path)

    async def play(port):
        async with page_socket(port) as ws:
            await take(ws, "places")
            await ws.send("not json")
            await ws.send("[1, 2]")
            await say(ws, type="something_else")
            await say(ws, type="place_search", text="")
            return await take(ws, "place_found")

    assert run(serving(dashboard, play))["found"] == []
