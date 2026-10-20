"""Getting places with Google's Routes API: how long a trip takes, the ways to make it compared, and his own route
leg by leg. By car with today's traffic, by public transport (its lines, stops and times), on foot or by bike.
https://developers.google.com/maps/documentation/routes

Offered once GOOGLE_MAPS_API_KEY is set (a key for the Routes API, in the same Google Cloud project; the free
monthly allowance covers personal use). Trips start from NATRO_HOME_ADDRESS unless he says where from ("from
here" on the phone is where it is: its location tool says "lat,lng", which the Routes API takes as a place), and
go by NATRO_TRAVEL_MODE (default drive) unless he says how. Places are personal (where he lives, where he goes),
so only Claude uses it. The meetings watcher uses it too, for when to leave (watchers.py).

- travel_time: how long, the quick answer.
- travel_routes: up to five of Google's ways, compared, each step by step (walks, lines, stops, times, changes);
  the same lines at later times are one way. The kinds of transport he asks for are only a preference to
  Google: from Rustavi it keeps to buses even when asked for the metro.
- travel_plan: his own route, leg by leg ("metro to Station Square, then the bus"), each leg leaving when he
  gets there; with arrive_by it's worked out back from the end. Google's quickest way straight there follows, so
  he hears what his way costs.
Both end with Google Maps links, for following a route on his phone: its open_maps tool opens one in Google Maps,
or starts navigating it.
"""
import asyncio
import http.client
import io
import itertools
import json
import os
import re
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

from natro_agent.config import timezone
from natro_agent.tools import Tool, ToolError

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELDS = ("routes.duration,routes.staticDuration,routes.distanceMeters,routes.description,routes.localizedValues,"
          "routes.legs.steps.travelMode,routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,"
          "routes.legs.steps.navigationInstruction,routes.legs.steps.transitDetails")
TIMEOUT_SECONDS = 20
MODES = {"drive": "DRIVE", "transit": "TRANSIT", "walk": "WALK", "bicycle": "BICYCLE"}
MODE_WORDS = {"drive": "by car", "transit": "by public transport", "walk": "on foot", "bicycle": "by bike"}
MAPS_MODES = {"drive": "driving", "transit": "transit", "walk": "walking", "bicycle": "bicycling"}
# The kinds of transport he can ask for, as Google's transitPreferences name them.
KINDS = {"bus": ["BUS"], "metro": ["SUBWAY"], "train": ["TRAIN", "RAIL"], "tram": ["LIGHT_RAIL"]}
# Google's vehicle types: what a line is called, and the kind he'd ask for.
VEHICLES = {"BUS": ("Bus", "bus"), "INTERCITY_BUS": ("Bus", "bus"), "TROLLEYBUS": ("Trolleybus", "bus"),
            "SHARE_TAXI": ("Minibus", "bus"), "SUBWAY": ("Metro", "metro"), "METRO_RAIL": ("Metro", "metro"),
            "TRAM": ("Tram", "tram"), "RAIL": ("Train", "train"), "HEAVY_RAIL": ("Train", "train"),
            "COMMUTER_TRAIN": ("Train", "train"), "HIGH_SPEED_TRAIN": ("Train", "train"),
            "LONG_DISTANCE_TRAIN": ("Train", "train"), "MONORAIL": ("Monorail", "train"),
            "CABLE_CAR": ("Cable car", ""), "GONDOLA_LIFT": ("Cable car", ""), "FUNICULAR": ("Funicular", ""),
            "FERRY": ("Ferry", "")}
PREFER = {"fewer_changes": "FEWER_TRANSFERS", "less_walking": "LESS_WALKING"}
MOST_ROUTES = 5
MOST_LEGS = 5
# Turn-by-turn lines kept for one route or walk.
MOST_TURNS = 40
# What he calls his home: NATRO_HOME_ADDRESS (Google would look for a place called "home").
HOME_WORDS = {"home", "my home", "my house", "his home", "the house", "house"}
# A place given as "lat,lng", as the phone's location tool says where it is: "41.69339,44.80153".
COORDINATES = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def post(url, body, key):
    """POST JSON to the Routes API (blocking), over IPv4: the key may be limited to the server's IPv4 address,
    and the server would otherwise call from its IPv6 one."""
    parts = urllib.parse.urlsplit(url)
    # Bound to an IPv4 address, an IPv6 socket fails and the connection goes on to the host's IPv4 address.
    connection = http.client.HTTPSConnection(parts.hostname, timeout=TIMEOUT_SECONDS, source_address=("0.0.0.0", 0))
    try:
        connection.request("POST", parts.path, body=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS})
        response = connection.getresponse()
        data = response.read()
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(data))
        return json.loads(data)
    finally:
        connection.close()


def seconds(value):
    """Google's "1234s" as a number."""
    return float(str(value or "0s").rstrip("s") or 0)


def minutes_words(minutes):
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {rest} minutes" if rest else "")


def distance_words(meters):
    return f"{round(meters)} m" if meters < 1000 else f"{meters / 1000:.1f} km"


def instant(value):
    """Google's "2026-10-19T08:47:51Z" in his time zone; None without one."""
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone())
    except ValueError:
        return None


def clock(moment, now=None):
    """15:07, with the day when it isn't today: "07:30 tomorrow", "07:30 on Friday 23 October"."""
    days = (moment.date() - (now or datetime.now(timezone())).date()).days
    if days == 0:
        return f"{moment:%H:%M}"
    return f"{moment:%H:%M} tomorrow" if days == 1 else f"{moment:%H:%M} on {moment:%A %d %B}"


def leave_and_arrive(leaves, arrives):
    """"leave 15:03 tomorrow, arrive 16:32": the day once."""
    return f"leave {clock(leaves)}, arrive " + (f"{arrives:%H:%M}" if arrives.date() == leaves.date() else clock(arrives))


def waypoint(place):
    """A place as the Routes API takes it: "lat,lng" (where his phone is) as a location, anything else as an
    address."""
    found = COORDINATES.fullmatch(place)
    if found:
        latitude, longitude = map(float, found.groups())
        if abs(latitude) <= 90 and abs(longitude) <= 180:
            return {"location": {"latLng": {"latitude": latitude, "longitude": longitude}}}
    return {"address": place}


def maps_link(origin, destination, mode):
    """Google Maps with these directions: on his phone it opens the Maps app, to follow them there."""
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(
        {"api": "1", "origin": origin, "destination": destination, "travelmode": MAPS_MODES[mode]})


def ride(step):
    """A step on public transport: its line, kind, stops, times, how many stops and how often it goes."""
    details = step["transitDetails"]
    line = details.get("transitLine", {})
    vehicle = line.get("vehicle", {})
    word = vehicle.get("name", {}).get("text") or "Transit"
    word, kind = VEHICLES.get(vehicle.get("type"), (word, word.lower() if word.lower() in KINDS else ""))
    stops = details.get("stopDetails", {})
    local = details.get("localizedValues", {})
    found = {"line": " ".join(part for part in (word, line.get("nameShort") or line.get("name") or "") if part),
             "kind": kind, "towards": details.get("headsign", ""),
             "from": stops.get("departureStop", {}).get("name", "?"), "to": stops.get("arrivalStop", {}).get("name", "?"),
             "stops": details.get("stopCount") or 0, "every": round(seconds(details.get("headway")) / 60)}
    for end in ("departure", "arrival"):
        moment = instant(stops.get(f"{end}Time"))
        found[end] = moment
        # Google's own words for the time are "8:11 PM"; the clock is 20:11.
        found[f"{end}_text"] = f"{moment:%H:%M}" if moment else local.get(f"{end}Time", {}).get("time", {}).get("text", "")
    return found


def transit_steps(route):
    """The lines to take: "Metro 1 from Rustaveli at 15:02 to Station Square (4 stops)"."""
    said = []
    for step in (step for leg in route.get("legs", []) for step in leg.get("steps", [])):
        if not step.get("transitDetails"):
            continue
        taken = ride(step)
        text = f"{taken['line']} from {taken['from']}"
        text += f" at {taken['departure_text']}" if taken["departure_text"] else ""
        text += f" to {taken['to']}"
        if taken["stops"]:
            text += f" ({taken['stops']} stops)"
        said.append(text)
    return said


def ride_words(taken):
    """"Bus 23 towards Tbilisi: from Post Office at 12:47 to Gulia Square at 13:20, 13 stops, every 10 minutes"."""
    text = taken["line"] + (f" towards {taken['towards']}" if taken["towards"] else "")
    text += f": from {taken['from']}" + (f" at {taken['departure_text']}" if taken["departure_text"] else "")
    text += f" to {taken['to']}" + (f" at {taken['arrival_text']}" if taken["arrival_text"] else "")
    if taken["stops"]:
        text += f", {taken['stops']} stop{'s' if taken['stops'] != 1 else ''}"
    if taken["every"]:
        text += f", every {minutes_words(taken['every'])}"
    return text


def instruction(step):
    """A turn, with how far it goes: "Turn left onto Ivane Machabeli St (381 m)"."""
    words = " ".join((step.get("navigationInstruction") or {}).get("instructions", "").split())
    meters = step.get("distanceMeters") or 0
    return f"{words} ({distance_words(meters)})" if words and meters else words


def turns_of(steps):
    said = [line for line in map(instruction, steps) if line]
    if len(said) > MOST_TURNS:
        said = said[:MOST_TURNS] + [f"… and {len(said) - MOST_TURNS} more."]
    return said


def parts(steps, mode, destination, turns):
    """A route step by step, as lines: by public transport each walk (to the next stop, or the end) and each
    ride; otherwise nothing but the turns, if he wants them. Turns are indented under their walk."""
    if mode != "transit":
        return turns_of(steps) if turns else []
    said, walking = [], []

    def walked(to):
        minutes = round(sum(seconds(step.get("staticDuration")) for step in walking) / 60)
        if minutes:
            meters = sum(step.get("distanceMeters") or 0 for step in walking)
            said.append(f"Walk {minutes_words(minutes)} ({distance_words(meters)}) to {to}.")
            said.extend(f"  {line}" for line in (turns_of(walking) if turns else []))
        walking.clear()

    for step in steps:
        if step.get("transitDetails"):
            taken = ride(step)
            walked(taken["from"])
            said.append(ride_words(taken) + ".")
        else:
            walking.append(step)
    walked(destination)
    return said


def summary(route, mode, destination, start, arrive=None, turns=False):
    """One of Google's routes, as travel_routes and travel_plan tell it: when he leaves and arrives, how long, its
    rides, walking, fare and steps. start: when he'd set off (now, or what he asked for)."""
    steps = [step for leg in route.get("legs", []) for step in leg.get("steps", [])]
    rides = [ride(step) for step in steps if step.get("transitDetails")]
    total = seconds(route.get("duration"))
    found = {"minutes": max(1, round(total / 60)),
             "usual": max(1, round(seconds(route.get("staticDuration") or route.get("duration")) / 60)),
             "km": round((route.get("distanceMeters") or 0) / 1000, 1), "via": route.get("description") or "",
             "fare": (route.get("localizedValues") or {}).get("transitFare", {}).get("text", ""),
             "walk": round(sum(seconds(step.get("staticDuration")) for step in steps
                               if step.get("travelMode") == "WALK") / 60) if mode == "transit" else 0,
             "rides": rides, "parts": parts(steps, mode, destination, turns),
             "signature": tuple(taken["line"] for taken in rides), "also": []}
    if rides and rides[0]["departure"] and rides[-1]["arrival"]:
        # A trip by public transport leaves when the walk to its first stop starts, and ends after the last walk.
        def walk_until_a_ride(in_order):
            return sum(seconds(step.get("staticDuration"))
                       for step in itertools.takewhile(lambda step: not step.get("transitDetails"), in_order))
        found["leaves"] = rides[0]["departure"] - timedelta(seconds=walk_until_a_ride(steps))
        found["arrives"] = rides[-1]["arrival"] + timedelta(seconds=walk_until_a_ride(reversed(steps)))
        found["minutes"] = max(1, round((found["arrives"] - found["leaves"]).total_seconds() / 60))
    elif arrive:
        found["leaves"], found["arrives"] = arrive - timedelta(seconds=total), arrive
    else:
        found["leaves"], found["arrives"] = start, start + timedelta(seconds=total)
    return found


def route_line(found, mode):
    """A route's first line: how long, when he leaves and arrives, and how."""
    times = leave_and_arrive(found["leaves"], found["arrives"])
    if mode == "transit":
        how = ", then ".join(taken["line"] for taken in found["rides"]) or "on foot all the way"
        more = [f"{minutes_words(found['walk'])} on foot"] if found["walk"] and found["rides"] else []
        changes = len(found["rides"]) - 1
        if changes > 0:
            more.append(f"{changes} change{'s' if changes > 1 else ''}")
        more.append(f"{found['km']} km")
        if found["fare"]:
            more.append(f"fare {found['fare']}")
        return f"{minutes_words(found['minutes'])}, {times}: {how}; {', '.join(more)}."
    via = f" via {found['via']}" if found["via"] else ""
    traffic = (f", traffic adds {found['minutes'] - found['usual']} minutes"
               if mode == "drive" and found["minutes"] - found["usual"] >= 5 else "")
    return f"{minutes_words(found['minutes'])}{via}, {found['km']} km{traffic}: {times}."


def kinds_words(use):
    return " or ".join(use)


def moment(args, name):
    """A time he gave (his local time, "2026-10-18T15:00"), or None."""
    if not args.get(name):
        return None
    try:
        value = datetime.fromisoformat(str(args[name]))
    except ValueError:
        raise ToolError(f"{name} is a time like 2026-10-18T15:00.")
    return value if value.tzinfo else value.replace(tzinfo=timezone())


class Travel:
    def __init__(self, key=None, home=None, mode=None, post=post):
        self.key = key or os.environ.get("GOOGLE_MAPS_API_KEY")
        self.home = home or os.environ.get("NATRO_HOME_ADDRESS", "")
        mode = (mode or os.environ.get("NATRO_TRAVEL_MODE") or "drive").lower()
        self.mode = mode if mode in MODES else "drive"
        self._post = post

    @property
    def available(self):
        return bool(self.key)

    def _place(self, value):
        """A place as he gave it, in one line; "home" is his home address (when it's set)."""
        value = " ".join(str(value or "").split())
        return self.home if self.home and value.casefold() in HOME_WORDS else value

    def _how(self, args):
        """(mode, kinds of transport, preference) from a tool's arguments; kinds and a preference mean public
        transport unless a mode is given."""
        use = args.get("use") or []
        use = [str(kind).lower() for kind in ([use] if isinstance(use, str) else use)]
        if any(kind not in KINDS for kind in use):
            raise ToolError(f"use is some of: {', '.join(KINDS)}.")
        prefer = args.get("prefer") or None
        if prefer and prefer not in PREFER:
            raise ToolError(f"prefer is one of: {', '.join(PREFER)}.")
        mode = args.get("mode") or ("transit" if use or prefer else self.mode)
        if mode not in MODES:
            raise ToolError(f"mode is one of: {', '.join(MODES)}.")
        return mode, use if mode == "transit" else [], prefer if mode == "transit" else None

    async def _routes(self, destination, origin, mode, depart=None, arrive=None, use=(), prefer=None,
                      alternatives=False):
        """(where from, as he'd say it; Google's routes); ToolError when there's none, or Google can't be asked.
        arrive (by public transport only) instead of depart: the routes that get there by then."""
        destination = self._place(destination)
        origin = self._place(origin) or self.home
        if not destination:
            raise ToolError("Say where to.")
        if not origin:
            raise ToolError("Natro doesn't know where he is: say where from, or set NATRO_HOME_ADDRESS.")
        if mode not in MODES:
            raise ToolError(f"mode is one of: {', '.join(MODES)}.")
        body = {"origin": waypoint(origin), "destination": waypoint(destination), "travelMode": MODES[mode],
                "languageCode": "en", "units": "METRIC"}
        if mode == "drive":
            body["routingPreference"] = "TRAFFIC_AWARE"
        if alternatives:
            body["computeAlternativeRoutes"] = True
        now = datetime.now(timezone())
        if arrive and mode == "transit":
            if arrive <= now:
                raise ToolError(f"{clock(arrive)} has passed: say the day too.")
            body["arrivalTime"] = arrive.astimezone(timezone()).isoformat()
        elif depart and depart > now:
            body["departureTime"] = depart.astimezone(timezone()).isoformat()
        if use or prefer:
            body["transitPreferences"] = {
                **({"allowedTravelModes": [name for kind in use for name in KINDS[kind]]} if use else {}),
                **({"routingPreference": PREFER[prefer]} if prefer else {})}
        try:
            data = await asyncio.to_thread(self._post, URL, body, self.key)
        except urllib.error.HTTPError as e:
            raise ToolError(f"Google's Routes API said: {google_message(e)} (HTTP {e.code})")
        except (OSError, ValueError) as e:
            raise ToolError(f"Couldn't reach Google's Routes API: {e}")
        start = "home" if origin == self.home else origin
        routes = data.get("routes") or []
        if not routes:
            raise ToolError(f"Google found no route {MODE_WORDS[mode]} from {start} to {destination}.")
        return start, routes

    async def trip(self, destination, origin=None, mode=None, depart=None):
        """{"minutes", "km", "mode", "from", "lines"} for a trip; ToolError when there's no route."""
        mode = mode or self.mode
        start, routes = await self._routes(destination, origin, mode, depart)
        route = routes[0]
        return {"minutes": max(1, round(seconds(route.get("duration")) / 60)),
                "usual": max(1, round(seconds(route.get("staticDuration") or route.get("duration")) / 60)),
                "km": round((route.get("distanceMeters") or 0) / 1000, 1), "mode": mode, "from": start,
                "lines": transit_steps(route)}

    async def travel_time(self, args):
        found = await self.trip(args.get("destination"), args.get("origin"), args.get("mode"),
                                moment(args, "depart_at"))
        said = (f"{minutes_words(found['minutes'])} {MODE_WORDS[found['mode']]} from {found['from']}, "
                f"{found['km']} km")
        if found["mode"] == "drive" and found["minutes"] - found["usual"] >= 5:
            said += f" (traffic adds {found['minutes'] - found['usual']} minutes)"
        lines = "".join(f"\n- {line}" for line in found["lines"])
        return said + "." + lines

    async def travel_routes(self, args):
        mode, use, prefer = self._how(args)
        depart, arrive = moment(args, "depart_at"), moment(args, "arrive_by")
        if depart and arrive:
            raise ToolError("Give depart_at or arrive_by, not both.")
        destination = self._place(args.get("destination"))
        start, routes = await self._routes(destination, args.get("origin"), mode, depart, arrive, use, prefer,
                                           alternatives=True)
        now = datetime.now(timezone())
        found = [summary(route, mode, destination, max(depart or now, now), arrive, bool(args.get("turns")))
                 for route in routes]
        if mode == "transit":
            # Soonest there first (with arrive_by, the latest to leave); the same lines later are the same way.
            found.sort(key=lambda each: -each["leaves"].timestamp() if arrive else
                       (each["arrives"], -each["leaves"].timestamp()))
            ways = {}
            for each in found:
                if each["signature"] in ways:
                    ways[each["signature"]]["also"].append(each)
                else:
                    ways[each["signature"]] = each
            found = list(ways.values())
        notes = []
        if use:
            wanted = [each for each in found if any(taken["kind"] in use for taken in each["rides"])]
            if wanted and len(wanted) < len(found):
                others = len(found) - len(wanted)
                notes.append(f"{others} other way{'s' if others > 1 else ''} Google found "
                             f"{'don' if others > 1 else 'doesn'}'t take the {kinds_words(use)}.")
                found = wanted
            elif not wanted:
                notes.append(f"None of Google's routes take the {kinds_words(use)} (it only prefers them): to go "
                             "that way, plan it leg by leg with travel_plan, changing where that line can be taken.")
        found = found[:MOST_ROUTES]
        when = (f"arriving by {clock(arrive)}" if arrive else
                f"leaving at {clock(depart)}" if depart and depart > now else "leaving now")
        said = [f"{len(found)} way{'s' if len(found) > 1 else ''} {MODE_WORDS[mode]} from {start} to {destination}, "
                f"{when}:"]
        for number, each in enumerate(found, 1):
            said.append(f"{number}. {route_line(each, mode)}")
            said.extend(f"   - {line}" for line in each["parts"])
            if each["also"]:
                later = [f"{clock(other['leaves'])} (arrives {other['arrives']:%H:%M})"
                         for other in sorted(each["also"], key=lambda other: other["leaves"])]
                said.append(f"   The same way also leaves at {', '.join(later[:-1])}"
                            f"{' and ' if len(later) > 1 else ''}{later[-1]}.")
        said += notes
        origin = self._place(args.get("origin")) or self.home
        said.append(f"Google Maps: {maps_link(origin, destination, mode)}")
        return "\n".join(said)

    async def travel_plan(self, args):
        legs = args.get("legs") or []
        if not isinstance(legs, list) or not legs or not all(isinstance(leg, dict) for leg in legs):
            raise ToolError("legs is a list of {to, mode, use}: where each part of the trip goes, and how.")
        if len(legs) > MOST_LEGS:
            raise ToolError(f"At most {MOST_LEGS} legs.")
        depart, arrive = moment(args, "depart_at"), moment(args, "arrive_by")
        if depart and arrive:
            raise ToolError("Give depart_at or arrive_by, not both.")
        origin = self._place(args.get("origin")) or self.home
        if not origin:
            raise ToolError("Natro doesn't know where he is: say where from, or set NATRO_HOME_ADDRESS.")
        places = [origin] + [self._place(leg.get("to")) for leg in legs]
        if not all(places[1:]):
            raise ToolError("Each leg needs its to: where that part of the trip ends.")
        hows = [self._how(leg) for leg in legs]
        try:
            stays = [timedelta(minutes=max(0, int(leg.get("stay_minutes") or 0))) for leg in legs]
        except (TypeError, ValueError):
            raise ToolError("stay_minutes is a number of minutes.")
        turns = bool(args.get("turns"))
        now = datetime.now(timezone())

        async def leg_route(number, depart=None, arrive=None):
            mode, use, prefer = hows[number]
            try:
                _, routes = await self._routes(places[number + 1], places[number], mode, depart, arrive, use, prefer)
            except ToolError as e:
                raise ToolError(f"Leg {number + 1}: {e}")
            start = max(depart or now, now)
            return summary(routes[0], mode, places[number + 1], start, arrive, turns)

        found = [None] * len(legs)
        if arrive:
            # Back from the end: each leg gets there in time for the next (and the stay before it).
            by = arrive
            for number in reversed(range(len(legs))):
                if number < len(legs) - 1:
                    by -= stays[number]
                if by <= now:
                    raise ToolError(f"Arriving by {clock(arrive)} that way means leaving before now.")
                if hows[number][0] == "transit":
                    found[number] = await leg_route(number, arrive=by)
                else:  # by car, on foot or by bike Google only leaves at a time: the traffic then is near enough
                    found[number] = arriving(await leg_route(number, depart=by), by)
                by = found[number]["leaves"]
        else:
            at = max(depart or now, now)
            for number in range(len(legs)):
                found[number] = await leg_route(number, depart=at)
                at = found[number]["arrives"] + stays[number]

        waited = timedelta()
        lines = []
        for number, (each, (mode, use, _)) in enumerate(zip(found, hows)):
            ready = found[number - 1]["arrives"] + stays[number - 1] if number else None
            wait = each["leaves"] - ready if ready else timedelta()
            wait_words = ""
            if wait >= timedelta(minutes=1):
                waited += wait
                wait_words = f"wait {minutes_words(round(wait.total_seconds() / 60))}, then "
            kinds = f" ({kinds_words(use)})" if use else ""
            lines.append(f"Leg {number + 1}, {MODE_WORDS[mode]}{kinds} to {places[number + 1]}: {wait_words}"
                         f"{route_line(each, mode)}")
            lines.extend(f"   - {line}" for line in each["parts"])
            if use and each["rides"] and not any(taken["kind"] in use for taken in each["rides"]):
                lines.append(f"   Google has no {kinds_words(use)} for this leg: it goes as above.")
            if number < len(legs) - 1 and stays[number]:
                lines.append(f"   Then {minutes_words(round(stays[number].total_seconds() / 60))} there.")
        leaves, arrives = found[0]["leaves"], found[-1]["arrives"]
        start = "home" if origin == self.home else origin
        total = round((arrives - leaves).total_seconds() / 60)
        waiting = f", {minutes_words(round(waited.total_seconds() / 60))} of it waiting" if waited else ""
        said = [f"His route from {start} to {places[-1]} in {len(legs)} leg{'s' if len(legs) > 1 else ''}: "
                f"{leave_and_arrive(leaves, arrives)} ({minutes_words(total)} in all{waiting})."] + lines
        if len(legs) > 1:
            said.append(await self._quickest(places[0], places[-1], hows, depart, arrive))
        said.append("Google Maps, leg by leg: " + " ".join(
            f"{number + 1}. {maps_link(places[number], places[number + 1], hows[number][0])}"
            for number in range(len(legs))))
        return "\n".join(said)

    async def _quickest(self, origin, destination, hows, depart, arrive):
        """Google's own quickest way straight there, to set his plan against: by public transport if a leg is."""
        mode = "transit" if any(how[0] == "transit" for how in hows) else hows[0][0]
        try:
            _, routes = await self._routes(destination, origin, mode, depart, arrive)
        except ToolError as e:
            return f"Google's quickest way straight there couldn't be found: {e}"
        now = datetime.now(timezone())
        quickest = summary(routes[0], mode, destination, max(depart or now, now), arrive if mode == "transit" else None)
        if arrive and mode != "transit":
            quickest = arriving(quickest, arrive)
        return f"To compare, Google's quickest way {MODE_WORDS[mode]} straight there: {route_line(quickest, mode)}"

    def tools(self):
        if not self.available:
            return []
        home = " (by default from his home)" if self.home else ""
        usual = MODE_WORDS[self.mode]
        origin = {"type": "string", "description": "Where he starts, if not home: a place, or \"lat,lng\" (where his "
                                                   "phone is, from phone_location, when he says \"from here\")."}
        place = {"type": "string", "description": "A place or address (\"Tbilisi airport\", \"Vake Park\", "
                                                  "\"12 Chavchavadze Ave, Tbilisi\")."}
        use = {"type": "array", "items": {"type": "string", "enum": list(KINDS)},
               "description": "Public transport only: the kinds he wants to ride. Google prefers them but may "
                              "still use others."}
        prefer = {"type": "string", "enum": list(PREFER), "description": "Public transport only."}
        depart_at = {"type": "string", "description": "When he'd leave, his local time (2026-10-18T15:00); "
                                                      "default now."}
        arrive_by = {"type": "string", "description": "When he must be there instead, his local time "
                                                      "(2026-10-18T10:00)."}
        turns = {"type": "boolean", "description": "Every turn too: driving, walking and cycling directions, and "
                                                   "the walks to and between stops."}
        return [
            Tool("travel_time",
                 f"How long it takes to get somewhere{home}: by car with traffic, by public transport with its lines "
                 f"and times, on foot or by bike; {usual} unless he says otherwise (mode). For \"how long to the "
                 "airport?\", \"when should I leave for ...?\". origin: where he's starting from, if not home. To "
                 "compare ways, or for the details, use travel_routes.",
                 {"type": "object", "properties": {
                     "destination": place, "origin": origin, "mode": {"type": "string", "enum": list(MODES)},
                     "depart_at": depart_at,
                 }, "required": ["destination"]},
                 self.travel_time, "natro", personal=True),
            Tool("travel_routes",
                 f"Compare the ways to get somewhere{home}, step by step, to plan a trip: up to {MOST_ROUTES} of "
                 "Google's routes, by public transport each with its walks, lines, stops, departure and arrival "
                 "times, changes and fare (the same lines at later times listed with it); by car each road with "
                 f"traffic; on foot or by bike. {usual.capitalize()} unless he says otherwise. For \"what are my "
                 "options to ...?\", \"which bus is best?\", \"fewer changes\", \"less walking\", \"I need to be "
                 "there by 10\", \"tell me the way step by step\". Ends with a Google Maps link.",
                 {"type": "object", "properties": {
                     "destination": place, "origin": origin, "mode": {"type": "string", "enum": list(MODES)},
                     "use": use, "prefer": prefer, "depart_at": depart_at, "arrive_by": arrive_by, "turns": turns,
                 }, "required": ["destination"]},
                 self.travel_routes, "natro", personal=True),
            Tool("travel_plan",
                 f"His own route, leg by leg, when he says how to go: \"metro to Station Square, then the bus to the "
                 "airport\", \"a taxi to Varketili metro, then the metro\". Each leg goes from where the last one "
                 "ended (the first from origin, by default his home), by its own mode and kinds of transport, and "
                 "leaves when he gets there (after stay_minutes, if he stops there first). Says each leg's walks, "
                 "lines, stops, times and waits, and when he arrives; arrive_by works it out back from the end. "
                 "Then Google's quickest way straight there, to compare, and a Google Maps link for each leg.",
                 {"type": "object", "properties": {
                     "legs": {"type": "array", "minItems": 1, "maxItems": MOST_LEGS, "items": {
                         "type": "object", "properties": {
                             "to": {**place, "description": "Where this leg ends: a place, a stop or a station "
                                                            "(\"Varketili metro station, Tbilisi\")."},
                             "mode": {"type": "string", "enum": list(MODES),
                                      "description": f"Default {usual}; a taxi is drive."},
                             "use": use, "prefer": prefer,
                             "stay_minutes": {"type": "integer", "description": "Time he spends there before the "
                                                                                "next leg."},
                         }, "required": ["to"]}},
                     "origin": origin, "depart_at": depart_at, "arrive_by": arrive_by, "turns": turns,
                 }, "required": ["legs"]},
                 self.travel_plan, "natro", personal=True),
        ]


def arriving(found, arrives):
    """A route by car, on foot or by bike moved to arrive then."""
    return {**found, "leaves": arrives - (found["arrives"] - found["leaves"]), "arrives": arrives}


def google_message(error):
    try:
        return json.loads(error.read())["error"]["message"]
    except Exception:
        return error.reason
