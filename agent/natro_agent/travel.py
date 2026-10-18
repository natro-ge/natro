"""Travel time with Google's Routes API: how long it takes to get somewhere, by car with today's traffic, by
public transport (with its lines), on foot or by bike. https://developers.google.com/maps/documentation/routes

Offered once GOOGLE_MAPS_API_KEY is set (a key for the Routes API, in the same Google Cloud project; the free
monthly allowance covers personal use). Trips start from NATRO_HOME_ADDRESS unless he says where from, and go
by NATRO_TRAVEL_MODE (default drive) unless he says how. Places are personal (where he lives, where he goes),
so only Claude uses it. The meetings watcher uses it too, for when to leave (watchers.py).
"""
import asyncio
import http.client
import io
import json
import os
import urllib.error
import urllib.parse
from datetime import datetime

from natro_agent.config import timezone
from natro_agent.tools import Tool, ToolError

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELDS = ("routes.duration,routes.staticDuration,routes.distanceMeters,routes.legs.steps.travelMode,"
          "routes.legs.steps.transitDetails")
TIMEOUT_SECONDS = 20
MODES = {"drive": "DRIVE", "transit": "TRANSIT", "walk": "WALK", "bicycle": "BICYCLE"}
MODE_WORDS = {"drive": "by car", "transit": "by public transport", "walk": "on foot", "bicycle": "by bike"}


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


def transit_steps(route):
    """The lines to take: "Metro line 1 from Rustaveli at 15:02 to Station Square (4 stops)"."""
    said = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            details = step.get("transitDetails")
            if not details:
                continue
            line = details.get("transitLine", {})
            vehicle = line.get("vehicle", {}).get("name", {}).get("text", "")
            name = line.get("nameShort") or line.get("name") or ""
            stops = details.get("stopDetails", {})
            leaves = details.get("localizedValues", {}).get("departureTime", {}).get("time", {}).get("text", "")
            text = " ".join(part for part in (vehicle, name) if part) or "Transit"
            text += f" from {stops.get('departureStop', {}).get('name', '?')}"
            text += f" at {leaves}" if leaves else ""
            text += f" to {stops.get('arrivalStop', {}).get('name', '?')}"
            if details.get("stopCount"):
                text += f" ({details['stopCount']} stops)"
            said.append(text)
    return said


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

    async def trip(self, destination, origin=None, mode=None, depart=None):
        """{"minutes", "km", "mode", "from", "lines"} for a trip; ToolError when there's no route."""
        destination = " ".join(str(destination or "").split())
        origin = " ".join(str(origin or "").split()) or self.home
        if not destination:
            raise ToolError("Say where to.")
        if not origin:
            raise ToolError("Natro doesn't know where he is: say where from, or set NATRO_HOME_ADDRESS.")
        mode = mode or self.mode
        if mode not in MODES:
            raise ToolError(f"mode is one of: {', '.join(MODES)}.")
        body = {"origin": {"address": origin}, "destination": {"address": destination}, "travelMode": MODES[mode],
                "languageCode": "en", "units": "METRIC"}
        if mode == "drive":
            body["routingPreference"] = "TRAFFIC_AWARE"
        if depart and depart > datetime.now(timezone()):
            body["departureTime"] = depart.astimezone(timezone()).isoformat()
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
        route = routes[0]
        return {"minutes": max(1, round(seconds(route.get("duration")) / 60)),
                "usual": max(1, round(seconds(route.get("staticDuration") or route.get("duration")) / 60)),
                "km": round((route.get("distanceMeters") or 0) / 1000, 1), "mode": mode, "from": start,
                "lines": transit_steps(route)}

    async def travel_time(self, args):
        depart = None
        if args.get("depart_at"):
            try:
                depart = datetime.fromisoformat(str(args["depart_at"]))
            except ValueError:
                raise ToolError("depart_at is a time like 2026-10-18T15:00.")
            depart = depart if depart.tzinfo else depart.replace(tzinfo=timezone())
        found = await self.trip(args.get("destination"), args.get("origin"), args.get("mode"), depart)
        said = (f"{minutes_words(found['minutes'])} {MODE_WORDS[found['mode']]} from {found['from']}, "
                f"{found['km']} km")
        if found["mode"] == "drive" and found["minutes"] - found["usual"] >= 5:
            said += f" (traffic adds {found['minutes'] - found['usual']} minutes)"
        lines = "".join(f"\n- {line}" for line in found["lines"])
        return said + "." + lines

    def tools(self):
        if not self.available:
            return []
        home = " (by default from his home)" if self.home else ""
        return [Tool(
            "travel_time",
            f"How long it takes to get somewhere{home}: by car with traffic, by public transport with its lines "
            f"and times, on foot or by bike; {MODE_WORDS[self.mode]} unless he says otherwise (mode). For \"how "
            "long to the airport?\", \"when should I leave for ...?\". origin: where he's starting from, if not "
            "home.",
            {"type": "object", "properties": {
                "destination": {"type": "string", "description": "A place or address (\"Tbilisi airport\", \"Vake "
                                                                "Park\", \"12 Chavchavadze Ave, Tbilisi\")."},
                "origin": {"type": "string"},
                "mode": {"type": "string", "enum": list(MODES)},
                "depart_at": {"type": "string", "description": "When he'd leave, his local time (2026-10-18T15:00); "
                                                              "default now."},
            }, "required": ["destination"]},
            self.travel_time, "natro", personal=True)]


def google_message(error):
    try:
        return json.loads(error.read())["error"]["message"]
    except Exception:
        return error.reason
