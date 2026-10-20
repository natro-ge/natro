"""Reminders: Natro speaks up at a time, or when his PC or phone is back, with no model call.

remind keeps one in reminders.json, with the words she'll say. The clock (run) sleeps until the next one is
due; then deliver (the server's) shows it on every device that can and speaks it on one. A repeating one
(an RRULE, read like a calendar event's) moves on to its next time. One that's due while no device that
shows reminders is online waits for one, and is dropped (logged) once it's more than LATE_HOURS late; the
same goes for one that came due while the server was down. One for "when my PC is back" goes off the next
time that device connects, and speaks there.

A reminder that went off can be snoozed from the device (the phone's notification): it comes back once,
SNOOZE minutes later. Those that went off in the last day show in reminders_list, so "remind me again in an
hour" knows what "again" is.

Natro to device (those that said "reminders" in their hello):
  {"type": "reminder", "id": "rm3-1760000000000", "text": "Time to call Nino.", "due": 1760000000000,
   "late": false, "speak": true}                 show it; speak it only if speak (and speaking is on there).
                                                  late: it couldn't be delivered on time, and text says when
                                                  it was for
  {"type": "reminder_snoozed", "id": "rm3-1760000000000", "ok": true, "text": "Snoozed until 15:10."}
Device to Natro:
  {"type": "reminder_snooze", "id": "rm3-1760000000000", "minutes": 10}
"""
import asyncio
import itertools
import json
import time
from datetime import UTC, datetime, timedelta

from dateutil.rrule import rrulestr

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.google_tools import parse_when, repeat_rule
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

# Later than this, a reminder that couldn't be delivered is dropped.
LATE_HOURS = 12
# A time this far in the past still counts as now ("at 15:00", said at 15:00:30).
GRACE_SECONDS = 60
# The clock looks at least this often, in case the system clock moved.
CHECK_SECONDS = 60
MOST = 100
MOST_TEXT = 300
# How long reminders that went off are kept, for snoozing and "remind me again".
FIRED_HOURS = 24
SNOOZE = 10
# Repeating more often than this is refused.
MOST_OFTEN_MINUTES = 15
DAY_TIME = (9, 0)  # a day without a time ("on Friday") means this time

DAYS = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday", "FR": "Friday", "SA": "Saturday",
        "SU": "Sunday"}
UNITS = {"MINUTELY": "minute", "HOURLY": "hour", "DAILY": "day", "WEEKLY": "week", "MONTHLY": "month",
         "YEARLY": "year"}


def ms(moment):
    return int(moment.timestamp() * 1000)


def from_ms(value):
    return datetime.fromtimestamp(value / 1000, timezone())


def day_words(moment, now):
    """"today", "tomorrow", "on Friday" (this week), "on Friday 30 October" (later)."""
    days = (moment.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    if 1 < days < 7:
        return f"on {moment:%A}"
    year = f" {moment.year}" if moment.year != now.year else ""
    return f"on {moment:%A} {moment.day} {moment:%B}{year}"


def when_words(moment, now):
    return f"{day_words(moment, now)} at {moment:%H:%M}"


def rule_parts(rule):
    return dict(part.split("=", 1) for part in rule.removeprefix("RRULE:").split(";") if "=" in part)


def repeat_words(rule):
    """An RRULE in words: "every weekday", "every Monday and Thursday", "every 2 weeks, until 31 December"."""
    parts = rule_parts(rule)
    freq, interval = parts.get("FREQ", ""), int(parts.get("INTERVAL", "1") or 1)
    days = [day[-2:] for day in parts.get("BYDAY", "").split(",") if day[-2:] in DAYS]
    if freq == "WEEKLY" and days:
        if set(days) == {"MO", "TU", "WE", "TH", "FR"}:
            what = "every weekday"
        else:
            names = [DAYS[day] for day in days]
            what = "every " + (" and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1])
        if interval > 1:
            what += f", every {interval} weeks"
    else:
        unit = UNITS.get(freq, "time")
        what = f"every {unit}" if interval == 1 else f"every {interval} {unit}s"
    if parts.get("COUNT"):
        what += f", {parts['COUNT']} times"
    if until := parts.get("UNTIL"):
        try:
            last = datetime.strptime(until[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC).astimezone(timezone())
        except ValueError:
            last = datetime.strptime(until[:8], "%Y%m%d")
        what += f", until {last.day} {last:%B}"
    return what


def check_rule(repeat, start, least_minutes=MOST_OFTEN_MINUTES, what="Reminders"):
    """An RRULE he gave, made Google's way (a plain-date UNTIL is the end of that day) and checked: it reads, and
    it doesn't repeat more often than every least_minutes."""
    rule = repeat_rule(repeat, start)[0].removeprefix("RRULE:")
    try:
        rrulestr(rule, dtstart=start)
    except (ValueError, TypeError) as e:
        raise ToolError(f"Can't read the repeat rule {repeat!r} ({e}): use an RRULE like FREQ=WEEKLY;BYDAY=MO.")
    parts = rule_parts(rule)
    interval = int(parts.get("INTERVAL", "1") or 1)
    minutes = {"SECONDLY": 0, "MINUTELY": interval, "HOURLY": 60 * interval}.get(parts.get("FREQ"))
    if minutes is not None and minutes < least_minutes:
        raise ToolError(f"{what} repeat at most every {least_minutes} minutes.")
    return rule


def item_rule(item):
    """A repeating item's times (its start and RRULE), in his time zone."""
    return rrulestr(item["repeat"], dtstart=datetime.fromisoformat(item["start"]).astimezone(timezone()))


class Reminders:
    """path: the JSON file they're kept in. devices: name -> device, while connected (the agent's).

    The server sets deliver(reminder) -> whether any device showed it, and asking() -> the device the request
    being handled came from."""

    def __init__(self, path, devices):
        self.path = path
        self.devices = devices
        self.deliver = lambda reminder: False
        self.asking = lambda: None
        self.now = lambda: datetime.now(timezone())
        self._ids = itertools.count(1)
        self._wake = asyncio.Event()
        self.items, self.waiting, self.fired = self._load()

    def tools(self):
        return [
            Tool("remind",
                 "Remind your owner of something: at a time, after some minutes, repeating, or when his PC or phone "
                 "is next online. When it's due, text is said to him out loud and shown on his devices, word for "
                 "word, with no other context, so write it as you'd say it then, short and complete: \"Time to "
                 "call Nino.\", \"Take the chicken out of the freezer.\" Give exactly one of at, in_minutes or "
                 "when_online. Something he has to do on a day without a set time is a task, not a reminder.",
                 {"type": "object", "properties": {
                     "text": {"type": "string", "description": "What you'll say when it's due."},
                     "at": {"type": "string", "description": "When, his local time: 2026-10-18T09:00. A day alone "
                                                             "means 09:00 that day."},
                     "in_minutes": {"type": "number", "description": "Or: this many minutes from now (\"in 20 "
                                                                    "minutes\": 20, \"in two hours\": 120)."},
                     "repeat": {"type": "string", "description": "To repeat it from at, an RRULE: FREQ=WEEKLY;BYDAY=MO "
                                "(every Monday), FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR (weekdays), FREQ=MONTHLY;BYMONTHDAY=1, "
                                "FREQ=DAILY;UNTIL=20261231, FREQ=HOURLY;COUNT=5."},
                     "when_online": {"type": "string", "enum": list(KNOWN_DEVICES),
                                     "description": "Or: when this device is next online (it's offline now)."},
                 }, "required": ["text"]},
                 self.set, "natro", personal=True, changes=True, final=True),
            Tool("reminders_list",
                 "His reminders still to come (with their ids), and those that went off in the last day.",
                 {"type": "object", "properties": {}}, self.list, "natro", personal=True),
            Tool("reminders_cancel",
                 "Cancel one of his reminders by its id (from reminders_list); a repeating one stops for good. "
                 "To change one, cancel it and set it again.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                 self.cancel, "natro", personal=True, changes=True),
        ]

    # Tools

    async def set(self, args):
        text = " ".join(str(args.get("text") or "").split())
        if not text:
            raise ToolError("Say what to remind him of (text).")
        if len(text) > MOST_TEXT:
            raise ToolError(f"Keep the reminder under {MOST_TEXT} characters.")
        if len(self.items) >= MOST:
            raise ToolError(f"He already has {MOST} reminders: cancel some first.")
        given = [key for key in ("at", "in_minutes", "when_online") if args.get(key) not in (None, "")]
        if len(given) != 1:
            raise ToolError("Give exactly one of at, in_minutes or when_online.")
        now = self.now()
        item = {"id": f"rm{int(time.time() * 1000):x}{next(self._ids)}", "text": text, "next": None, "start": None,
                "repeat": "", "on": None, "made": ms(now), "from": getattr(self.asking(), "name", "")}
        repeat = str(args.get("repeat") or "").strip()
        if "when_online" in given:
            device = args["when_online"]
            if device not in KNOWN_DEVICES:
                raise ToolError(f"when_online is one of: {', '.join(KNOWN_DEVICES)}.")
            if device in self.devices:
                raise ToolError(f"His {KNOWN_DEVICES[device]} is online now: give a time instead.")
            if repeat:
                raise ToolError("A reminder for when a device is back can't repeat.")
            item["on"] = device
            said = f"OK, I'll remind you when your {KNOWN_DEVICES[device]} is back."
        else:
            start = self._start(args, now)
            item["start"] = start.isoformat()
            if repeat:
                item["repeat"] = check_rule(repeat, start)
                first = item_rule(item).after(now - timedelta(seconds=GRACE_SECONDS), inc=True)
                if first is None:
                    raise ToolError("That repeat rule has no times left.")
                item["next"] = first.isoformat()
                if rule_parts(item["repeat"]).get("FREQ") in ("MINUTELY", "HOURLY"):
                    said = f"OK, I'll remind you {repeat_words(item['repeat'])}, starting {when_words(first, now)}."
                else:
                    said = (f"OK, I'll remind you {repeat_words(item['repeat'])} at {first:%H:%M}, starting "
                            f"{day_words(first, now)}.")
            else:
                if start < now - timedelta(seconds=GRACE_SECONDS):
                    raise ToolError(f"{when_words(start, now).capitalize()} has passed: give a time to come.")
                item["next"] = max(start, now).isoformat()
                minutes = args.get("in_minutes")
                if minutes is not None and float(minutes) < 120:
                    minutes = max(1, round(float(minutes)))
                    said = f"OK, I'll remind you in {minutes} minute{'' if minutes == 1 else 's'}."
                else:
                    said = f"OK, I'll remind you {when_words(start, now)}."
        self.items.append(item)
        self._save()
        self._wake.set()
        log.write("reminder", event="set", id=item["id"], text=text, next=item["next"], repeat=item["repeat"],
                  on=item["on"])
        return f"{said}\nReminder {item['id']}: \"{text}\""

    async def list(self, args):
        now = self.now()
        coming = sorted((item for item in self.items if item["next"]), key=lambda item: item["next"])
        lines = []
        for item in coming:
            repeats = f", repeats {repeat_words(item['repeat'])} ({item['repeat']})" if item["repeat"] else ""
            lines.append(f"- {item['id']}: {when_words(datetime.fromisoformat(item['next']), now)}{repeats}: "
                         f"\"{item['text']}\"")
        for item in self.items:
            if item["on"]:
                lines.append(f"- {item['id']}: when his {KNOWN_DEVICES[item['on']]} is back: \"{item['text']}\"")
        lines = ["Still to come:", *lines] if lines else ["No reminders still to come."]
        for item in self.waiting:
            lines.append(f"Waiting for a device to show it (was for {when_words(from_ms(item['due']), now)}): "
                         f"\"{item['text']}\"")
        self._forget_old_fired()
        if self.fired:
            lines.append("Went off in the last day:")
            lines += [f"- {when_words(from_ms(item['due']), now)}: \"{item['text']}\"" for item in self.fired]
        return "\n".join(lines)

    async def cancel(self, args):
        wanted = str(args.get("id") or "").strip()
        for item in self.items:
            if item["id"] == wanted:
                self.items.remove(item)
                self._save()
                self._wake.set()
                log.write("reminder", event="cancelled", id=wanted, text=item["text"])
                return f"Cancelled: \"{item['text']}\""
        raise ToolError(f"There's no reminder {wanted!r}: reminders_list has their ids.")

    def coming(self, until):
        """What goes off from now until then, in order, a repeating one at each of its times: [(time, text)];
        and those for when a device is back: [(its label, text)]. For the briefing."""
        timed = []
        for item in self.items:
            if not item["next"] or (first := datetime.fromisoformat(item["next"])) > until:
                continue
            times = [first] + (item_rule(item).between(first, until) if item["repeat"] else [])
            timed += [(moment, item["text"]) for moment in times]
        on_return = [(KNOWN_DEVICES[item["on"]], item["text"]) for item in self.items if item["on"]]
        return sorted(timed, key=lambda found: found[0]), on_return

    def _start(self, args, now):
        if args.get("in_minutes") not in (None, ""):
            try:
                minutes = float(args["in_minutes"])
            except (TypeError, ValueError):
                raise ToolError("in_minutes is a number.")
            if not 0 < minutes <= 366 * 24 * 60:
                raise ToolError("in_minutes is more than 0 and at most a year.")
            return now + timedelta(minutes=minutes)
        when = parse_when(str(args["at"]))
        if not isinstance(when, datetime):
            when = datetime(when.year, when.month, when.day, *DAY_TIME, tzinfo=timezone())
        return when.astimezone(timezone())

    # The clock

    async def run(self):
        """Deliver reminders as they come due, forever (the server runs it as a task)."""
        while True:
            self._wake.clear()
            try:
                self.check()
            except Exception as e:  # one bad reminder must not stop the clock
                log.write("error", step="reminders", error=repr(e))
            try:
                await asyncio.wait_for(self._wake.wait(), self._seconds_to_next())
            except asyncio.TimeoutError:
                pass

    def _seconds_to_next(self):
        now = self.now()
        times = [datetime.fromisoformat(item["next"]) for item in self.items if item["next"]]
        if not times:
            return CHECK_SECONDS
        return min(CHECK_SECONDS, max(0.0, (min(times) - now).total_seconds()))

    def check(self):
        """Deliver the reminders that are due, and move repeating ones on."""
        now = self.now()
        changed = False
        for item in list(self.items):
            if not item["next"] or datetime.fromisoformat(item["next"]) > now:
                continue
            changed = True
            if item["repeat"]:
                rule = item_rule(item)
                # The latest time it was due: a repeating one may have passed several while nobody could see it.
                last = rule.before(now, inc=True) or datetime.fromisoformat(item["next"])
                following = rule.after(now)
                if following is None:
                    self.items.remove(item)
                else:
                    item["next"] = following.isoformat()
            else:
                last = datetime.fromisoformat(item["next"])
                self.items.remove(item)
            self._due(item, last, now)
        if changed:
            self._save()

    def _due(self, item, when, now, speak_on=None):
        reminder = {"id": f"{item['id']}-{ms(when)}", "reminder": item["id"], "text": item["text"], "due": ms(when),
                    "on": speak_on}
        if now - when > timedelta(hours=LATE_HOURS):
            log.write("reminder", event="dropped", id=reminder["id"], text=item["text"], late_hours=LATE_HOURS)
        elif self.deliver(reminder):
            self._fired(reminder)
        else:
            self.waiting.append(reminder)
            log.write("reminder", event="waiting", id=reminder["id"])

    def _fired(self, reminder):
        self.fired.append(reminder)
        self._forget_old_fired()
        log.write("reminder", event="delivered", id=reminder["id"], text=reminder["text"])

    def _forget_old_fired(self):
        oldest = ms(self.now() - timedelta(hours=FIRED_HOURS))
        self.fired = [item for item in self.fired if item["due"] >= oldest][-50:]

    def connected(self, device):
        """A device came online: reminders waiting for one, and those for when it's back."""
        if not getattr(device, "reminders", False):
            return
        now = self.now()
        waiting, self.waiting = self.waiting, []
        for reminder in waiting:
            if now - from_ms(reminder["due"]) > timedelta(hours=LATE_HOURS):
                log.write("reminder", event="dropped", id=reminder["id"], text=reminder["text"], late_hours=LATE_HOURS)
            elif self.deliver(reminder):
                self._fired(reminder)
            else:
                self.waiting.append(reminder)
        for item in [item for item in self.items if item["on"] == device.name]:
            self.items.remove(item)
            self._due(item, now, now, speak_on=device.name)
        self._save()

    def snooze(self, reminder_id, minutes=SNOOZE):
        """A reminder that went off comes back once, minutes from now. Returns that time, or None if it's gone."""
        reminder = next((item for item in self.fired + self.waiting if item["id"] == reminder_id), None)
        if reminder is None:
            return None
        try:
            minutes = min(max(float(minutes), 1), 24 * 60)
        except (TypeError, ValueError):
            minutes = SNOOZE
        now = self.now()
        at = now + timedelta(minutes=minutes)
        self.items.append({"id": f"rm{int(time.time() * 1000):x}{next(self._ids)}", "text": reminder["text"],
                           "next": at.isoformat(), "start": at.isoformat(), "repeat": "", "on": None,
                           "made": ms(now), "from": "snooze"})
        self._save()
        self._wake.set()
        log.write("reminder", event="snoozed", id=reminder_id, minutes=minutes)
        return at

    # On disk

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], [], []

        def kept(key):
            return [item for item in data.get(key, []) if isinstance(item, dict) and item.get("id") and item.get("text")]
        return kept("reminders"), kept("waiting"), kept("fired")

    def _save(self):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps({"reminders": self.items, "waiting": self.waiting, "fired": self.fired},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(self.path)
