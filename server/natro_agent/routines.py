"""Routines: requests Natro carries out by herself at set times ("every weekday at 8, brief me", "every Friday at
6 in the evening, check the Berlin flight's price, and tell me only if it got cheaper").

routine_add keeps one in routines.json: a short name, its task (his whole request, complete: a routine doesn't
see the conversation it was set up in), when it first runs, how it repeats (an RRULE, read like a reminder's, at
most every MOST_OFTEN_MINUTES; without one it runs once), and whether it's quiet; or, in place of a time, one of his
places (places.py): it runs when he gets there or leaves, once or every time. The clock (run) sleeps until the
next one is due; then it runs as a background job titled with its name (start: background.py's routine): in
the chat as it works, a step that needs his yes asks where he was last, and what it found goes to his devices as
a job's result does, titled. A quiet routine that finds nothing he asked to hear about replies only QUIET_REPLY:
nothing is pushed or spoken (the chat shows it, as it shows every run).

A time is skipped when it's more than LATE_MINUTES gone (the server was down then), when the routine's last run
is still going, and when this month's budget is used up (he's told). routines_list says how each one's last run
went; routines_run runs one now ("run my morning briefing now").
"""
import asyncio
import itertools
import json
import time
from datetime import datetime, timedelta

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.google_tools import parse_when
from natro_agent.jobs import RUNNING, short
from natro_agent.places import AGAIN_MINUTES, where_words
from natro_agent.reminders import (CHECK_SECONDS, DAY_TIME, GRACE_SECONDS, check_rule, day_words, item_rule, ms,
                                   rule_parts, repeat_words, when_words)
from natro_agent.tools import Tool, ToolError

MOST = 20
MOST_NAME = 60
MOST_TASK = 1000
# Each run is a whole request on Claude, so routines repeat at most this often.
MOST_OFTEN_MINUTES = 60
# A time missed by more than this (the server was down) is skipped: what a routine finds is about now.
LATE_MINUTES = 120
RESULT_CHARS = 300
# What a quiet routine replies when there's nothing he asked to hear about.
QUIET_REPLY = "Nothing to report."


def is_quiet(reply):
    """Whether a quiet routine's reply is QUIET_REPLY (perhaps with a few words more): nothing to tell him."""
    words = " ".join(str(reply or "").split()).casefold()
    return words.startswith(QUIET_REPLY.casefold().rstrip(".")) and len(words) < 120


def from_ms(value):
    return datetime.fromtimestamp(value / 1000, timezone())


class Routines:
    """path: the JSON file they're kept in. places: a places.Places, for routines at a place (None: none).

    background.Background sets start(routine, asked) -> why it didn't start ("" when it did; asked: he asked to
    run it now), and the server asking() -> the device the request being handled came from."""

    def __init__(self, path, places=None):
        self.path = path
        self.places = places
        if places:
            places.users.append(self)
        self.start = lambda routine, asked=False: "Routines can't run here."
        self.asking = lambda: None
        self.now = lambda: datetime.now(timezone())
        self._ids = itertools.count(1)
        self._wake = asyncio.Event()
        self.items = self._load()

    def tools(self):
        return [
            Tool("routine_add",
                 "Set up a routine: a request you carry out by yourself at set times, as a background job, then tell "
                 "him what you found: \"every weekday at 8, brief me\", \"every Friday at 18:00, check the cheapest "
                 "flight to Berlin\". The routine doesn't see this conversation: task is his whole request, complete, "
                 "with every detail he gave, as he'd ask you then (\"Brief me.\", \"Find the cheapest flight from "
                 "Tbilisi to Berlin on 12 December and tell me its price.\"). A nudge with words fixed now is a "
                 "reminder (remind); something to notice as it happens (an email, a notification) is a watcher. In "
                 "place of a time it can run when he gets to one of his places or leaves it (\"when I get to work, "
                 "tell me what's on today\"): give place instead of at.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "A short name, a few words: \"Morning briefing\", "
                                                               "\"Berlin flight price\"."},
                     "task": {"type": "string", "description": "What to do each time, complete."},
                     "at": {"type": "string", "description": "When it first runs, his local time: 2026-10-20T08:00. "
                                                             "A day alone means 09:00 that day."},
                     "repeat": {"type": "string", "description": "To repeat it from at, an RRULE: FREQ=DAILY (every "
                                "day), FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR (weekdays), FREQ=WEEKLY;BYDAY=FR, "
                                "FREQ=MONTHLY;BYMONTHDAY=1, FREQ=DAILY;UNTIL=20261231. None: it runs once."},
                     "quiet": {"type": "boolean", "description": "True when he wants to hear only if there's "
                                                                 "something to report (\"tell me only if it got "
                                                                 "cheaper\")."},
                     "place": {"type": "string", "description": "Instead of at: one of his saved places, by name."},
                     "on": {"type": "string", "enum": ["arrive", "leave"],
                            "description": "With place: when he gets there, or leaves. Default arrive."},
                     "every_time": {"type": "boolean", "description": "With place: every time, when he says so; "
                                                                      "else once."},
                 }, "required": ["name", "task"]},
                 self.add, "natro", changes=True, final=True),
            Tool("routines_list",
                 "His routines (with their ids), when each runs next, and how its last run went.",
                 {"type": "object", "properties": {}}, self.list, "natro"),
            Tool("routines_cancel",
                 "Stop one of his routines for good, by its id (from routines_list). To change one, cancel it and "
                 "set it up again.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                 self.cancel, "natro", changes=True),
            Tool("routines_run",
                 "Run one of his routines now, as a background job, by its id (from routines_list): \"run my morning "
                 "briefing now\". Its times stay as they are.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                 self.run_now, "natro", final=True),
        ]

    # Tools

    async def add(self, args):
        name = " ".join(str(args.get("name") or "").split())
        task = " ".join(str(args.get("task") or "").split())
        if not name or not task:
            raise ToolError("Give the routine a name and say what to do each time (task).")
        if len(name) > MOST_NAME:
            raise ToolError(f"Keep the name under {MOST_NAME} characters.")
        if len(task) > MOST_TASK:
            raise ToolError(f"Keep the task under {MOST_TASK} characters.")
        if len(self.items) >= MOST:
            raise ToolError(f"He already has {MOST} routines: cancel some first.")
        if str(args.get("place") or "").strip():
            return self._add_at_place(args, name, task)
        if not str(args.get("at") or "").strip():
            raise ToolError("Say when it first runs (at).")
        now = self.now()
        when = parse_when(str(args["at"]))
        if not isinstance(when, datetime):
            when = datetime(when.year, when.month, when.day, *DAY_TIME, tzinfo=timezone())
        start = when.astimezone(timezone())
        quiet = bool(args.get("quiet"))
        item = {"id": f"rt{int(time.time() * 1000):x}{next(self._ids)}", "name": name, "task": task,
                "start": start.isoformat(), "repeat": "", "next": None, "quiet": quiet, "made": ms(now),
                "from": getattr(self.asking(), "name", ""), "last": None}
        repeat = str(args.get("repeat") or "").strip()
        if repeat:
            item["repeat"] = check_rule(repeat, start, MOST_OFTEN_MINUTES, "Routines")
            first = item_rule(item).after(now - timedelta(seconds=GRACE_SECONDS), inc=True)
            if first is None:
                raise ToolError("That repeat rule has no times left.")
            if rule_parts(item["repeat"]).get("FREQ") in ("MINUTELY", "HOURLY"):
                said = f"{repeat_words(item['repeat'])}, starting {when_words(first, now)}"
            else:
                said = f"{repeat_words(item['repeat'])} at {first:%H:%M}, starting {day_words(first, now)}"
        else:
            if start < now - timedelta(seconds=GRACE_SECONDS):
                raise ToolError(f"{when_words(start, now).capitalize()} has passed: give a time to come.")
            first = max(start, now)
            said = when_words(first, now)
        item["next"] = first.isoformat()
        self.items.append(item)
        self._save()
        self._wake.set()
        log.write("routine", event="set", id=item["id"], name=name, task=task, next=item["next"],
                  repeat=item["repeat"], quiet=quiet)
        only = " I'll tell you only when there's something to report." if quiet else ""
        return f"OK, I'll run \"{name}\" {said}.{only}\nRoutine {item['id']}: \"{task}\""

    def _add_at_place(self, args, name, task):
        if not self.places:
            raise ToolError("Natro has no places here.")
        if str(args.get("at") or "").strip() or str(args.get("repeat") or "").strip():
            raise ToolError("A routine at a place doesn't take at or repeat: every_time, if it's every time.")
        place, event = self.places.trigger(args)
        every, quiet = bool(args.get("every_time")), bool(args.get("quiet"))
        item = {"id": f"rt{int(time.time() * 1000):x}{next(self._ids)}", "name": name, "task": task, "start": None,
                "repeat": "", "next": None, "quiet": quiet, "made": ms(self.now()),
                "from": getattr(self.asking(), "name", ""), "last": None, "place": place["id"], "place_on": event,
                "every": every}
        self.items.append(item)
        self._save()
        self.places.changed()
        log.write("routine", event="set", id=item["id"], name=name, task=task, place=place["name"], on=event, every=every)
        only = " I'll tell you only when there's something to report." if quiet else ""
        return (f"OK, I'll run \"{name}\" {where_words(place, event)}{', every time' if every else ''}.{only}\n"
                f"Routine {item['id']}: \"{task}\"")

    def _place_of(self, item):
        if not self.places or not item.get("place"):
            return None
        return next((place for place in self.places.items if place["id"] == item["place"]), None)

    # For places.py

    def happening(self, place_id):
        return [{"id": item["id"], "kind": "request", "on": item["place_on"], "every": bool(item.get("every")),
                 "text": item["name"]} for item in self.items if item.get("place") == place_id]

    def drop(self, id):
        item = next((item for item in self.items if item["id"] == id), None)
        if item is None:
            return False
        self.items.remove(item)
        self._save()
        self._wake.set()
        if self.places:
            self.places.changed()
        log.write("routine", event="cancelled", id=item["id"], name=item["name"])
        return True

    def place_gone(self, place_id):
        self.items = [item for item in self.items if item.get("place") != place_id]
        self._save()

    def place_event(self, place, event, at, late):
        """He got to a place or left it. One that's late stays for next time: what it would find is about then."""
        for item in list(self.items):
            if item.get("place") != place["id"] or item.get("place_on") != event:
                continue
            if late:
                log.write("routine", event="late", id=item["id"], name=item["name"])
                continue
            if at - item.get("fired", 0) < AGAIN_MINUTES * 60 * 1000:
                continue
            if item.get("every"):
                item["fired"] = at
            else:
                self.items.remove(item)
            self._save()
            self.places.changed()
            if why := self.start(item, False):
                self.ran(item["id"], "skipped", why)
                log.write("routine", event="skipped", id=item["id"], why=why)
            else:
                log.write("routine", event="started", id=item["id"], name=item["name"])

    async def list(self, args):
        if not self.items:
            return "No routines."
        now = self.now()
        lines = ["Routines:"]
        for item in sorted(self.items, key=lambda item: item["next"] or ""):
            repeats = f", {repeat_words(item['repeat'])}" if item["repeat"] else ", once"
            if place := self._place_of(item):
                repeats = f", {where_words(place, item['place_on'])}{', every time' if item.get('every') else ', once'}"
            coming = f", next {when_words(datetime.fromisoformat(item['next']), now)}" if item["next"] else ""
            quiet = ", only when there's something to report" if item["quiet"] else ""
            lines.append(f"- {item['id']}: \"{item['name']}\"{repeats}{coming}{quiet}: \"{item['task']}\"")
            if last := item.get("last"):
                said = f": {last['result']}" if last.get("result") else ""
                lines.append(f"  Last run {when_words(from_ms(last['at']), now)}: {last['state']}{said}")
        return "\n".join(lines)

    async def cancel(self, args):
        item = self._find(args)
        self.drop(item["id"])
        return f"Cancelled the routine \"{item['name']}\"."

    async def run_now(self, args):
        if RUNNING.get() is not None:
            raise ToolError("You're a background job yourself: do this part here.")
        item = self._find(args)
        if why := self.start(item, True):
            raise ToolError(f"\"{item['name']}\" can't run now: {why}")
        log.write("routine", event="run_now", id=item["id"])
        return f"OK, I'm running \"{item['name']}\" now. I'll tell you when it's done."

    def _find(self, args):
        wanted = str(args.get("id") or "").strip()
        item = next((item for item in self.items if item["id"] == wanted), None)
        if item is None:
            raise ToolError(f"There's no routine {wanted!r}: routines_list has their ids.")
        return item

    # From the server

    def ran(self, routine_id, state, result=""):
        """How a run went: done, quiet (nothing to report), failed or cancelled; or skipped (why)."""
        item = next((item for item in self.items if item["id"] == routine_id), None)
        if item is None:  # it ran once, or was cancelled meanwhile
            return
        item["last"] = {"at": ms(self.now()), "state": state, "result": short(result, RESULT_CHARS)}
        self._save()

    # The clock

    async def run(self):
        """Start routines as they come due, forever (the server runs it as a task)."""
        while True:
            self._wake.clear()
            try:
                self.check()
            except Exception as e:  # one bad routine must not stop the clock
                log.write("error", step="routines", error=repr(e))
            try:
                await asyncio.wait_for(self._wake.wait(), self._seconds_to_next())
            except asyncio.TimeoutError:
                pass

    def _seconds_to_next(self):
        times = [datetime.fromisoformat(item["next"]) for item in self.items if item["next"]]
        if not times:
            return CHECK_SECONDS
        return min(CHECK_SECONDS, max(0.0, (min(times) - self.now()).total_seconds()))

    def check(self):
        """Start the routines that are due, and move repeating ones on."""
        now = self.now()
        due = []
        for item in list(self.items):
            if not item["next"] or datetime.fromisoformat(item["next"]) > now:
                continue
            if item["repeat"]:
                rule = item_rule(item)
                # The latest time it was due: several missed while the server was down come as one.
                when = rule.before(now, inc=True) or datetime.fromisoformat(item["next"])
                following = rule.after(now)
                item["next"] = following.isoformat() if following else None
            else:
                when = datetime.fromisoformat(item["next"])
                item["next"] = None
            if item["next"] is None:
                self.items.remove(item)
            due.append((item, when))
        if due:
            self._save()
        for item, when in due:
            if now - when > timedelta(minutes=LATE_MINUTES):
                self.ran(item["id"], "skipped", f"missed at {when:%H:%M} (Natro was off then)")
                log.write("routine", event="missed", id=item["id"], due=when.isoformat())
            elif why := self.start(item, False):
                self.ran(item["id"], "skipped", why)
                log.write("routine", event="skipped", id=item["id"], why=why)
            else:
                log.write("routine", event="started", id=item["id"], name=item["name"])

    # On disk

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in data.get("routines", [])
                if isinstance(item, dict) and item.get("id") and item.get("task")]

    def _save(self):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps({"routines": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(self.path)
