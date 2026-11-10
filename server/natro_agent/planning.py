"""Planning his week, and looking back on it: "plan my week", "fit in three gym sessions", "how did my week go?".

week_plan gathers what she needs to suggest blocks of time: his calendar for the week, the free time in it (between
DAY_HOURS, at least SHORTEST_FREE long) and his open tasks. She says what she'd do and asks; on his yes (the next
request, in words: he can change it first, which a yes-or-no question couldn't take) week_plan_add puts them all on his
calendar at once, each an event marked as a block with the task it's for, if any. One that runs into something on the
calendar, or into another of them, stops all of them, so asking twice adds nothing twice.

week_review looks back: the tasks he finished, the ones that slipped (past their day, or a block for them went by and
they're still open), the rest of his open tasks, how the blocks went and what was on his calendar. What's left she
offers to move, with tasks_update or week_plan_add, again on his yes. A routine can run it ("every Sunday at 8, review
my week"): he isn't waiting then, so she only suggests, and his yes comes with the next request.

Nothing's kept here: the marks on the events are what it knows of past plans. Personal: Claude only.
"""
from datetime import date, datetime, time, timedelta

from natro_agent.config import timezone
from natro_agent.google_tools import (CALENDAR, as_moment, describe_event, event_time, event_times, parse_when,
                                      start_of, task_due)
from natro_agent.tools import Tool, ToolError
from natro_agent.watchers import declined

# The part of a day that counts as free when nothing's on.
DAY_HOURS = (9, 22)
SHORTEST_FREE = timedelta(minutes=30)
BLOCK = timedelta(hours=1)
MOST_DAYS = 31
MOST_BLOCKS = 20
# Enough for any week of his: a calendar cut short here would look free.
MOST_EVENTS = 500
# What marks an event as a block, and the task it's for: its private extended properties, which only this app sees.
MARK, TASK_MARK = "natro_block", "natro_task"


def week_ahead(today):
    """The days to plan when he doesn't say: to this Sunday, or from Friday on, to next Sunday."""
    left = 6 - today.weekday()
    return today, today + timedelta(days=left if today.weekday() < 4 else left + 7)


def as_day(value):
    return value.date() if isinstance(value, datetime) else value


def days_asked(args, first, last):
    """The days args' start and end say (just start: a week from it), else first to last."""
    if str(args.get("start") or "").strip():
        first = as_day(parse_when(str(args["start"])))
        last = first + timedelta(days=6)
    if str(args.get("end") or "").strip():
        last = as_day(parse_when(str(args["end"])))
    if last < first:
        raise ToolError("end is before start.")
    if (last - first).days >= MOST_DAYS:
        raise ToolError(f"At most {MOST_DAYS} days at a time.")
    return first, last


def blocks_time(event):
    """Whether he can't do anything else then: a timed event he didn't decline and that isn't marked free."""
    return "dateTime" in event["start"] and event.get("transparency") != "transparent" and not declined(event)


def busy_times(events):
    return [(event_time(event["start"]), event_time(event["end"]), event) for event in events if blocks_time(event)]


def free_times(day, busy, now):
    """The stretches of a day with nothing on and still to come, as (start, end)."""
    start = datetime.combine(day, time(DAY_HOURS[0]), tzinfo=timezone())
    end = datetime.combine(day, time(DAY_HOURS[1]), tzinfo=timezone())
    if now > start:  # from the next quarter of an hour
        start = now + timedelta(minutes=-(now.minute % 15) % 15, seconds=-now.second, microseconds=-now.microsecond)
    free = []
    for first, last, _ in sorted(busy, key=lambda found: found[0]):
        if last <= start or first >= end:  # another day's, or outside these hours
            continue
        if first - start >= SHORTEST_FREE:
            free.append((start, first))
        start = max(start, last)
    if end - start >= SHORTEST_FREE:
        free.append((start, end))
    return free


def times_words(times):
    return ", ".join(f"{first:%H:%M}-{last:%H:%M}" for first, last in times) or "nothing free"


def block_of(event):
    """The task id a block is for ("" for none), or None if the event isn't a block."""
    marks = (event.get("extendedProperties") or {}).get("private") or {}
    return marks.get(TASK_MARK, "") if MARK in marks else None


def event_line(event):
    """An event as calendar_events shows it, and what makes it different here."""
    task = block_of(event)
    if task is not None:
        return f"{describe_event(event)} (a block he planned{f', for task {task}' if task else ''})"
    return f"{describe_event(event)}{' (shows as free)' if event.get('transparency') == 'transparent' else ''}"


def went_by(event, now):
    return as_moment(event_time(event["end"])) <= now


class Planning:
    """google: a google_tools.Google."""

    def __init__(self, google):
        self.google = google
        self.now = lambda: datetime.now(timezone())

    def tools(self):
        when = "2026-10-06 for a day"
        return [
            Tool("week_plan",
                 "His week to plan, all at once: his calendar, the free time in it day by day and his open tasks. For "
                 "\"plan my week\", \"plan next week\", \"find time for the gym this week\". By default to this Sunday, "
                 "or from Friday on to next Sunday. Suggest blocks from it and ask; add them with week_plan_add on his "
                 "yes.",
                 {"type": "object", "properties": {
                     "start": {"type": "string", "description": f"The first day, if he names one: {when}."},
                     "end": {"type": "string", "description": f"The last day: {when}. Just start: a week from it."},
                 }}, self.plan, "natro"),
            Tool("week_plan_add",
                 "Put the blocks of time he said yes to on his calendar, all at once. If one runs into something on "
                 "his calendar or into another of them, nothing is added: it says which.",
                 {"type": "object", "properties": {"blocks": {"type": "array", "items": {
                     "type": "object", "properties": {
                         "title": {"type": "string", "description": "What it's for, in a few words."},
                         "start": {"type": "string", "description": "His local time: 2026-10-06T17:00."},
                         "end": {"type": "string", "description": "His local time; an hour after start by default."},
                         "task_id": {"type": "string", "description": "The open task it's for, by its id, if any."},
                     }, "required": ["title", "start"]}}}, "required": ["blocks"]},
                 self.add, "natro", changes=True, final=True),
            Tool("week_review",
                 "How his week went, all at once: the tasks he finished, what slipped, the rest of his open tasks, how "
                 "the blocks he planned went and what was on his calendar. For \"review my week\", \"summarize my "
                 "week\", \"how did my week go?\", \"what slipped?\". By default the last 7 days, today included.",
                 {"type": "object", "properties": {
                     "start": {"type": "string", "description": f"The first day, if he names one: {when}."},
                     "end": {"type": "string", "description": f"The last day: {when}. Just start: a week from it."},
                 }}, self.review, "natro"),
        ]

    # Planning

    async def plan(self, args=None):
        now = self.now()
        first, last = days_asked(args or {}, *week_ahead(now.date()))
        if last < now.date():
            raise ToolError("Those days have gone by: week_review looks back.")
        start = max(start_of(first), now)
        events = [event for event in await self.google.event_items(start, start_of(last + timedelta(days=1)),
                                                                   most=MOST_EVENTS) if not declined(event)]
        busy = busy_times(events)
        days = [first + timedelta(days=count) for count in range((last - first).days + 1)]
        free = [f"{day:%a %d %b}: {times_words(free_times(day, busy, now))}" for day in days if day >= now.date()]
        tasks = await self.google.task_items()
        lines = [f"His week to plan, {first:%a %d %b} to {last:%a %d %b}. It's {now:%a %d %b}, {now:%H:%M}.",
                 "Calendar:", *[event_line(event) for event in events], *([] if events else ["Nothing on."]),
                 f"Free time (from {DAY_HOURS[0]:02d}:00 to {DAY_HOURS[1]:02d}:00, at least "
                 f"{int(SHORTEST_FREE.total_seconds() / 60)} minutes):", *free,
                 "Open tasks:", *task_lines(tasks, now.date(), last)]
        return "\n".join(lines)

    async def add(self, args):
        blocks = [self._block(block) for block in args.get("blocks") or []]
        if not blocks:
            raise ToolError("Say which blocks to add (blocks).")
        if len(blocks) > MOST_BLOCKS:
            raise ToolError(f"At most {MOST_BLOCKS} blocks at a time.")
        await self._check_room(blocks)
        added = []
        try:
            for block in sorted(blocks, key=lambda block: block["start"]):
                added.append(await self._put(block))
        except ToolError as e:
            done = f"Added only these: {'; '.join(added)}." if added else "Nothing was added."
            raise ToolError(f"{e} {done}")
        count = "1 block is" if len(added) == 1 else f"{len(added)} blocks are"
        # The first line is the reply when it's all he asked for (final); the ids are for later calls.
        return "\n".join([f"Done, {count} on your calendar.", *added])

    def _block(self, block):
        """A block as week_plan_add was given it, checked: its title, start, end and task."""
        block = block if isinstance(block, dict) else {}
        title = " ".join(str(block.get("title") or "").split())
        if not title or not str(block.get("start") or "").strip():
            raise ToolError("Each block has a title and a start.")
        start = parse_when(str(block["start"]))
        if isinstance(start, date) and not isinstance(start, datetime):
            raise ToolError(f"\"{title}\" needs a time of day: 2026-10-06T17:00.")
        end = parse_when(str(block["end"])) if str(block.get("end") or "").strip() else start + BLOCK
        if not isinstance(end, datetime) or end <= start:
            raise ToolError(f"\"{title}\" ends before it starts.")
        if start < self.now():
            raise ToolError(f"\"{title}\" is in the past ({start:%a %d %b %H:%M}).")
        return {"title": title, "start": start, "end": end, "task": str(block.get("task_id") or "").strip()}

    async def _check_room(self, blocks):
        """A ToolError naming what any of them runs into, with nothing added."""
        ordered = sorted(blocks, key=lambda block: block["start"])
        for one, other in zip(ordered, ordered[1:]):
            if other["start"] < one["end"]:
                raise ToolError(f"\"{one['title']}\" and \"{other['title']}\" overlap: nothing was added.")
        start, end = ordered[0]["start"], max(block["end"] for block in ordered)
        for first, last, event in busy_times(await self.google.event_items(start, end, most=MOST_EVENTS)):
            for block in ordered:
                if block["start"] < last and first < block["end"]:
                    raise ToolError(f"\"{block['title']}\" runs into {describe_event(event)}: nothing was added. Move "
                                    "it, and tell him why.")

    async def _put(self, block):
        start_field, end_field = event_times(block["start"], block["end"])
        marks = {MARK: "1", **({TASK_MARK: block["task"]} if block["task"] else {})}
        event = await self.google.api.call("POST", f"{CALENDAR}/events", body={
            "summary": block["title"], "start": start_field, "end": end_field,
            "extendedProperties": {"private": marks}})
        return describe_event(event)

    # Looking back

    async def review(self, args=None):
        now = self.now()
        first, last = days_asked(args or {}, now.date() - timedelta(days=6), now.date())
        if first > now.date():
            raise ToolError("Those days haven't come yet: week_plan looks ahead.")
        start, end = start_of(first), min(start_of(last + timedelta(days=1)), now)
        events = [event for event in await self.google.event_items(start, end, most=MOST_EVENTS)
                  if not declined(event)]
        finished = await self.google.tasks_done(start, end)
        tasks = await self.google.task_items()
        blocks = [(event, block_of(event)) for event in events if block_of(event) is not None]
        late = {task for event, task in blocks if task and went_by(event, now)}
        slipped = [task for task in tasks if (task_due(task) and task_due(task) <= now.date()) or task["id"] in late]
        others = [task for task in tasks if task not in slipped]
        lines = [f"His week, {first:%a %d %b} to {last:%a %d %b}. It's {now:%a %d %b}, {now:%H:%M}.",
                 "Done:", *[done_line(task) for task in finished], *([] if finished else ["No task marked done."]),
                 "Slipped, still open:", *[slipped_line(task, now.date()) for task in slipped],
                 *([] if slipped else ["Nothing."]),
                 "The rest of his open tasks:", *[open_line(task) for task in others], *([] if others else ["None."])]
        if blocks:
            still_open, done = {task["id"] for task in tasks}, {task["id"] for task in finished}
            lines += ["The blocks he planned:", *[block_line(event, task, still_open, done, now)
                                                  for event, task in blocks]]
        others = [describe_event(event) for event in events if block_of(event) is None]
        lines += ["Calendar:", *(others or ["Nothing on."])]
        return "\n".join(lines)


def task_lines(tasks, today, last):
    """His open tasks by when they're due, for planning."""
    groups = {"Overdue": [], f"Due by {last:%a %d %b}": [], "Due later": [], "No date": []}
    names = list(groups)
    for task in tasks:
        due = task_due(task)
        group = names[3] if due is None else names[0] if due < today else names[1] if due <= last else names[2]
        groups[group].append(open_line(task))
    lines = [line for group, found in groups.items() if found for line in (f"{group}:", *found)]
    return lines or ["None."]


def open_line(task):
    due = f" (due {task_due(task):%a %d %b})" if task.get("due") else ""
    return f"- {task.get('title', '(no title)')}{due} [id {task['id']}]"


def slipped_line(task, today):
    due = task_due(task)
    when = "" if due is None else " (due today)" if due == today else f" (was due {due:%a %d %b})" if due < today \
        else f" (due {due:%a %d %b})"
    return f"- {task.get('title', '(no title)')}{when} [id {task['id']}]"


def done_line(task):
    finished = datetime.fromisoformat(task["completed"].replace("Z", "+00:00")).astimezone(timezone())
    return f"- {task.get('title', '(no title)')} ({finished:%a %d %b})"


def block_line(event, task, still_open, done, now):
    what = event.get("summary", "(no title)")
    when = as_moment(event_time(event["start"]))
    if not went_by(event, now):
        state = "still to come"
    elif not task:
        state = "it went by; no task to tell by whether he did it"
    else:
        state = "its task is still open" if task in still_open else "its task is done" if task in done \
            else "its task is no longer open"
    return f"- {when:%a %d %b}, {when:%H:%M}: {what}: {state}"
