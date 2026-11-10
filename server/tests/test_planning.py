"""Planning his week and looking back on it: free time, blocks added all at once on his yes, and what slipped."""
from datetime import date, datetime, timedelta

import pytest

from conftest import run
from natro_agent.config import timezone
from natro_agent.google_tools import CALENDAR, TASKS, Google, GoogleAPI
from natro_agent.planning import MARK, MOST_BLOCKS, TASK_MARK, Planning, free_times, week_ahead
from natro_agent.tools import ToolError
from test_google import FakeGoogle, FakeResponse

# A Sunday evening where he is, and the week before it.
NOW = datetime(2026, 11, 8, 20, 0, tzinfo=timezone())
EVENTS = f"{CALENDAR}/events"
ALL_TASKS = f"{TASKS}/lists/@default/tasks"


def timed(id, summary, day, start, end, **others):
    return {"id": id, "summary": summary, "start": {"dateTime": f"{day}T{start}:00+04:00"},
            "end": {"dateTime": f"{day}T{end}:00+04:00"}, **others}


def block(id, summary, day, start, end, task=None):
    marks = {MARK: "1", **({TASK_MARK: task} if task else {})}
    return timed(id, summary, day, start, end, extendedProperties={"private": marks})


class FakeCalendar(FakeGoogle):
    """Answers with events and tasks; what's posted comes back as an event, with an id."""

    def __init__(self, events=(), tasks=(), done=(), fail_after=None):
        super().__init__({})
        self.events = list(events)
        self.tasks = list(tasks)
        self.done = list(done)
        self.fail_after = fail_after
        self.added = []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.requests.append((method, url, params, json))
        if method == "POST" and url == EVENTS:
            if self.fail_after is not None and len(self.added) >= self.fail_after:
                return FakeResponse(500, {"error": {"message": "Backend"}})
            self.added.append(json)
            return FakeResponse(200, {**json, "id": f"new{len(self.added)}"})
        if url == EVENTS:
            return FakeResponse(200, {"items": self.events})
        if url == ALL_TASKS:
            return FakeResponse(200, {"items": self.done if params.get("showHidden") == "true" else self.tasks})
        return FakeResponse(200, {})


def planning(now=NOW, **calendar):
    fake = FakeCalendar(**calendar)
    planner = Planning(Google(GoogleAPI(fake)))
    planner.now = lambda: now
    return planner, fake


def events_asked(fake):
    return [params for method, url, params, _ in fake.requests if method == "GET" and url == EVENTS]


# --- The days and the free time ----------------------------------------------------------------

@pytest.mark.parametrize("today, last", [
    (date(2026, 11, 2), date(2026, 11, 8)),  # a Monday: to this Sunday
    (date(2026, 11, 5), date(2026, 11, 8)),  # a Thursday, still
    (date(2026, 11, 6), date(2026, 11, 15)),  # a Friday: on to next Sunday
    (date(2026, 11, 8), date(2026, 11, 15)),  # a Sunday too
])
def test_the_days_to_plan(today, last):
    assert week_ahead(today) == (today, last)


def at(hour, minute=0, day=9):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone())


def busy(*times):
    return [(start, end, {}) for start, end in times]


def test_free_time_between_what_is_on():
    free = free_times(date(2026, 11, 9), busy((at(10), at(16)), (at(16, 15), at(17))), NOW)
    assert free == [(at(9), at(10)), (at(17), at(22))]  # 15 minutes between them isn't worth saying


def test_free_time_leaves_out_other_days_and_hours():
    free = free_times(date(2026, 11, 9), busy((at(10, day=8), at(11, day=8)), (at(10, day=10), at(11, day=10)),
                                              (at(22, 30), at(23))), NOW)
    assert free == [(at(9), at(22))]
    free = free_times(date(2026, 11, 9), busy((at(7), at(9, 30)), (at(21, 45), at(23))), NOW)
    assert free == [(at(9, 30), at(21, 45))]


def test_free_time_today_starts_from_now():
    now = at(14, 7)
    assert free_times(now.date(), [], now) == [(at(14, 15), at(22))]
    assert free_times(now.date(), [], at(21, 40)) == []
    assert free_times(now.date(), [], at(23)) == []


def test_a_day_with_nothing_free_is_empty():
    assert free_times(date(2026, 11, 9), busy((at(8), at(23))), NOW) == []


# --- Planning ----------------------------------------------------------------------------------

def test_the_week_all_at_once():
    planner, fake = planning(
        events=[timed("e1", "Work", "2026-11-09", "10:00", "16:00", recurringEventId="s1"),
                {"id": "e2", "summary": "Nino's birthday", "start": {"date": "2026-11-10"}, "end": {"date": "2026-11-11"}},
                block("b1", "The report", "2026-11-11", "17:00", "18:00", task="t1"),
                block("b2", "Gym", "2026-11-12", "18:00", "19:00")],
        tasks=[{"id": "t0", "title": "Pay rent", "due": "2026-11-02T00:00:00.000Z"},
               {"id": "t1", "title": "Finish the report", "due": "2026-11-12T00:00:00.000Z"},
               {"id": "t2", "title": "Renew passport", "due": "2026-11-30T00:00:00.000Z"},
               {"id": "t3", "title": "Read a book"}])
    text = run(planner.plan({}))
    assert text.startswith("His week to plan, Sun 08 Nov to Sun 15 Nov. It's Sun 08 Nov, 20:00.\nCalendar:\n")
    assert "Mon 09 Nov, 10:00-16:00: Work [id e1; repeats, series s1]\n" in text
    assert "Tue 10 Nov, all day: Nino's birthday [id e2]\n" in text
    assert "Wed 11 Nov, 17:00-18:00: The report [id b1] (a block he planned, for task t1)\n" in text
    assert "Thu 12 Nov, 18:00-19:00: Gym [id b2] (a block he planned)\n" in text
    assert ("Free time (from 09:00 to 22:00, at least 30 minutes):\nSun 08 Nov: 20:00-22:00\n"
            "Mon 09 Nov: 09:00-10:00, 16:00-22:00\nTue 10 Nov: 09:00-22:00\nWed 11 Nov: 09:00-17:00, 18:00-22:00\n"
            "Thu 12 Nov: 09:00-18:00, 19:00-22:00\n") in text
    assert "Sun 15 Nov: 09:00-22:00\nOpen tasks:\n" in text
    assert ("Overdue:\n- Pay rent (due Mon 02 Nov) [id t0]\nDue by Sun 15 Nov:\n- Finish the report (due Thu 12 Nov) "
            "[id t1]\nDue later:\n- Renew passport (due Mon 30 Nov) [id t2]\nNo date:\n- Read a book [id t3]") in text
    asked, = events_asked(fake)
    assert asked["timeMin"] == "2026-11-08T20:00:00+04:00" and asked["timeMax"] == "2026-11-16T00:00:00+04:00"
    assert asked["maxResults"] == 500  # a calendar cut short would look free


def test_what_doesnt_take_up_his_time():
    planner, _ = planning(events=[
        timed("e1", "Lunch he said no to", "2026-11-09", "13:00", "14:00",
              attendees=[{"self": True, "responseStatus": "declined"}]),
        timed("e2", "Maybe", "2026-11-09", "15:00", "16:00", transparency="transparent")])
    text = run(planner.plan({"start": "2026-11-09", "end": "2026-11-09"}))
    assert "Lunch" not in text
    assert "Mon 09 Nov, 15:00-16:00: Maybe [id e2] (shows as free)" in text
    assert "Mon 09 Nov: 09:00-22:00" in text


def test_the_days_he_names():
    planner, fake = planning()
    text = run(planner.plan({"start": "2026-11-16"}))  # a week from it
    assert text.startswith("His week to plan, Mon 16 Nov to Sun 22 Nov.")
    assert events_asked(fake)[0]["timeMin"] == "2026-11-16T00:00:00+04:00"
    assert "Calendar:\nNothing on.\nFree time" in text and text.endswith("Open tasks:\nNone.")
    with pytest.raises(ToolError, match="before start"):
        run(planner.plan({"start": "2026-11-16", "end": "2026-11-10"}))
    with pytest.raises(ToolError, match="At most 31 days"):
        run(planner.plan({"start": "2026-11-16", "end": "2027-02-01"}))
    with pytest.raises(ToolError, match="gone by"):
        run(planner.plan({"start": "2026-10-01", "end": "2026-10-07"}))


# --- Adding them ---------------------------------------------------------------------------------

GYM = {"title": "Gym", "start": "2026-11-09T18:00"}
REPORT = {"title": "  The  report ", "start": "2026-11-10T17:00", "end": "2026-11-10T18:30", "task_id": "t1"}


def test_all_of_them_on_his_yes():
    planner, fake = planning()
    text = run(planner.add({"blocks": [REPORT, GYM]}))
    lines = text.splitlines()
    assert lines[0] == "Done, 2 blocks are on your calendar."  # the reply, when it's all he asked for
    assert lines[1:] == ["Mon 09 Nov, 18:00-19:00: Gym [id new1]", "Tue 10 Nov, 17:00-18:30: The report [id new2]"]
    gym, report = fake.added
    assert gym["start"]["dateTime"].startswith("2026-11-09T18:00") and gym["end"]["dateTime"].startswith("2026-11-09T19:00")
    assert gym["extendedProperties"] == {"private": {MARK: "1"}}
    assert report["summary"] == "The report"
    assert report["extendedProperties"] == {"private": {MARK: "1", TASK_MARK: "t1"}}
    assert run(planning()[0].add({"blocks": [GYM]})).startswith("Done, 1 block is on your calendar.")


def test_a_block_that_runs_into_something_stops_them_all():
    planner, fake = planning(events=[timed("e1", "Dentist", "2026-11-09", "18:30", "19:30")])
    with pytest.raises(ToolError, match=r"\"Gym\" runs into Mon 09 Nov, 18:30-19:30: Dentist \[id e1\]: nothing was added"):
        run(planner.add({"blocks": [REPORT, GYM]}))
    assert fake.added == []


def test_asking_twice_adds_nothing_twice():
    planner, fake = planning()
    run(planner.add({"blocks": [GYM]}))
    fake.events = [block("new1", "Gym", "2026-11-09", "18:00", "19:00")]
    with pytest.raises(ToolError, match="nothing was added"):
        run(planner.add({"blocks": [GYM]}))
    assert len(fake.added) == 1


def test_blocks_that_overlap_each_other_are_refused():
    planner, fake = planning()
    with pytest.raises(ToolError, match="\"Gym\" and \"Run\" overlap"):
        run(planner.add({"blocks": [GYM, {"title": "Run", "start": "2026-11-09T18:30"}]}))
    assert fake.added == []


def test_what_doesnt_take_up_his_time_leaves_room():
    planner, fake = planning(events=[
        {"id": "e1", "summary": "Birthday", "start": {"date": "2026-11-09"}, "end": {"date": "2026-11-10"}},
        timed("e2", "Maybe", "2026-11-09", "18:00", "19:00", transparency="transparent")])
    run(planner.add({"blocks": [GYM]}))
    assert len(fake.added) == 1


@pytest.mark.parametrize("blocks, why", [
    ([], "Say which blocks"),
    ([{"title": "Gym"}], "a title and a start"),
    ([{"start": "2026-11-09T18:00"}], "a title and a start"),
    (["Gym"], "a title and a start"),
    ([{"title": "Gym", "start": "2026-11-09"}], "needs a time of day"),
    ([{"title": "Gym", "start": "2026-11-09T18:00", "end": "2026-11-09T17:00"}], "ends before it starts"),
    ([{"title": "Gym", "start": "2026-11-02T18:00"}], "in the past"),
    ([{"title": f"B{hour}", "start": f"2026-11-{hour + 9:02d}T18:00"} for hour in range(MOST_BLOCKS + 1)], "At most 20"),
])
def test_blocks_that_arent_right(blocks, why):
    planner, fake = planning()
    with pytest.raises(ToolError, match=why):
        run(planner.add({"blocks": blocks}))
    assert fake.added == []


def test_google_failing_halfway_says_what_was_added():
    planner, fake = planning(fail_after=1)
    with pytest.raises(ToolError, match=r"Backend \(HTTP 500\) Added only these: Mon 09 Nov, 18:00-19:00: Gym"):
        run(planner.add({"blocks": [REPORT, GYM]}))
    planner, fake = planning(fail_after=0)
    with pytest.raises(ToolError, match="Nothing was added"):
        run(planner.add({"blocks": [GYM]}))


# --- Looking back --------------------------------------------------------------------------------

def test_the_review():
    planner, fake = planning(
        events=[timed("e1", "Work", "2026-11-02", "10:00", "16:00"),
                block("b1", "The report", "2026-11-03", "17:00", "18:00", task="t1"),
                block("b2", "Pay rent", "2026-11-04", "17:00", "18:00", task="t2"),
                block("b3", "Gym", "2026-11-05", "18:00", "19:00"),
                block("b4", "An old one", "2026-11-06", "18:00", "19:00", task="gone"),
                block("b5", "Later today", "2026-11-08", "21:00", "22:00", task="t5")],
        tasks=[{"id": "t1", "title": "Finish the report", "due": "2026-11-20T00:00:00.000Z"},
               {"id": "t3", "title": "Call the bank", "due": "2026-11-04T00:00:00.000Z"},
               {"id": "t4", "title": "Buy milk", "due": "2026-11-08T00:00:00.000Z"},
               {"id": "t5", "title": "Read a book"},
               {"id": "t6", "title": "Renew passport", "due": "2026-11-30T00:00:00.000Z"}],
        done=[{"id": "t2", "title": "Pay rent", "status": "completed", "completed": "2026-11-04T10:00:00.000Z"},
              {"id": "t7", "title": "No date done", "status": "needsAction"}])
    text = run(planner.review({}))
    assert text.startswith("His week, Mon 02 Nov to Sun 08 Nov. It's Sun 08 Nov, 20:00.\n")
    assert "Done:\n- Pay rent (Wed 04 Nov)\nSlipped, still open:\n" in text
    assert ("Slipped, still open:\n- Finish the report (due Fri 20 Nov) [id t1]\n"  # its block went by
            "- Call the bank (was due Wed 04 Nov) [id t3]\n- Buy milk (due today) [id t4]\nThe rest") in text
    assert ("The rest of his open tasks:\n- Read a book [id t5]\n- Renew passport (due Mon 30 Nov) [id t6]\n"
            "The blocks he planned:\n") in text
    assert ("The blocks he planned:\n- Tue 03 Nov, 17:00: The report: its task is still open\n"
            "- Wed 04 Nov, 17:00: Pay rent: its task is done\n"
            "- Thu 05 Nov, 18:00: Gym: it went by; no task to tell by whether he did it\n"
            "- Fri 06 Nov, 18:00: An old one: its task is no longer open\n"
            "- Sun 08 Nov, 21:00: Later today: still to come\n") in text
    assert text.endswith("Calendar:\nMon 02 Nov, 10:00-16:00: Work [id e1]")
    # Those ticked off in Google's own apps too, and only those of the week.
    done = next(params for method, url, params, _ in fake.requests if url == ALL_TASKS and params.get("showHidden") == "true")
    assert done["showCompleted"] == "true"
    assert done["completedMin"] == "2026-11-02T00:00:00+04:00" and done["completedMax"] == NOW.isoformat()


def test_an_empty_week():
    planner, _ = planning()
    text = run(planner.review({}))
    assert ("Done:\nNo task marked done.\nSlipped, still open:\nNothing.\nThe rest of his open tasks:\nNone.\n"
            "Calendar:\nNothing on.") in text
    assert "blocks he planned" not in text


def test_the_week_he_names():
    planner, fake = planning(now=NOW + timedelta(days=3))
    text = run(planner.review({"start": "2026-11-02", "end": "2026-11-08"}))
    assert text.startswith("His week, Mon 02 Nov to Sun 08 Nov. It's Wed 11 Nov, 20:00.")
    asked, = events_asked(fake)
    assert asked["timeMin"] == "2026-11-02T00:00:00+04:00" and asked["timeMax"] == "2026-11-09T00:00:00+04:00"
    with pytest.raises(ToolError, match="haven't come"):
        run(planner.review({"start": "2026-12-01"}))


# --- As tools ------------------------------------------------------------------------------------

def test_the_tools():
    tools = {tool.name: tool for tool in planning()[0].tools()}
    assert set(tools) == {"week_plan", "week_plan_add", "week_review"}
    assert not any(tool.confirm for tool in tools.values())  # his yes is the next request, in his own words
    assert tools["week_plan_add"].final and tools["week_plan_add"].changes
    assert not tools["week_plan"].changes and not tools["week_review"].changes
