"""His places, by name: Home, Work, the gym. He saves them by voice ("save where I am as the gym"), and she knows them
by what he calls them: her instructions list them (memory.py), and the travel tools take the names (travel.py). The
dashboard's Places page shows them on a map, to mark, move and delete (places_page.py).

What happens at a place is its users': a reminder (reminders.py) for words to say, a routine (routines.py) for something
to work out then, an action (place_actions.py) for one thing done whole, and a card (cards.py), which the phone shows
itself. Each user has happening(place id) -> what's set there, drop(id) -> whether it stopped one, place_gone(place id),
and place_event(place, event, at, late), and may have on_phone(place id) -> what place_rules should say of it. Late is
when the phone was offline: more than LATE_MINUTES. Each fires at most once in AGAIN_MINUTES.

A device that checks where it is is sent them (place_rules) and says only when it arrives at one or leaves it
(place_event; shared/PROTOCOL.md), so where he is otherwise stays on the device. Kept in places.json.

Addresses come from Google's Geocoding API, on the Routes API's key (GOOGLE_MAPS_API_KEY), which must have it enabled:
an address gives a place's point, and a point gives a place's address.
"""
import asyncio
import http.client
import io
import itertools
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse

from natro_agent import log
from natro_agent.tools import Tool, ToolError
from natro_agent.travel import COORDINATES, TIMEOUT_SECONDS, google_message

URL = "https://maps.googleapis.com/maps/api/geocode/json"
# Meters. The phone's location is rarely surer than the least of these, and a place's edge is where it's checked.
LEAST_RADIUS, USUAL_RADIUS, MOST_RADIUS = 100, 150, 5000
MOST_NAMES = 5
MOST_NAME_CHARS = 80
MOST_ADDRESS_CHARS = 300
ON = {"arrive": "arrived", "leave": "left"}
# What came later than this is too late for an action or a request; a reminder says when it was for.
LATE_MINUTES = 15
# So walking in and out doesn't repeat what happens at a place.
AGAIN_MINUTES = 60
HERE_WORDS = {"here", "where i am", "where he is", "my location", "this place"}
# How long his phone may take to say where it is.
PHONE_SECONDS = 30


def get(params, key):
    """Ask the Geocoding API (blocking), over IPv4, for the reason travel.post says."""
    parts = urllib.parse.urlsplit(URL)
    query = urllib.parse.urlencode({**params, "key": key})
    connection = http.client.HTTPSConnection(parts.hostname, timeout=TIMEOUT_SECONDS, source_address=("0.0.0.0", 0))
    try:
        connection.request("GET", f"{parts.path}?{query}")
        response = connection.getresponse()
        data = response.read()
        if response.status >= 400:
            raise urllib.error.HTTPError(URL, response.status, response.reason, response.headers, io.BytesIO(data))
        return json.loads(data)
    finally:
        connection.close()


def name_key(text):
    """A name as names are compared: "the Gym" and "my gym" are both "gym"."""
    return re.sub(r"^(?:the|my|his) ", "", " ".join(str(text or "").casefold().split()))


def meters(lat1, lng1, lat2, lng2):
    """Meters between two points on Earth."""
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def where_words(place, event):
    """"when you get to Gym", "when you leave Gym"."""
    return f"when you {'get to' if event == 'arrived' else 'leave'} {place['name']}"


def starting(words):
    """Words as a sentence starts them: its first letter a capital, and the rest as they are (a place's name)."""
    return words[:1].upper() + words[1:]


class Places:
    """path: the JSON file they're kept in. devices: name -> device, while connected (the agent's). key: Google's
    (default GOOGLE_MAPS_API_KEY). get: asks the Geocoding API (for tests). changed: called when they've changed
    (set by the Places page)."""

    def __init__(self, path, devices, key=None, get=get):
        self.path = path
        self.devices = devices
        self.key = key or os.environ.get("GOOGLE_MAPS_API_KEY")
        self._get = get
        self.changed = lambda: None
        self.users = []  # what happens at places (the module's docstring)
        self.listeners = []  # others to hear of his arriving and leaving: place_event's arguments
        self._ids = itertools.count(1)
        self.items = self._load()

    def named(self, text):
        """The place he calls this, or None."""
        wanted = name_key(text)
        if not wanted:
            return None
        return next((item for item in self.items
                     if wanted in (name_key(name) for name in (item["name"], *item["names"]))), None)

    def trigger(self, args):
        """(the place, "arrived" or "left") from a tool's place and on, or a ToolError for her to ask him."""
        wanted = " ".join(str(args.get("place") or "").split())
        item = self.named(wanted)
        if item is None:
            raise ToolError(f"He has no place called \"{wanted}\": ask him where it is, and save it with places_save.")
        on = args.get("on") or "arrive"
        if on not in ON:
            raise ToolError("on is arrive or leave.")
        return item, ON[on]

    def happening(self, place_id):
        """What's set to happen at a place, each {"id", "kind", "on", "every", "text"}."""
        return [each for user in self.users for each in user.happening(place_id)]

    def stop_happening(self, id):
        if not any(user.drop(id) for user in self.users):
            raise ToolError("That's gone.")

    def prompt_lines(self):
        """His places, for her instructions: "Gym (the gym): Fitness Park, 12 Kostava St, Rustavi"."""
        return [self._line(item) for item in self.items]

    @staticmethod
    def _line(item):
        others = f" ({', '.join(item['names'])})" if item["names"] else ""
        return f"{item['name']}{others}: {item['address'] or point(item)}"

    # Looking addresses up

    async def _geocoding(self, **params):
        """Google's answers, best first: {"address", "lat", "lng"}."""
        if not self.key:
            raise ToolError("Natro can't look addresses up: GOOGLE_MAPS_API_KEY isn't set.")
        try:
            data = await asyncio.to_thread(self._get, {**params, "language": "en"}, self.key)
        except urllib.error.HTTPError as e:
            raise ToolError(f"Google's Geocoding API said: {google_message(e)} (HTTP {e.code})")
        except (OSError, ValueError) as e:
            raise ToolError(f"Couldn't reach Google's Geocoding API: {e}")
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            raise ToolError(f"Google's Geocoding API said: {data.get('error_message') or data.get('status')}")
        return [{"address": result.get("formatted_address", ""), **result["geometry"]["location"]}
                for result in data.get("results") or []]

    async def search(self, text):
        """What Google finds for an address or a place's name."""
        return await self._geocoding(address=text)

    async def address_at(self, lat, lng):
        """The address at a point; "" when Google can't say, as a place doesn't need one."""
        try:
            found = await self._geocoding(latlng=f"{lat},{lng}")
        except ToolError as e:
            log.write("error", step="places", error=str(e)[:200])
            return ""
        return found[0]["address"] if found else ""

    async def _phone_is(self):
        """Where his phone is, as "lat,lng"."""
        phone = self.devices.get("phone")
        tool = next((tool for tool in phone.tools() if tool.name == "phone_location"), None) if phone else None
        if tool is None:
            raise ToolError("His phone is offline, so Natro can't tell where here is: ask him for the address.")
        try:
            said = str(await asyncio.wait_for(tool.run({}), PHONE_SECONDS))
        except asyncio.TimeoutError:
            raise ToolError("His phone didn't say where it is in time: ask him for the address.")
        found = COORDINATES.search(said)
        if not found:
            raise ToolError(f"His phone can't say where it is ({said[:100]}): ask him for the address.")
        return f"{found.group(1)},{found.group(2)}"

    async def _where(self, where):
        """(lat, lng, address) from what he said: "here", "lat,lng" or an address."""
        if where.casefold() in HERE_WORDS:
            where = await self._phone_is()
        found = COORDINATES.fullmatch(where)
        if found:
            lat, lng = self._coordinates(*found.groups())
            return lat, lng, await self.address_at(lat, lng)
        results = await self.search(where)
        if not results:
            raise ToolError(f"Google found nothing for \"{where}\": ask him for the address, with its city.")
        return results[0]["lat"], results[0]["lng"], results[0]["address"]

    # Saving

    async def save(self, name, where="", names=None, radius=None):
        """Saves a place, or (its name saved already) changes it; what to say about it."""
        where = " ".join(str(where or "").split())
        item = self.named(name)
        name, names = self._clean(item, name, names)
        if radius is not None:
            radius = self._radius(radius)
        if item is None and not where:
            raise ToolError(f"Say where {name} is: \"here\" or an address.")
        moved = item is not None and bool(where)
        lat, lng, address = await self._where(where) if where else (None, None, None)
        if item is None:
            item = self._new(name)
        if where:
            item.update(lat=lat, lng=lng, address=address)
        if names:
            item["names"] = list(dict.fromkeys([*item["names"], *names]))[:MOST_NAMES]
        if radius is not None:
            item["radius"] = radius
        self._changed()
        log.write("place", event="saved", place=item["name"])
        said = "Moved" if moved else "Saved" if where else "Changed"
        return f"{said} {item['name']}: {item['address'] or point(item)}.\n(Within {item['radius']} m of there counts as there.)"

    async def mark(self, id, name, names, lat, lng, radius, address=""):
        """A place as the dashboard's map marks it, all of it given (id: the one it changes; none: a new one).
        Returns it. When it's new or has moved, its address is this (what Google found there), else Google's for
        the point."""
        item = self._by_id(id) if id else None
        if id and item is None:
            raise ToolError("That place is gone.")
        name, names = self._clean(item, name, names)
        lat, lng = self._coordinates(lat, lng)
        radius = self._radius(radius)
        moved = item is None or (item["lat"], item["lng"]) != (lat, lng)
        address = " ".join(str(address or "").split())[:MOST_ADDRESS_CHARS]
        address = (address or await self.address_at(lat, lng)) if moved else item["address"]
        if item is None:
            item = self._new(name)
        item.update(name=name, names=names, lat=lat, lng=lng, radius=radius, address=address)
        self._changed()
        log.write("place", event="saved", place=name)
        return item

    def forget(self, name):
        item = self.named(name)
        if item is None:
            raise ToolError(f"He has no place called \"{name}\".")
        there = len(self.happening(item["id"]))
        self._drop(item)
        also = f", and {'what was' if there == 1 else f'the {there} things'} set to happen there" if there else ""
        return f"Forgot {item['name']}{also}."

    def remove(self, id):
        item = self._by_id(id)
        if item is None:
            raise ToolError("That place is gone.")
        self._drop(item)

    def _drop(self, item):
        self.items.remove(item)
        for user in self.users:
            user.place_gone(item["id"])
        self._changed()
        log.write("place", event="forgotten", place=item["name"])

    def _by_id(self, id):
        return next((item for item in self.items if item["id"] == id), None)

    def _new(self, name):
        item = {"id": f"pl{int(time.time() * 1000):x}{next(self._ids)}", "name": name, "names": [],
                "radius": USUAL_RADIUS}
        self.items.append(item)
        return item

    def _clean(self, item, name, names):
        """(name, other names) as they're kept, for this place (None: a new one), unless another goes by one."""
        name = " ".join(str(name or "").split())[:MOST_NAME_CHARS]
        if not name:
            raise ToolError("The place needs a name.")
        names = self._other_names(name, names)
        for other in (self.named(each) for each in (name, *names)):
            if other is not None and other is not item:
                raise ToolError(f"{other['name']} goes by one of those names already.")
        return name, names

    @staticmethod
    def _other_names(name, names):
        names = [names] if isinstance(names, str) else names if isinstance(names, list) else []
        cleaned = {name_key(each): " ".join(str(each).split())[:MOST_NAME_CHARS] for each in names if name_key(each)}
        cleaned.pop(name_key(name), None)
        return list(cleaned.values())[:MOST_NAMES]

    @staticmethod
    def _coordinates(lat, lng):
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            raise ToolError("That isn't a place on Earth.")
        if not (abs(lat) <= 90 and abs(lng) <= 180):
            raise ToolError("That isn't a place on Earth.")
        return lat, lng

    @staticmethod
    def _radius(value):
        try:
            value = round(float(value))
        except (TypeError, ValueError):
            raise ToolError("radius is a number of meters.")
        if not LEAST_RADIUS <= value <= MOST_RADIUS:
            raise ToolError(f"radius is from {LEAST_RADIUS} to {MOST_RADIUS} meters.")
        return value

    def _changed(self):
        self._save()
        self.refresh()

    def refresh(self):
        """What's sent of them changed: the devices and the Places page hear of it."""
        for device in list(self.devices.values()):
            self.connected(device)
        self.changed()

    # Devices

    def rules(self):
        rules = []
        for item in self.items:
            rule = {key: item[key] for key in ("id", "name", "lat", "lng", "radius")}
            cards = [each for user in self.users if hasattr(user, "on_phone") for each in user.on_phone(item["id"])]
            rules.append({**rule, "cards": cards} if cards else rule)
        return rules

    def connected(self, device):
        """A device that checks where it is connected (or they changed): the places, all of them."""
        if getattr(device, "places", False):
            device.post(type="place_rules", places=self.rules())

    def messages(self):
        return {"place_event": self._event}

    def _event(self, device, data):
        """The device arrived at a place or left it. Logged with its name and when, and nothing of where he is."""
        item = next((item for item in self.items if item["id"] == str(data.get("id") or "")), None)
        event, at = data.get("event"), data.get("at")
        if item is None or event not in ON.values():
            return  # forgotten meanwhile
        now = int(time.time() * 1000)
        at = int(min(at, now)) if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0 else now
        log.write("place", event=event, place=item["name"], device=device.name, at=at)
        for hear in [user.place_event for user in self.users] + self.listeners:
            try:
                hear(item, event, at, now - at > LATE_MINUTES * 60 * 1000)
            except Exception as e:  # one must not stop the others, or the phone's connection
                log.write("error", step="places", error=repr(e))

    # On disk

    def _load(self):
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
            return [item for item in items if isinstance(item, dict)
                    and all(key in item for key in ("id", "name", "names", "lat", "lng", "radius", "address"))]
        except (OSError, ValueError):
            return []

    def _save(self):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        partial.replace(self.path)

    # Her tools

    def tools(self):
        async def save(args):
            return await self.save(args.get("name"), args.get("where"), args.get("names"), args.get("radius"))

        async def show(args):
            if not self.items:
                return "He hasn't saved any places."
            lines = []
            for item in self.items:
                lines.append(f"- {self._line(item)}; within {item['radius']} m")
                lines += [f"  {starting(where_words(item, each['on']))}, {'every time' if each['every'] else 'once'}: "
                          f"{each['kind']} {each['id']}: {each['text']}" for each in self.happening(item["id"])]
            return "\n".join(lines)

        async def forget(args):
            return self.forget(args.get("name"))

        return [
            Tool("places_save",
                 "Save one of his places under the name he calls it, or change one: \"save where I am as the gym\", "
                 "\"Work is at 12 Kostava St, Tbilisi\", \"the office is Work too\". Saving a name he has already "
                 "moves it there.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "What he calls it, capitalized: \"Gym\"."},
                     "where": {"type": "string",
                               "description": "\"here\" (where his phone is), or an address with its city, or "
                                              "\"lat,lng\". Leave it out to change only its names or radius."},
                     "names": {"type": "array", "items": {"type": "string"},
                               "description": "Other names he uses for it (\"the office\")."},
                     "radius": {"type": "integer", "description": f"How near counts as there, in meters, "
                                                                  f"{LEAST_RADIUS} to {MOST_RADIUS}: only if he "
                                                                  f"says (default {USUAL_RADIUS})."},
                 }, "required": ["name"]},
                 save, "natro", changes=True, final=True),
            Tool("places_list",
                 "His saved places, with their names and addresses, how near counts as there, and what's set to "
                 "happen at each (with ids, for reminders_cancel, routines_cancel or place_action_cancel). For \"what "
                 "places do you know?\", or when you need more than your instructions say of them.",
                 {"type": "object", "properties": {}},
                 show, "natro"),
            Tool("places_forget",
                 "Forget one of his saved places, when he asks you to, and what's set to happen there.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "What he calls it."},
                 }, "required": ["name"]},
                 forget, "natro", confirm=True),
        ]


def point(item):
    """A place as "lat,lng", which the Routes API and Google Maps take as a place."""
    return f"{item['lat']},{item['lng']}"
