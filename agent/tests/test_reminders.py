"""Reminders: setting them, the clock, delivery to the devices (shown on all, spoken on one), snoozing."""
import asyncio
from datetime import datetime, timedelta

import pytest

from conftest import FakeClient, message, run, text
from natro_agent import chats
from natro_agent.agent import Agent
from natro_agent.config import timezone
from natro_agent.reminders import LATE_HOURS, Reminders, from_ms, repeat_words
from natro_agent.server import Server
from natro_agent.tools import ToolError
from test_shared_chat import devices, hello

# A Saturday afternoon where he is.
NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone())


class Clock:
    def __init__(self, now=NOW):
        self.time = now

    def __call__(self):
        return self.time


class Shown:
    """Stands in for the server: records each reminder delivered; online=False means nobody could see it."""

    def __init__(self):
        self.online = True
        self.reminders = []

    def __call__(self, reminder):
        if self.online:
            self.reminders.append(reminder)
        return self.online


def make(tmp_path, devices=None, now=NOW):
    reminders = Reminders(tmp_path / "reminders.json", devices if devices is not None else {})
    reminders.now = Clock(now)
    reminders.deliver = Shown()
    return reminders


def set_one(reminders, **args):
    return run(reminders.set(args))


# --- Setting them -------------------------------------------------------------------------------

def test_in_minutes(tmp_path):
    reminders = make(tmp_path)
    said = set_one(reminders, text="Check the oven.", in_minutes=20)
    assert said.splitlines()[0] == "OK, I'll remind you in 20 minutes."
    item, = reminders.items
    assert datetime.fromisoformat(item["next"]) == NOW + timedelta(minutes=20)
    assert item["text"] == "Check the oven." and item["id"] in said


def test_a_time_today_tomorrow_and_a_day_alone(tmp_path):
    reminders = make(tmp_path)
    assert set_one(reminders, text="Call Nino.", at="2026-10-17T18:30").startswith("OK, I'll remind you today at 18:30.")
    assert set_one(reminders, text="Gym.", at="2026-10-18T08:00").startswith("OK, I'll remind you tomorrow at 08:00.")
    said = set_one(reminders, text="Pay rent.", at="2026-10-22")
    assert said.startswith("OK, I'll remind you on Thursday at 09:00.")
    said = set_one(reminders, text="Renew passport.", at="2026-12-01T10:00")
    assert said.startswith("OK, I'll remind you on Tuesday 1 December at 10:00.")


def test_a_time_gone_by_is_refused_but_just_now_counts_as_now(tmp_path):
    reminders = make(tmp_path)
    with pytest.raises(ToolError, match="has passed"):
        set_one(reminders, text="Late.", at="2026-10-17T14:00")
    set_one(reminders, text="Right now.", at="2026-10-17T14:59:30")
    assert datetime.fromisoformat(reminders.items[0]["next"]) == NOW


def test_exactly_one_when(tmp_path):
    reminders = make(tmp_path)
    for args in ({}, {"at": "2026-10-18T09:00", "in_minutes": 5}):
        with pytest.raises(ToolError, match="exactly one"):
            set_one(reminders, text="Hi.", **args)
    with pytest.raises(ToolError, match="what to remind"):
        set_one(reminders, text="  ", in_minutes=5)


def test_repeating(tmp_path):
    reminders = make(tmp_path)
    said = set_one(reminders, text="Team call.", at="2026-10-19T09:00", repeat="FREQ=WEEKLY;BYDAY=MO;UNTIL=20261231")
    assert said.startswith("OK, I'll remind you every Monday, until 31 December at 09:00, starting on Monday.")
    item, = reminders.items
    assert item["repeat"].startswith("FREQ=WEEKLY;BYDAY=MO;UNTIL=20261231T")  # the end of that day, in UTC
    assert datetime.fromisoformat(item["next"]) == datetime(2026, 10, 19, 9, 0, tzinfo=timezone())
    # Started in the past: the first time still to come.
    set_one(reminders, text="Water.", at="2026-10-01T16:00", repeat="FREQ=DAILY")
    assert datetime.fromisoformat(reminders.items[1]["next"]) == datetime(2026, 10, 17, 16, 0, tzinfo=timezone())
    said = set_one(reminders, text="Stretch.", in_minutes=30, repeat="FREQ=HOURLY;COUNT=3")
    assert said.startswith("OK, I'll remind you every hour, 3 times, starting today at 15:30.")


def test_repeat_words():
    assert repeat_words("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR") == "every day"
    assert repeat_words("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == "every weekday"
    assert repeat_words("FREQ=WEEKLY;BYDAY=MO,TH") == "every Monday and Thursday"
    assert repeat_words("FREQ=WEEKLY;INTERVAL=2") == "every 2 weeks"
    assert repeat_words("FREQ=MINUTELY;INTERVAL=30") == "every 30 minutes"


def test_bad_or_too_frequent_rules_are_refused(tmp_path):
    reminders = make(tmp_path)
    with pytest.raises(ToolError, match="at most every 15 minutes"):
        set_one(reminders, text="Spam.", in_minutes=1, repeat="FREQ=MINUTELY;INTERVAL=5")
    with pytest.raises(ToolError, match="Can't read"):
        set_one(reminders, text="Odd.", in_minutes=1, repeat="FREQ=SOMETIMES")
    with pytest.raises(ToolError, match="no times left"):
        set_one(reminders, text="Over.", at="2026-01-01T09:00", repeat="FREQ=DAILY;COUNT=2")
    assert reminders.items == []


def test_list_and_cancel(tmp_path):
    reminders = make(tmp_path)
    set_one(reminders, text="Call Nino.", at="2026-10-17T18:30")
    set_one(reminders, text="Team call.", at="2026-10-19T09:00", repeat="FREQ=WEEKLY;BYDAY=MO")
    listed = run(reminders.list({}))
    first, second = reminders.items
    assert listed.index(first["id"]) < listed.index(second["id"])
    assert "today at 18:30: \"Call Nino.\"" in listed and "repeats every Monday" in listed
    assert run(reminders.cancel({"id": first["id"]})) == "Cancelled: \"Call Nino.\""
    with pytest.raises(ToolError, match="no reminder"):
        run(reminders.cancel({"id": first["id"]}))
    assert [item["text"] for item in reminders.items] == ["Team call."]
    assert run(make(tmp_path).list({})).count("\"Team call.\"") == 1  # kept on disk


# --- The clock ----------------------------------------------------------------------------------

def test_a_due_reminder_is_delivered_once_and_a_repeating_one_moves_on(tmp_path):
    reminders = make(tmp_path)
    set_one(reminders, text="Check the oven.", in_minutes=20)
    set_one(reminders, text="Water.", at="2026-10-17T15:10", repeat="FREQ=DAILY")
    reminders.check()
    assert reminders.deliver.reminders == []
    reminders.now.time = NOW + timedelta(minutes=25)
    reminders.check()
    reminders.check()
    assert sorted(r["text"] for r in reminders.deliver.reminders) == ["Check the oven.", "Water."]
    assert [item["text"] for item in reminders.items] == ["Water."]
    assert datetime.fromisoformat(reminders.items[0]["next"]) == datetime(2026, 10, 18, 15, 10, tzinfo=timezone())
    assert {r["text"] for r in reminders.fired} == {"Water.", "Check the oven."}


def test_many_missed_times_of_a_repeating_one_come_as_one(tmp_path):
    reminders = make(tmp_path)
    set_one(reminders, text="Stretch.", at="2026-10-17T15:30", repeat="FREQ=HOURLY")
    reminders.now.time = NOW + timedelta(hours=5, minutes=10)  # 20:10: due at 15:30 ... 19:30
    reminders.check()
    shown, = reminders.deliver.reminders
    assert from_ms(shown["due"]) == datetime(2026, 10, 17, 19, 30, tzinfo=timezone())
    assert datetime.fromisoformat(reminders.items[0]["next"]) == datetime(2026, 10, 17, 20, 30, tzinfo=timezone())


def test_while_nobody_can_see_it_it_waits_then_comes_when_a_device_does(tmp_path):
    reminders = make(tmp_path)
    set_one(reminders, text="Call Nino.", in_minutes=10)
    reminders.deliver.online = False
    reminders.now.time = NOW + timedelta(minutes=10)
    reminders.check()
    assert reminders.items == [] and [r["text"] for r in reminders.waiting] == ["Call Nino."]

    class Phone:
        name, reminders = "phone", True

    reminders.deliver.online = True
    reminders.now.time = NOW + timedelta(hours=3)
    reminders.connected(Phone())
    assert [r["text"] for r in reminders.deliver.reminders] == ["Call Nino."]
    assert reminders.waiting == []


def test_too_late_is_dropped(tmp_path):
    reminders = make(tmp_path)
    set_one(reminders, text="Waiting too long.", in_minutes=10)
    set_one(reminders, text="Server was down.", in_minutes=20)
    reminders.deliver.online = False
    reminders.now.time = NOW + timedelta(minutes=10)
    reminders.check()  # the first waits for a device; the second isn't due yet

    class Phone:
        name, reminders = "phone", True

    reminders.deliver.online = True
    reminders.now.time = NOW + timedelta(hours=LATE_HOURS, minutes=30)
    reminders.check()  # the server comes back after more than 12 hours
    reminders.connected(Phone())
    assert reminders.deliver.reminders == [] and reminders.waiting == [] and reminders.items == []


def test_when_a_device_is_back(tmp_path):
    online = {"phone": object()}
    reminders = make(tmp_path, devices=online)
    with pytest.raises(ToolError, match="online now"):
        set_one(reminders, text="Charge it.", when_online="phone")
    said = set_one(reminders, text="Back up the photos.", when_online="pc")
    assert said.startswith("OK, I'll remind you when your PC is back.")
    reminders.now.time = NOW + timedelta(days=3)
    reminders.check()
    assert reminders.deliver.reminders == []

    class OldPC:  # an app that can't show reminders doesn't count
        name, reminders = "pc", False

    class PC:
        name, reminders = "pc", True

    reminders.connected(OldPC())
    assert reminders.deliver.reminders == [] and len(reminders.items) == 1
    reminders.connected(PC())
    shown, = reminders.deliver.reminders
    assert shown["text"] == "Back up the photos." and shown["on"] == "pc" and reminders.items == []


def test_snooze(tmp_path):
    reminders = make(tmp_path)
    set_one(reminders, text="Call Nino.", in_minutes=5)
    reminders.now.time = NOW + timedelta(minutes=5)
    reminders.check()
    shown, = reminders.deliver.reminders
    assert reminders.snooze("nope") is None
    at = reminders.snooze(shown["id"], 10)
    assert at == NOW + timedelta(minutes=15)
    reminders.now.time = at
    reminders.check()
    assert [r["text"] for r in reminders.deliver.reminders] == ["Call Nino.", "Call Nino."]


def test_the_clock_runs_by_itself(tmp_path):
    reminders = make(tmp_path, now=None)
    reminders.now = lambda: datetime.now(timezone())

    async def go():
        clock = asyncio.create_task(reminders.run())
        await asyncio.sleep(0.05)
        await reminders.set({"text": "Soon.", "in_minutes": 0.002})  # 0.12 s
        for _ in range(100):
            if reminders.deliver.reminders:
                break
            await asyncio.sleep(0.02)
        clock.cancel()
        return reminders.deliver.reminders

    shown = run(go())
    assert [r["text"] for r in shown] == ["Soon."]


# --- Delivery, through the server ---------------------------------------------------------------

def server_with_reminders(tmp_path, client=None):
    agent = Agent(client=client or FakeClient())
    reminders = Reminders(tmp_path / "reminders.json", agent.devices)
    return Server(agent, reminders=reminders), reminders


async def due_now(reminders, text="Time to call Nino."):
    await reminders.set({"text": text, "in_minutes": 1})
    reminders.items[-1]["next"] = datetime.now(timezone()).isoformat()
    reminders.check()


def reminder_hello(name, label):
    return {**hello(name, label), "reminders": True}


def test_shown_on_both_and_spoken_on_the_phone_when_neither_was_used_lately(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server, reminders = server_with_reminders(tmp_path)

    async def play(pc, phone):
        await due_now(reminders)
        return await pc.next("reminder"), await phone.next("reminder"), await pc.next("chat_entry")

    on_pc, on_phone, entry = run(devices(server, reminder_hello("pc", "PC"), reminder_hello("phone", "phone"),
                                         play=play))
    assert on_pc["text"] == on_phone["text"] == "Time to call Nino."
    assert (on_pc["speak"], on_phone["speak"]) == (False, True)
    assert on_pc["late"] is False and on_pc["id"] == on_phone["id"]
    entry = entry["entry"]
    assert entry["reminder"] and entry["device"] == "natro" and entry["reply"] == "Time to call Nino."
    assert entry["asked"] == "" and entry["done"]
    assert [r["text"] for r in reminders.fired] == ["Time to call Nino."]


def test_spoken_on_the_device_he_used_last(tmp_path):
    server, reminders = server_with_reminders(tmp_path, FakeClient(message(text("Hi."))))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="hello there friend")
        await pc.next("reply")
        await due_now(reminders)
        return await pc.next("reminder"), await phone.next("reminder")

    on_pc, on_phone = run(devices(server, reminder_hello("pc", "PC"), reminder_hello("phone", "phone"), play=play))
    assert (on_pc["speak"], on_phone["speak"]) == (True, False)


def test_only_apps_that_show_reminders_get_them(tmp_path):
    server, reminders = server_with_reminders(tmp_path)

    async def play(old_pc, phone):
        await due_now(reminders)
        return await phone.next("reminder")

    on_phone = run(devices(server, hello("pc", "PC"), reminder_hello("phone", "phone"), play=play))
    assert on_phone["speak"] is True
    # Once nobody who can show it is online, it waits.
    run(due_now(reminders, "Second one."))
    assert [r["text"] for r in reminders.waiting] == ["Second one."]


def test_a_late_one_says_when_it_was_for(tmp_path):
    server, reminders = server_with_reminders(tmp_path)
    run(reminders.set({"text": "Call Nino.", "in_minutes": 1}))
    was_for = datetime.now(timezone()) - timedelta(minutes=30)
    reminders.items[0]["next"] = was_for.isoformat()

    async def play(phone):
        reminders.check()
        return await phone.next("reminder")

    shown = run(devices(server, reminder_hello("phone", "phone"), play=play))
    assert shown["late"] is True
    assert shown["text"].endswith(f"{was_for:%H:%M}: Call Nino.") and shown["text"].startswith("A reminder from ")


def test_snooze_from_the_phone(tmp_path):
    server, reminders = server_with_reminders(tmp_path)

    async def play(phone):
        await due_now(reminders)
        shown = await phone.next("reminder")
        await phone.send(type="reminder_snooze", id=shown["id"], minutes=10)
        done = await phone.next("reminder_snoozed")
        await phone.send(type="reminder_snooze", id="gone", minutes=10)
        return done, await phone.next("reminder_snoozed")

    done, gone = run(devices(server, reminder_hello("phone", "phone"), play=play))
    assert done["ok"] and done["text"].startswith("Snoozed until ")
    assert not gone["ok"]
    item, = reminders.items
    assert item["text"] == "Time to call Nino." and item["from"] == "snooze"


def test_the_next_request_hears_what_the_reminder_said(tmp_path):
    claude = FakeClient(message(text("Sure.")))
    server, reminders = server_with_reminders(tmp_path, claude)

    async def play(phone):
        await due_now(reminders, "Take your pills.")
        await phone.next("reminder")
        await phone.send(type="text", id="r1", text="remind me again in an hour")
        return await phone.next("reply")

    run(devices(server, reminder_hello("phone", "phone"), play=play))
    header = claude.requests[0]["messages"][-1]["content"].splitlines()[0]
    assert "you reminded him at" in header and "\"Take your pills.\"" in header
    assert server.agent.private_notes == []
