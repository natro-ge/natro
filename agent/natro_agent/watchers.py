"""Watchers: Natro keeps an eye out for what he told her to, and speaks up when it happens, with cheap checks
and no model call.

Kinds:
  email          an unread email from someone (Gmail's from:, a name or an address), and if he said so about
                 something (words in it), checked every EMAIL_SECONDS: "Nino emailed: Dinner on Friday."
  notification   a notification on his phone, from an app and/or with words in it. The phone matches its own
                 notifications (it's sent the rules: watch_rules) and sends only those that match
                 (notification_matched): "Bank of Georgia: You received 500 GEL."
  meetings       a heads-up before each timed event in his calendar: "Dentist at City clinic in 10 minutes."
                 There's one from the start; cancelling it stops them. With travel time (travel.py), an event at a
                 place further away than that gives the heads-up as long before he has to leave instead: "Leave
                 in 10 minutes for Dentist at City clinic: about 25 minutes by car from where you are; it starts
                 at 15:00." The trip starts where his phone is (its location tool, while it's online and
                 allowed), else at his home address.

A watcher goes after its first match (once) or stays. An urgent one (meetings always) is said out loud where he
is; the rest only show. An alert goes to every device that says "alerts" in its hello and into the chat; one
that no device could show waits for one until it's stale (an email's or a notification's after STALE_HOURS, a
meeting's once it starts). The next request a model answers hears of an email or a notification (Sonnet answers
it). Kept in watchers.json.

Natro to device:
  {"type": "alert", "id": "al3", "text": "Nino emailed: Dinner on Friday.", "kind": "email", "urgent": false,
   "speak": false}                   speak: on this device (an urgent one, on the device he used last)
  {"type": "watch_rules", "rules": [{"id": "w2", "app": "Bank of Georgia", "words": "transfer"}]}
                                     to devices that say "watch_notifications" in their hello: what to look
                                     for in its notifications (on connecting, and whenever they change)
Device to Natro:
  {"type": "notification_matched", "rule": "w2", "app": "Bank of Georgia", "title": "...", "text": "..."}
"""
import asyncio
import itertools
import json
import re
import time
from datetime import datetime, timedelta
from email.utils import parseaddr

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.google_tools import header_map
from natro_agent.tools import Tool, ToolError
from natro_agent.travel import COORDINATES, MODE_WORDS, minutes_words

CHECK_SECONDS = 30
EMAIL_SECONDS = 120
# Meetings are looked up this far ahead, this often.
MEETINGS_AHEAD = timedelta(hours=2)
MEETINGS_SECONDS = 300
MEETING_MINUTES = 10
# Time to spare when saying when to leave; the trip is looked up again this often while the meeting is ahead
# (more often once it's near, as he may have moved), and where his phone is, at most this often.
LEAVE_SPARE_MINUTES = 5
TRIP_SECONDS = 1800
TRIP_NEAR_SECONDS = 600
NEAR = timedelta(minutes=45)
PLACE_SECONDS = 300
# A meeting's "place" that's a link or a call, not somewhere to go.
ONLINE = re.compile(r"https?://|www\.|zoom|meet\.google|teams|skype|webex|online|discord|phone call", re.IGNORECASE)
STALE_HOURS = 12
MOST = 50
MOST_SEEN = 50
ALERT_CHARS = 240
KINDS = ("email", "notification", "meetings")


def ms(moment):
    return int(moment.timestamp() * 1000)


def short(text, length=ALERT_CHARS):
    text = " ".join(str(text or "").split())
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def sender(from_header):
    """"Nino Beridze <nino@example.com>" -> "Nino Beridze"; a bare address stays itself."""
    name, address = parseaddr(from_header or "")
    return name or address or "Someone"


def describe(item):
    """A watcher in words, for the list and for her reply."""
    if item["kind"] == "email":
        about = f" about \"{item['about']}\"" if item.get("about") else ""
        return f"an email from {item['from']}{about}"
    if item["kind"] == "notification":
        return f"a notification on his phone {notification_words(item)}"
    return f"a heads-up {item['minutes']} minutes before each meeting"


def notification_words(item):
    """"from Bank of Georgia with "transfer"", or either half."""
    parts = [f"from {item['app']}" if item.get("app") else "", f"with \"{item['words']}\"" if item.get("words") else ""]
    return " ".join(part for part in parts if part)


class Watchers:
    """path: watchers.json. devices: name -> device, while connected (the agent's). google: a
    google_tools.Google, or None (then no email or meeting watchers work). travel: a travel.Travel, for when
    to leave for a meeting (None, or one without a key or a home address: only the heads-up).

    The server sets deliver(alert) -> whether any device showed it."""

    def __init__(self, path, devices, google=None, travel=None):
        self.path = path
        self.devices = devices
        self.google = google
        self.travel = travel
        self._trips = {}  # a meeting's key -> (its trip, or None; when it was looked up)
        self._place = (None, 0.0)  # where his phone is ("lat,lng", or None), and when (monotonic) it was asked
        self.deliver = lambda alert: False
        self.now = lambda: datetime.now(timezone())
        self._ids = itertools.count(1)
        self._events = []  # the meetings ahead, as Google gave them
        self._events_at = 0.0  # when (monotonic) they were looked up
        self._mail_at = 0.0
        self.items, self.waiting, self.told = self._load()

    def tools(self):
        return [
            Tool("watch",
                 "Keep an eye out for something and tell him when it happens, with no need to ask again: an email "
                 "from someone (kind email: from, and about if he said what it's about), a notification on his "
                 "phone (kind notification: app and/or words in it), or heads-ups before his meetings (kind "
                 "meetings: minutes; for one at a place he has to travel to, the heads-up comes that long before "
                 "he has to leave, with the trip from where his phone is). once: only the next time (\"tell me when Nino's email comes\"); otherwise "
                 "every time (\"always tell me when Nino emails\"). urgent: he wants it said out loud right away; "
                 "otherwise it's only shown.",
                 {"type": "object", "properties": {
                     "kind": {"type": "string", "enum": list(KINDS)},
                     "from": {"type": "string", "description": "Email: who it's from, a name or an address."},
                     "about": {"type": "string", "description": "Email: words it must have, if he said."},
                     "app": {"type": "string", "description": "Notification: the app's name (\"Bank of Georgia\", "
                                                             "\"WhatsApp\", \"Messages\")."},
                     "words": {"type": "string", "description": "Notification: words it must have (a sender's "
                                                               "name, \"transfer\")."},
                     "minutes": {"type": "integer", "description": "Meetings: how long before; default 10."},
                     "once": {"type": "boolean"},
                     "urgent": {"type": "boolean"},
                 }, "required": ["kind"]},
                 self.watch, "natro", personal=True, changes=True, final=True),
            Tool("watchers_list",
                 "What you're watching for him (with ids), and what you told him lately.",
                 {"type": "object", "properties": {}}, self.list, "natro", personal=True),
            Tool("watchers_cancel",
                 "Stop watching for something, by its id (from watchers_list).",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                 self.cancel, "natro", personal=True, changes=True),
        ]

    # Tools

    async def watch(self, args):
        kind = args.get("kind")
        if kind not in KINDS:
            raise ToolError(f"kind is one of: {', '.join(KINDS)}.")
        if len(self.items) >= MOST:
            raise ToolError(f"There are already {MOST} watchers: cancel some first.")
        now = self.now()
        item = {"id": f"w{int(time.time() * 1000):x}{next(self._ids)}", "kind": kind, "once": bool(args.get("once")),
                "urgent": bool(args.get("urgent")), "made": ms(now)}
        later = ""
        if kind == "email":
            if not self.google:
                raise ToolError("His Google account isn't signed in, so you can't watch his email.")
            item["from"] = short(args.get("from"), 100)
            item["about"] = short(args.get("about"), 100)
            if not item["from"]:
                raise ToolError("Say who the email is from (from).")
            item["seen"] = []
            when = "the next time" if item["once"] else "whenever"
            said = f"OK, I'll tell you {when} {item['from']} emails" + (f" about {item['about']}." if item["about"] else ".")
        elif kind == "notification":
            item["app"] = short(args.get("app"), 60)
            item["words"] = short(args.get("words"), 100)
            if not item["app"] and not item["words"]:
                raise ToolError("Give the app, the words in it, or both.")
            when = "the next time" if item["once"] else "whenever"
            said = f"OK, I'll tell you {when} your phone gets a notification {notification_words(item)}."
            if not any(getattr(device, "watch_notifications", False) for device in self.devices.values()):
                later = " (His phone isn't connected: it starts watching when it's back.)"
        else:
            if not self.google:
                raise ToolError("His Google account isn't signed in, so you can't watch his calendar.")
            try:
                minutes = int(args.get("minutes") or MEETING_MINUTES)
            except (TypeError, ValueError):
                raise ToolError("minutes is a number.")
            if not 1 <= minutes <= 120:
                raise ToolError("minutes is from 1 to 120.")
            item.update(minutes=minutes, urgent=True, once=False)
            self.items = [old for old in self.items if old["kind"] != "meetings"]  # one is enough
            said = f"OK, I'll give you a heads-up {minutes} minutes before each meeting."
        self.items.append(item)
        self._save()
        if kind == "notification":
            self._push_rules()
        log.write("watcher", event="set", id=item["id"], what=describe(item))
        return f"{said}\nWatcher {item['id']}: {describe(item)}.{later}"

    async def list(self, args):
        lines = []
        for item in self.items:
            how = ", once" if item["once"] else ""
            how += ", said out loud" if item["urgent"] else ""
            lines.append(f"- {item['id']}: {describe(item)}{how}")
        lines = ["Watching for:", *lines] if lines else ["Not watching for anything."]
        recent = [alert for alert in self.told if isinstance(alert, dict)]
        if recent:
            lines.append("Told him lately:")
            lines += [f"- {datetime.fromtimestamp(alert['due'] / 1000, timezone()):%a %H:%M}: {alert['text']}"
                      for alert in recent[-10:]]
        return "\n".join(lines)

    async def cancel(self, args):
        wanted = str(args.get("id") or "").strip()
        for item in self.items:
            if item["id"] == wanted:
                self._remove(item)
                log.write("watcher", event="cancelled", id=wanted)
                return f"Stopped watching for {describe(item)}."
        raise ToolError(f"There's no watcher {wanted!r}: watchers_list has their ids.")

    def _remove(self, item):
        self.items.remove(item)
        self._save()
        if item["kind"] == "notification":
            self._push_rules()

    # Checking

    async def run(self):
        """Check, forever (the server runs it as a task)."""
        while True:
            try:
                await self.check()
            except Exception as e:  # one bad check must not stop the others
                log.write("error", step="watchers", error=repr(e))
            await asyncio.sleep(CHECK_SECONDS)

    async def check(self):
        if not self.google:
            return
        now = self.now()
        if any(item["kind"] == "meetings" for item in self.items):
            await self._meetings(now)
        if any(item["kind"] == "email" for item in self.items) and time.monotonic() - self._mail_at >= EMAIL_SECONDS:
            self._mail_at = time.monotonic()
            await self._email(now)

    async def _meetings(self, now):
        watcher = next(item for item in self.items if item["kind"] == "meetings")
        if not self._events_at or time.monotonic() - self._events_at >= MEETINGS_SECONDS:
            self._events = await self.google.event_items(now, now + MEETINGS_AHEAD)
            self._events_at = time.monotonic()
        told = {alert.get("key") for alert in self.told if isinstance(alert, dict)}
        for event in self._events:
            start = event.get("start", {}).get("dateTime")
            if not start or declined(event):
                continue
            start = datetime.fromisoformat(start).astimezone(timezone())
            key = f"{event.get('id')}@{start.isoformat()}"
            left = (start - now).total_seconds()
            if key in told or left <= 0:
                continue
            what = short(event.get("summary") or "A meeting", 80)
            where = f" at {event['location']}" if event.get("location") else ""
            trip = await self._trip(event, key, start, now)
            if trip and trip["minutes"] + LEAVE_SPARE_MINUTES > watcher["minutes"]:
                # Further than the heads-up gives time for: the heads-up comes before he has to leave instead.
                leave = start - timedelta(minutes=trip["minutes"] + LEAVE_SPARE_MINUTES)
                if now >= leave - timedelta(minutes=watcher["minutes"]):
                    self._alert(watcher, leave_words(what, where, trip, start, leave, now), now, until=start, key=key)
                continue
            if left > watcher["minutes"] * 60:
                continue
            minutes = max(1, round(left / 60))
            text = f"{what}{where} in {minutes} minute{'s' if minutes > 1 else ''}."
            self._alert(watcher, text, now, until=start, key=key)

    async def _trip(self, event, key, start, now):
        """The trip to a meeting's place from where he is (or home), looked up now and then; None if it has no
        place, or no trip can be found."""
        place = (event.get("location") or "").strip()
        travel = self.travel
        if not place or ONLINE.search(place) or not travel or not travel.available:
            return None
        known = self._trips.get(key)
        again = TRIP_NEAR_SECONDS if start - now <= NEAR else TRIP_SECONDS
        if known and time.monotonic() - known[1] < again:
            return known[0]
        here = await self._where_he_is()
        if not here and not travel.home:
            return None
        try:
            trip = await travel.trip(place, origin=here)
            trip["here"] = bool(here)
        except ToolError as e:
            if not known:  # once is enough
                log.write("watcher", event="no_trip", place=place, error=str(e))
            trip = None
        self._trips[key] = (trip, time.monotonic())
        return trip

    async def _where_he_is(self):
        """Where his phone is, as "lat,lng" (asked at most every PLACE_SECONDS); None when it's offline, or
        can't say."""
        place, asked = self._place
        if asked and time.monotonic() - asked < PLACE_SECONDS:
            return place
        phone = self.devices.get("phone")
        tool = next((tool for tool in phone.tools() if tool.name == "phone_location"), None) if phone else None
        place = None
        if tool:
            try:
                found = COORDINATES.search(str(await asyncio.wait_for(tool.run({}), 30)))
                place = f"{found.group(1)},{found.group(2)}" if found else None
            except Exception as e:  # off, not allowed, no fix: from home
                log.write("watcher", event="no_place", error=str(e)[:200])
        self._place = (place, time.monotonic())
        return place

    async def _email(self, now):
        for watcher in [item for item in self.items if item["kind"] == "email"]:
            since = int(watcher["made"] / 1000) - 60
            query = f"in:inbox is:unread after:{since} from:({watcher['from']})"
            if watcher.get("about"):
                query += f" {watcher['about']}"
            messages, _ = await self.google.mail_items(query, max_results=5)
            alerted = False
            for message in reversed(messages):  # oldest first
                if message["id"] in watcher["seen"]:
                    continue
                watcher["seen"] = (watcher["seen"] + [message["id"]])[-MOST_SEEN:]
                headers = header_map(message)
                self._alert(watcher, f"{sender(headers.get('from'))} emailed: "
                                     f"{short(headers.get('subject') or '(no subject)', 140)}", now)
                alerted = True
                if watcher["once"]:
                    break
            if alerted and watcher["once"]:
                self._remove(watcher)

    def matched(self, rule_id, app, title, text):
        """The phone saw a notification that fits a rule."""
        watcher = next((item for item in self.items if item["id"] == rule_id and item["kind"] == "notification"), None)
        if watcher is None:
            return  # cancelled meanwhile
        said = ": ".join(part for part in (short(title, 80), short(text, 160)) if part)
        self._alert(watcher, f"{short(app, 40) or 'Your phone'}: {said}" if said else f"A notification from {app}.",
                    self.now())
        if watcher["once"]:
            self._remove(watcher)

    def _alert(self, watcher, text, now, until=None, key=None):
        alert = {"id": f"al{int(time.time() * 1000):x}{next(self._ids)}", "watcher": watcher["id"],
                 "kind": watcher["kind"], "text": text, "urgent": watcher["urgent"], "due": ms(now),
                 "until": ms(until or now + timedelta(hours=STALE_HOURS)), "key": key}
        self.told = (self.told + [alert])[-MOST:]
        if self.deliver(alert):
            log.write("watcher", event="alert", id=alert["id"], watcher=watcher["id"], text=text)
        else:
            self.waiting.append(alert)
            log.write("watcher", event="waiting", id=alert["id"], watcher=watcher["id"])
        self._save()

    # Devices

    def connected(self, device):
        """A device came online: the alerts waiting for one, and (a phone) the notification rules."""
        if getattr(device, "watch_notifications", False):
            device.post(type="watch_rules", rules=self.rules())
        if not getattr(device, "alerts", False) or not self.waiting:
            return
        now = ms(self.now())
        waiting, self.waiting = self.waiting, []
        for alert in waiting:
            if alert["until"] <= now:
                log.write("watcher", event="stale", id=alert["id"])
            elif not self.deliver(alert):
                self.waiting.append(alert)
        self._save()

    def rules(self):
        return [{"id": item["id"], "app": item.get("app", ""), "words": item.get("words", "")}
                for item in self.items if item["kind"] == "notification"]

    def _push_rules(self):
        for device in list(self.devices.values()):
            if getattr(device, "watch_notifications", False):
                device.post(type="watch_rules", rules=self.rules())

    # On disk

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # The first time: heads-ups before meetings, as he asked (2026-10-18).
            return [{"id": "w1", "kind": "meetings", "minutes": MEETING_MINUTES, "once": False, "urgent": True,
                     "made": ms(self.now())}], [], []

        def kept(key):
            return [item for item in data.get(key, []) if isinstance(item, dict) and item.get("id")]
        return [item for item in kept("watchers") if item.get("kind") in KINDS], kept("waiting"), kept("told")

    def _save(self):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps({"watchers": self.items, "waiting": self.waiting, "told": self.told},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(self.path)


def leave_words(what, where, trip, start, leave, now):
    """"Leave in 10 minutes for Dentist at City clinic: about 25 minutes by car from where you are; it starts at
    15:00." ("Time to leave for ..." once it's time.)"""
    minutes = round((leave - now).total_seconds() / 60)
    when = f"Leave in {minutes} minute{'s' if minutes > 1 else ''} for" if minutes >= 1 else "Time to leave for"
    start_from = "where you are" if trip.get("here") else trip["from"]
    return (f"{when} {what}{where}: about {minutes_words(trip['minutes'])} {MODE_WORDS[trip['mode']]} from "
            f"{start_from}; it starts at {start:%H:%M}.")


def declined(event):
    """Whether he said no to the event."""
    return any(person.get("self") and person.get("responseStatus") == "declined"
               for person in event.get("attendees", []))
