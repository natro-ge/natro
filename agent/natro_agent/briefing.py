"""The briefing: his day in one tool call, when he asks ("brief me", "what's my day like?").

briefing gathers at once: the weather where he lives (weather.py), what's left in his calendar today and
what's on tomorrow, tasks overdue or due today or tomorrow, reminders still to come today, and unread
email in Gmail's Primary tab since the last briefing (the last day the first time, at most a week back).
A part that fails says so and the rest still come. Natro words it (NATRO.md, "Your briefing"). Personal:
Claude only.

When the last briefing was, is kept in briefing.json. One asked again within AGAIN_MINUTES (he didn't hear
it, or asks on the other device) covers the same emails as that one, not only what came in between.
"""
import asyncio
import json
from datetime import datetime, time, timedelta

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.google_tools import task_due
from natro_agent.tools import Tool

AGAIN_MINUTES = 60
FIRST_DAYS = 1  # the first briefing's emails: from this many days back
MOST_DAYS = 7  # never further back than this
EMAILS = 5
MOST_TASKS = 15
MOST_REMINDERS = 10


class Briefing:
    """path: briefing.json. google: a google_tools.Google (None: not signed in). weather: a weather.Weather.
    reminders: a reminders.Reminders (None: no reminders, as in the typed chat)."""

    def __init__(self, path, google=None, weather=None, reminders=None):
        self.path = path
        self.google = google
        self.weather = weather
        self.reminders = reminders
        self.now = lambda: datetime.now(timezone())

    def tools(self):
        return [Tool(
            "briefing",
            "His briefing, all at once: the weather where he lives, his calendar for the rest of today and "
            "tomorrow, tasks overdue or due today or tomorrow, reminders still to come today, and unread emails "
            "in his Primary inbox since the last briefing. For \"brief me\", \"what's my day like?\", \"what's "
            "on today?\".",
            {"type": "object", "properties": {}}, self.run, "natro", personal=True)]

    async def run(self, args=None):
        now = self.now()
        since = self._since(now)
        parts = await asyncio.gather(self._weather(), self._calendar(now), self._tasks(now), self._reminders(now),
                                     self._email(since), return_exceptions=True)
        lines = [f"Briefing for {now:%A %d %B}, {now:%H:%M}."]
        for title, part in zip(("Weather", "Calendar", "Tasks", "Reminders", "Email"), parts):
            if isinstance(part, BaseException):
                log.write("error", step="briefing", part=title, error=repr(part))
                part = f"{title}: couldn't get it ({part})."
            lines.append(part)
        if not isinstance(parts[-1], BaseException) and self.google:
            self._save(since, now)
        return "\n\n".join(line for line in lines if line)

    # The parts

    async def _weather(self):
        if not self.weather:
            return ""
        return f"Weather:\n{await self.weather.report(days=2)}"

    async def _calendar(self, now):
        if not self.google:
            return "Calendar, tasks and email: Google isn't signed in."
        tomorrow = now.date() + timedelta(days=1)
        rest, next_day = await asyncio.gather(
            self.google.events(start=now.isoformat(timespec="minutes"),
                               end=datetime.combine(now.date(), time(23, 59), tzinfo=now.tzinfo).isoformat()),
            self.google.events(start=tomorrow.isoformat()))
        rest = "Nothing more today." if rest == "No events then." else rest
        next_day = "Nothing." if next_day == "No events then." else next_day
        return f"Calendar, the rest of today:\n{rest}\nCalendar, tomorrow:\n{next_day}"

    async def _tasks(self, now):
        if not self.google:
            return ""
        today, tomorrow = now.date(), now.date() + timedelta(days=1)
        groups = {"Overdue": [], "Due today": [], "Due tomorrow": []}
        others = 0
        for task in await self.google.task_items():
            due = task_due(task)
            if due is None or due > tomorrow:
                others += 1
                continue
            group = "Overdue" if due < today else "Due today" if due == today else "Due tomorrow"
            when = f" (was due {due:%a %d %b})" if group == "Overdue" else ""
            groups[group].append(f"- {task.get('title', '(no title)')}{when}")
        lines = []
        for group, found in groups.items():
            if found:
                lines += [f"{group}:", *found[:MOST_TASKS]]
                if len(found) > MOST_TASKS:
                    lines.append(f"- and {len(found) - MOST_TASKS} more")
        if not lines:
            lines.append("Nothing due today or tomorrow, nothing overdue.")
        lines.append(f"Other open tasks (no date, or due later): {others}.")
        return "Tasks:\n" + "\n".join(lines)

    async def _reminders(self, now):
        if not self.reminders:
            return ""
        end = datetime.combine(now.date(), time(23, 59, 59), tzinfo=now.tzinfo)
        timed, on_return = self.reminders.coming(end)
        lines = [f"- {moment:%H:%M}: \"{text}\"" for moment, text in timed[:MOST_REMINDERS]]
        if len(timed) > MOST_REMINDERS:
            lines.append(f"- and {len(timed) - MOST_REMINDERS} more today")
        lines += [f"- When his {device} is back: \"{text}\"" for device, text in on_return]
        return "Reminders still to come today:\n" + ("\n".join(lines) or "None.")

    async def _email(self, since):
        if not self.google:
            return ""
        query = f"in:inbox category:primary is:unread after:{int(since.timestamp())}"
        found = await self.google.search_mail(query, max_results=EMAILS)
        found = "None." if found == "No emails match." else found
        return f"Email, unread in Primary since {since:%a %d %b %H:%M}:\n{found}"

    # When the last one was

    def _since(self, now):
        """Where this briefing's emails start: the last briefing (or the one before it, if that was moments ago)."""
        oldest = now - timedelta(days=MOST_DAYS)
        try:
            kept = json.loads(self.path.read_text(encoding="utf-8"))
            at, since = datetime.fromisoformat(kept["at"]), datetime.fromisoformat(kept["since"])
        except (OSError, ValueError, KeyError, TypeError):
            return now - timedelta(days=FIRST_DAYS)
        start = since if now - at < timedelta(minutes=AGAIN_MINUTES) else at
        return max(start, oldest)

    def _save(self, since, now):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps({"at": now.isoformat(), "since": since.isoformat()}), encoding="utf-8")
        partial.replace(self.path)
