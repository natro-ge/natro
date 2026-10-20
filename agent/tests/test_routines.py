"""Routines: setting them up, the clock that starts them, and their runs as titled background jobs."""
import asyncio
from datetime import datetime, timedelta

import pytest

from conftest import FakeClient, message, run, text
from natro_agent import chats
from natro_agent.agent import Agent, personal_or_risky
from natro_agent.config import timezone
from natro_agent.jobs import Job, Jobs
from natro_agent.routines import LATE_MINUTES, QUIET_REPLY, Routines, is_quiet
from natro_agent.server import Server
from natro_agent.tools import ToolError
from test_jobs import job_hello, take
from test_reminders import NOW, Clock
from test_shared_chat import devices

BRIEF = {"name": "Morning briefing", "task": "Brief me.", "at": "2026-10-19T08:00",
         "repeat": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"}
FLIGHT = {"name": "Berlin flight", "task": "Find the cheapest flight from Tbilisi to Berlin on 12 December and tell "
                                          "me its price if it's under 200 dollars.",
          "at": "2026-10-17T18:00", "quiet": True}


def make(tmp_path, now=NOW):
    routines = Routines(tmp_path / "routines.json")
    routines.now = Clock(now)
    routines.started = []
    routines.start = lambda routine, asked=False: routines.started.append((routine["name"], asked)) or ""
    return routines


def add(routines, **args):
    return run(routines.add(args))


# --- Setting them up ----------------------------------------------------------------------------

def test_a_repeating_one(tmp_path):
    routines = make(tmp_path)
    said = add(routines, **BRIEF)
    assert said.splitlines()[0] == "OK, I'll run \"Morning briefing\" every weekday at 08:00, starting on Monday."
    item, = routines.items
    assert said.splitlines()[1] == f"Routine {item['id']}: \"Brief me.\""
    assert datetime.fromisoformat(item["next"]) == datetime(2026, 10, 19, 8, 0, tzinfo=timezone())
    assert item["repeat"] == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" and not item["quiet"]
    # Kept on disk.
    assert Routines(tmp_path / "routines.json").items == routines.items


def test_once_and_quiet(tmp_path):
    routines = make(tmp_path)
    said = add(routines, **FLIGHT)
    assert said.splitlines()[0] == ("OK, I'll run \"Berlin flight\" today at 18:00. I'll tell you only when there's "
                                    "something to report.")
    item, = routines.items
    assert item["quiet"] and item["repeat"] == ""


def test_what_add_refuses(tmp_path):
    routines = make(tmp_path)
    with pytest.raises(ToolError, match="at most every 60 minutes"):
        add(routines, **{**BRIEF, "repeat": "FREQ=MINUTELY;INTERVAL=30"})
    with pytest.raises(ToolError, match="has passed"):
        add(routines, **{**FLIGHT, "at": "2026-10-17T09:00"})
    with pytest.raises(ToolError, match="when it first runs"):
        add(routines, **{**BRIEF, "at": ""})
    with pytest.raises(ToolError, match="a name"):
        add(routines, **{**BRIEF, "task": " "})
    with pytest.raises(ToolError, match="no times left"):
        add(routines, **{**BRIEF, "at": "2026-01-01T08:00", "repeat": "FREQ=DAILY;COUNT=2"})
    assert routines.items == []
    # Every hour is as often as it gets.
    said = add(routines, **{**BRIEF, "at": "2026-10-17T16:00", "repeat": "FREQ=HOURLY"})
    assert said.startswith("OK, I'll run \"Morning briefing\" every hour, starting today at 16:00.")


def test_list_and_cancel(tmp_path):
    routines = make(tmp_path)
    assert run(routines.list({})) == "No routines."
    add(routines, **BRIEF)
    add(routines, **FLIGHT)
    brief, flight = routines.items
    routines.ran(brief["id"], "done", "Sunny, 18 degrees, and two meetings.")
    listed = run(routines.list({})).splitlines()
    assert listed[0] == "Routines:"
    assert listed[1] == (f"- {flight['id']}: \"Berlin flight\", once, next today at 18:00, only when there's something "
                         f"to report: \"{FLIGHT['task']}\"")
    assert listed[2] == f"- {brief['id']}: \"Morning briefing\", every weekday, next on Monday at 08:00: \"Brief me.\""
    assert listed[3] == "  Last run today at 15:00: done: Sunny, 18 degrees, and two meetings."

    assert run(routines.cancel({"id": brief["id"]})) == "Cancelled the routine \"Morning briefing\"."
    assert routines.items == [flight]
    with pytest.raises(ToolError, match="no routine 'rt9'"):
        run(routines.cancel({"id": "rt9"}))


def test_run_now(tmp_path):
    routines = make(tmp_path)
    add(routines, **BRIEF)
    item, = routines.items
    said = run(routines.run_now({"id": item["id"]}))
    assert said == "OK, I'm running \"Morning briefing\" now. I'll tell you when it's done."
    assert routines.started == [("Morning briefing", True)]
    assert routines.items[0]["next"] == item["next"]  # its times stay as they were
    routines.start = lambda routine, asked=False: "its last run is still going"
    with pytest.raises(ToolError, match="can't run now: its last run is still going"):
        run(routines.run_now({"id": item["id"]}))


def test_the_tools_are_personal_and_none_asks(tmp_path):
    tools = {tool.name: tool for tool in make(tmp_path).tools()}
    assert set(tools) == {"routine_add", "routines_list", "routines_cancel", "routines_run"}
    assert all(tool.personal and not tool.confirm for tool in tools.values())
    assert tools["routine_add"].final and tools["routine_add"].changes and tools["routines_run"].final


def test_routine_requests_go_to_sonnet():
    for request in ("every weekday at 8, brief me", "every Friday evening check the flight price",
                    "what routines do I have", "stop my morning routine"):
        assert personal_or_risky(request), request
    assert not personal_or_risky("play everything by Coldplay")


def test_is_quiet():
    assert is_quiet(QUIET_REPLY) and is_quiet("nothing to report") and is_quiet("Nothing to report. Still 240 dollars.")
    assert not is_quiet("The flight is now 180 dollars. Nothing to report otherwise, but book it soon, since prices "
                        "for December usually climb as the holidays get closer.")
    assert not is_quiet("It got cheaper: 180 dollars.")


# --- The clock ----------------------------------------------------------------------------------

def test_a_due_one_starts_and_a_repeating_one_moves_on(tmp_path):
    routines = make(tmp_path)
    add(routines, **BRIEF)
    add(routines, **FLIGHT)
    routines.check()
    assert routines.started == []
    routines.now.time = datetime(2026, 10, 17, 18, 0, 5, tzinfo=timezone())
    routines.check()
    assert routines.started == [("Berlin flight", False)]
    item, = routines.items  # a routine that runs once is gone once it has started
    assert item["name"] == "Morning briefing"
    routines.now.time = datetime(2026, 10, 19, 8, 0, tzinfo=timezone())
    routines.check()
    routines.check()
    assert routines.started[1:] == [("Morning briefing", False)]
    assert datetime.fromisoformat(item["next"]) == datetime(2026, 10, 20, 8, 0, tzinfo=timezone())


def test_long_missed_times_are_skipped_and_several_come_as_one(tmp_path):
    routines = make(tmp_path)
    add(routines, **{**BRIEF, "repeat": "FREQ=DAILY"})
    item, = routines.items
    # Natro was off from Monday 08:00 to Wednesday 08:30: one run, for Wednesday's.
    routines.now.time = datetime(2026, 10, 21, 8, 30, tzinfo=timezone())
    routines.check()
    assert routines.started == [("Morning briefing", False)]
    # Off past the next one by more than LATE_MINUTES: skipped, and said so.
    routines.now.time = datetime(2026, 10, 22, 8, 0, tzinfo=timezone()) + timedelta(minutes=LATE_MINUTES + 1)
    routines.check()
    assert len(routines.started) == 1
    assert item["last"]["state"] == "skipped" and "missed at 08:00" in item["last"]["result"]
    assert datetime.fromisoformat(item["next"]) == datetime(2026, 10, 23, 8, 0, tzinfo=timezone())


def test_a_run_the_server_refuses_is_skipped(tmp_path):
    routines = make(tmp_path)
    add(routines, **{**BRIEF, "at": "2026-10-17T15:00", "repeat": "FREQ=DAILY"})
    routines.start = lambda routine, asked=False: "this month's budget was used up"
    routines.check()
    item, = routines.items
    assert item["last"]["state"] == "skipped" and item["last"]["result"] == "this month's budget was used up"


def test_the_clock_runs_by_itself(tmp_path):
    routines = make(tmp_path, now=None)
    routines.now = lambda: datetime.now(timezone())

    async def go():
        clock = asyncio.create_task(routines.run())
        await asyncio.sleep(0.05)
        soon = datetime.now(timezone()) + timedelta(seconds=0.1)
        await routines.add({"name": "Soon", "task": "Say hi.", "at": soon.isoformat()})
        for _ in range(100):
            if routines.started:
                break
            await asyncio.sleep(0.02)
        clock.cancel()
        return routines.started

    assert run(go()) == [("Soon", False)]


# --- Runs, through the server -------------------------------------------------------------------

BRIEFED = "Sunny, 18 degrees, and two meetings: the dentist at 15:40 and Nino at 19:00.\n\nNo urgent email."


def server_with_routines(tmp_path, *replies, budget=None):
    agent = Agent(client=FakeClient(*replies), budget=budget)
    routines = Routines(tmp_path / "routines.json")
    return Server(agent, jobs=Jobs(), routines=routines), routines


async def due_now(routines, **args):
    """A daily routine that starts now, and the clock looking."""
    now = datetime.now(timezone())
    await routines.add({**BRIEF, "at": f"{now:%Y-%m-%dT%H:%M:%S}", "repeat": "FREQ=DAILY", **args})
    routines.items[-1]["next"] = now.isoformat()
    routines.check()


async def finished(server):
    for _ in range(250):
        if server.jobs.items and all(job.state != "running" for job in server.jobs.items):
            return server.jobs.items
        await asyncio.sleep(0.02)
    raise AssertionError("the routine's run didn't finish")


def test_a_run_is_a_titled_job_shown_everywhere(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server, routines = server_with_routines(tmp_path, message(text(BRIEFED)))

    async def play(pc, phone):
        await due_now(routines)
        return await take(pc, "job_done"), await take(phone, "job_done")

    on_pc, on_phone = run(devices(server, job_hello("pc", "PC"), job_hello("phone", "phone"), play=play))
    assert on_pc["title"] == on_phone["title"] == "Morning briefing" and on_pc["task"] == "Brief me."
    assert on_pc["text"] == BRIEFED and on_pc["say"] == BRIEFED.split("\n\n")[0] and on_pc["ok"]
    assert (on_pc["speak"], on_phone["speak"]) == (False, True)  # neither used lately: the phone says it
    job, = server.jobs.items
    assert job.routine == routines.items[0]["id"] and job.title == "Morning briefing"
    entry = server.chat.entries[f"natro:{job.id}"]
    assert entry["routine"] == "Morning briefing" and entry["job"] == "Brief me." and entry["reply"] == BRIEFED
    request = server.agent.client.requests[0]["messages"][0]["content"]
    assert "[A routine he set up, \"Morning briefing\": it runs by itself now, at " in request
    assert QUIET_REPLY not in request and request.endswith("Brief me.")
    assert routines.items[0]["last"]["state"] == "done"
    assert server.agent.personal_next and "your routine \"Morning briefing\"" in server.agent.private_notes[0]


def test_a_quiet_one_with_nothing_to_report_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server, routines = server_with_routines(tmp_path, message(text(QUIET_REPLY)))

    async def play(pc):
        await due_now(routines, name="Berlin flight", task=FLIGHT["task"], quiet=True)
        await finished(server)
        await asyncio.sleep(0.05)
        return pc

    pc = run(devices(server, job_hello("pc", "PC"), play=play))
    assert not any(isinstance(seen, dict) and seen["type"] == "job_done" for seen in pc.seen)
    request = server.agent.client.requests[0]["messages"][0]["content"]
    assert f"if there's nothing, reply with only \"{QUIET_REPLY}\"" in request
    assert routines.items[0]["last"]["state"] == "quiet"
    assert not server.agent.personal_next and server.agent.private_notes == []
    entry = server.chat.entries[f"natro:{server.jobs.items[0].id}"]
    assert entry["reply"] == QUIET_REPLY and entry["done"]  # the chat shows it ran


def test_over_budget_it_is_skipped_and_he_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server, routines = server_with_routines(tmp_path, budget=0.0)  # no model call scripted: one would fail

    async def play(pc):
        await due_now(routines)
        return await pc.next("alert")

    alert = run(devices(server, {**job_hello("pc", "PC"), "alerts": True}, play=play))
    assert alert["text"] == "I skipped your routine \"Morning briefing\": this month's 0 dollar budget is used up."
    assert server.jobs.items == [] and routines.items[0]["last"]["state"] == "skipped"


def test_its_last_run_still_going_skips_the_next(tmp_path):
    server, routines = server_with_routines(tmp_path)
    run(routines.add(BRIEF))
    item = routines.items[0]
    server.jobs.items.append(Job("jb7", "Brief me.", "", routine=item["id"], title="Morning briefing"))
    assert server._routine(item) == "its last run is still going"


def test_a_job_done_while_no_device_shows_jobs_waits_for_one(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server, _ = server_with_routines(tmp_path)
    job = Job("jb3", "Brief me.", "", title="Morning briefing", routine="rt1")
    server._job_done(job, BRIEFED, ok=True)  # nobody online

    async def play(pc):
        return await take(pc, "job_done")

    done = run(devices(server, job_hello("pc", "PC"), play=play))
    assert done["title"] == "Morning briefing" and done["text"] == BRIEFED and done["speak"]
    assert server._unseen_jobs == []
