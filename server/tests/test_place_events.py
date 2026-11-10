"""What happens at his places: reminders, requests and actions, fired by the phone's place_event."""
import asyncio
import json
import time

import pytest

from conftest import run
from natro_agent.place_actions import PlaceActions, check_args
from natro_agent.places import Places
from natro_agent.reminders import Reminders
from natro_agent.routines import Routines
from natro_agent.tool_calls import ASK
from natro_agent.tools import Tool, ToolError

GYM = {"id": "pl1", "name": "Gym", "names": ["the club"], "lat": 41.54, "lng": 45.0, "radius": 150, "address": "Rustavi"}
WORK = {"id": "pl2", "name": "Work", "names": [], "lat": 41.7, "lng": 44.8, "radius": 150, "address": ""}
HOUR = 3600 * 1000


class Phone:
    name = "phone"
    label = "phone"
    places = False


def now_ms():
    return int(time.time() * 1000)


def world(tmp_path):
    places = Places(tmp_path / "places.json", {}, key="k", get=lambda params, key: {"status": "ZERO_RESULTS"})
    places.items += [dict(GYM), dict(WORK)]
    reminders = Reminders(tmp_path / "reminders.json", {}, places)
    shown = []
    reminders.deliver = lambda reminder: shown.append(reminder) or True
    routines = Routines(tmp_path / "routines.json", places)
    started = []
    routines.start = lambda routine, asked=False: started.append(routine) or ""
    actions = PlaceActions(tmp_path / "place_actions.json", places)
    return places, reminders, shown, routines, started, actions


def event(places, kind="arrived", id="pl1", at=None):
    places._event(Phone(), {"id": id, "event": kind, **({"at": at} if at else {})})


# Reminders

def test_a_reminder_at_a_place_goes_off_once_when_he_gets_there(tmp_path):
    places, reminders, shown, *_ = world(tmp_path)
    said = run(reminders.set({"text": "Buy milk.", "place": "the club"}))
    assert said.splitlines()[0] == "OK, I'll remind you when you get to Gym."
    event(places, "left")
    event(places, id="pl2")
    assert shown == []  # leaving it, or another place
    event(places)
    assert [reminder["text"] for reminder in shown] == ["Buy milk."]
    assert reminders.items == [] and [item["text"] for item in reminders.fired] == ["Buy milk."]
    event(places)
    assert len(shown) == 1


def test_a_reminder_on_leaving_every_time_but_at_most_once_an_hour(tmp_path):
    places, reminders, shown, *_ = world(tmp_path)
    said = run(reminders.set({"text": "Lights off?", "place": "Gym", "on": "leave", "every_time": True}))
    assert said.splitlines()[0] == "OK, I'll remind you when you leave Gym, every time."
    at = now_ms()
    event(places, "arrived", at=at)
    assert shown == []
    event(places, "left", at=at - 2 * HOUR)
    event(places, "left", at=at - HOUR - 60000)
    assert len(shown) == 1  # less than an hour after the last
    event(places, "left", at=at)
    assert len(shown) == 2 and len(reminders.items) == 1


def test_a_late_reminder_still_goes_off_and_a_waiting_one_shows_later(tmp_path):
    places, reminders, shown, *_ = world(tmp_path)
    run(reminders.set({"text": "Buy milk.", "place": "Gym"}))
    at = now_ms() - HOUR
    event(places, at=at)
    assert shown[0]["due"] == at  # delivery.py says when it was for


def test_a_reminder_at_a_place_needs_the_place_saved_and_takes_no_repeat(tmp_path):
    _, reminders, *_ = world(tmp_path)
    with pytest.raises(ToolError, match="no place called"):
        run(reminders.set({"text": "x", "place": "Carrefour"}))
    with pytest.raises(ToolError, match="doesn't take repeat"):
        run(reminders.set({"text": "x", "place": "Gym", "repeat": "FREQ=DAILY"}))
    with pytest.raises(ToolError, match="exactly one"):
        run(reminders.set({"text": "x", "place": "Gym", "in_minutes": 5}))
    with pytest.raises(ToolError, match="on is arrive or leave"):
        run(reminders.set({"text": "x", "place": "Gym", "on": "soon"}))
    assert reminders.items == []


def test_without_places_a_reminder_cant_be_at_one(tmp_path):
    reminders = Reminders(tmp_path / "reminders.json", {})
    with pytest.raises(ToolError, match="no places"):
        run(reminders.set({"text": "x", "place": "Gym"}))


def test_a_reminder_at_a_place_is_listed_kept_and_cancelled(tmp_path):
    places, reminders, *_ = world(tmp_path)
    run(reminders.set({"text": "Buy milk.", "place": "Gym"}))
    id = reminders.items[0]["id"]
    assert f'- {id}: when you get to Gym: "Buy milk."' in run(reminders.list({}))
    kept = Reminders(tmp_path / "reminders.json", {}, places)
    assert kept.items[0]["place"] == "pl1"
    run(reminders.cancel({"id": id}))
    assert reminders.items == []


def test_the_clock_passes_over_a_reminder_at_a_place(tmp_path):
    _, reminders, shown, *_ = world(tmp_path)
    run(reminders.set({"text": "Buy milk.", "place": "Gym"}))
    reminders.check()
    assert reminders._seconds_to_next() > 0 and shown == [] and len(reminders.items) == 1
    timed, back = reminders.coming(reminders.now())
    assert timed == [] and back == []


# Requests

def test_a_routine_at_a_place_runs_as_a_job_when_he_gets_there(tmp_path):
    places, _, _, routines, started, _ = world(tmp_path)
    said = run(routines.add({"name": "Day ahead", "task": "Tell me what's on today.", "place": "Gym"}))
    assert said.splitlines()[0] == "OK, I'll run \"Day ahead\" when you get to Gym."
    assert "when you get to Gym, once" in run(routines.list({}))
    event(places)
    assert [routine["task"] for routine in started] == ["Tell me what's on today."] and routines.items == []


def test_a_routine_every_time_at_most_once_an_hour_and_late_is_dropped_but_kept(tmp_path):
    places, _, _, routines, started, _ = world(tmp_path)
    run(routines.add({"name": "Day", "task": "x", "place": "Gym", "on": "leave", "every_time": True}))
    event(places, "left", at=now_ms() - 20 * 60 * 1000)  # the phone was offline
    assert started == [] and len(routines.items) == 1
    event(places, "left")
    event(places, "left")
    assert len(started) == 1 and len(routines.items) == 1


def test_a_routine_that_cant_start_says_why(tmp_path):
    places, _, _, routines, _, _ = world(tmp_path)
    routines.start = lambda routine, asked=False: "this month's budget was used up"
    run(routines.add({"name": "Day", "task": "x", "place": "Gym", "every_time": True}))
    event(places)
    assert routines.items[0]["last"]["state"] == "skipped"


def test_a_routine_is_at_a_place_or_a_time(tmp_path):
    _, _, _, routines, _, _ = world(tmp_path)
    with pytest.raises(ToolError, match="doesn't take at or repeat"):
        run(routines.add({"name": "x", "task": "y", "place": "Gym", "at": "2030-01-01T08:00"}))
    with pytest.raises(ToolError, match="Say when"):
        run(routines.add({"name": "x", "task": "y"}))
    assert routines.items == []


def test_the_clock_passes_over_a_routine_at_a_place(tmp_path):
    _, _, _, routines, started, _ = world(tmp_path)
    run(routines.add({"name": "Day", "task": "x", "place": "Gym"}))
    routines.check()
    assert started == [] and len(routines.items) == 1


# Actions

def tool(name, ran, **flags):
    async def go(args):
        ran.append((name, args))
        return "Opened Strava.\n(More for the model.)"
    return Tool(name, name, {"type": "object", "properties": {"app": {"type": "string"}, "device": {"type": "string"}},
                             "required": ["app"]}, go, "phone", **flags)


def with_actions(tmp_path, *tools):
    places, _, _, _, _, actions = world(tmp_path)
    told = []
    actions.offered = lambda: list(tools)
    actions.tell = told.append
    return places, actions, told


async def fired(places, actions, *args, **kwargs):
    event(places, *args, **kwargs)
    await asyncio.gather(*actions._tasks)


def test_an_action_runs_as_it_is_with_no_question(tmp_path):
    ran = []
    places, actions, told = with_actions(tmp_path, tool("phone_open_app", ran, final=True))
    said = run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {"app": "Strava"}}))
    assert said.startswith("OK. When you get to Gym:")
    run(fired(places, actions))
    assert ran == [("phone_open_app", {"app": "Strava"})]
    assert told == ["Opened Strava: you got to Gym."] and actions.items == []


def test_an_action_every_time_at_most_once_an_hour(tmp_path):
    ran = []
    places, actions, _ = with_actions(tmp_path, tool("phone_open_app", ran, final=True))
    run(actions.set({"place": "Gym", "on": "leave", "tool": "phone_open_app", "args": {"app": "x"}, "every_time": True}))
    at = now_ms()

    async def play():
        await fired(places, actions, "left", at=at)
        await fired(places, actions, "left", at=at)
    run(play())
    assert len(ran) == 1 and len(actions.items) == 1


def test_a_late_action_is_dropped_and_stays_for_next_time(tmp_path):
    ran = []
    places, actions, told = with_actions(tmp_path, tool("phone_open_app", ran, final=True))
    run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {"app": "x"}}))
    run(fired(places, actions, at=now_ms() - 16 * 60 * 1000))
    assert ran == [] and told == [] and len(actions.items) == 1
    run(fired(places, actions))
    assert len(ran) == 1


def test_an_action_that_fails_says_so_in_a_sentence(tmp_path):
    async def broken(args):
        raise ToolError("Your owner's phone is offline right now. queue_for_device keeps it.")
    offline = Tool("phone_open_app", "x", {"type": "object", "properties": {}}, broken, "phone", final=True)
    places, actions, told = with_actions(tmp_path, offline)
    run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {}}))
    run(fired(places, actions))
    assert len(told) == 1 and told[0].endswith("when you got to Gym: Your owner's phone is offline right now.")


def test_a_tool_gone_by_then_says_so(tmp_path):
    places, actions, told = with_actions(tmp_path, tool("phone_open_app", [], final=True))
    run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {"app": "x"}}))
    actions.offered = lambda: []
    run(fired(places, actions))
    assert "isn't there any more" in told[0]


@pytest.mark.parametrize("name, flags, words", [
    ("nothing_like_it", None, "no tool named"),
    ("phone_call", {"confirm": True}, "can't do that"),
    ("phone_tap", {}, "isn't one thing done whole"),
    ("remind", {"final": True}, "isn't one thing done whole"),
    ("start_job", {"final": True}, "isn't one thing done whole"),
])
def test_what_cant_be_set_at_a_place(tmp_path, name, flags, words):
    tools = [] if flags is None else [tool(name, [], **flags)]
    _, actions, _ = with_actions(tmp_path, *tools)
    with pytest.raises(ToolError, match=words):
        run(actions.set({"place": "Gym", "tool": name, "args": {"app": "x"}}))
    assert actions.items == []


def test_a_place_that_isnt_saved_is_for_her_to_ask_about(tmp_path):
    _, actions, _ = with_actions(tmp_path, tool("phone_open_app", [], final=True))
    with pytest.raises(ToolError, match="no place called"):
        run(actions.set({"place": "Carrefour", "tool": "phone_open_app", "args": {"app": "x"}}))


@pytest.mark.parametrize("args, words", [
    ({}, "needs app"), ({"app": ""}, "needs app"), ({"app": 5}, "app is a string"),
    ({"app": "x", "colour": "red"}, "takes no argument 'colour'"),
])
def test_an_actions_arguments_are_checked_when_its_set(tmp_path, args, words):
    _, actions, _ = with_actions(tmp_path, tool("phone_open_app", [], final=True))
    with pytest.raises(ToolError, match=words):
        run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": args}))
    assert actions.items == []


def test_check_args_enums_and_booleans():
    schema = {"properties": {"mode": {"type": "string", "enum": ["a", "b"]}, "n": {"type": "integer"}}}
    check_args(schema, {"mode": "a", "n": 1})
    check_args({"properties": {}}, {"anything": 1})  # says nothing of what it takes
    with pytest.raises(ToolError, match="one of"):
        check_args(schema, {"mode": "c"})
    with pytest.raises(ToolError, match="n is a integer"):
        check_args(schema, {"n": True})


def test_who_it_says_it_on_is_where_it_was_asked(tmp_path):
    class Asker:
        name = "pc"
    ran = []
    places, actions, _ = with_actions(tmp_path, tool("say_out_loud", ran, final=True))
    actions.asking = lambda: Asker()
    run(actions.set({"place": "Gym", "tool": "say_out_loud", "args": {"app": "x"}}))
    assert actions.items[0]["args"]["device"] == "pc"


def test_an_action_is_kept_and_a_bad_line_passed_over(tmp_path):
    places, actions, _ = with_actions(tmp_path, tool("phone_open_app", [], final=True))
    run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {"app": "x"}}))
    assert PlaceActions(tmp_path / "place_actions.json", places).items[0]["tool"] == "phone_open_app"
    (tmp_path / "place_actions.json").write_text(json.dumps([{"id": "x"}, 5]), encoding="utf-8")
    assert PlaceActions(tmp_path / "place_actions.json", places).items == []


# All of them, at a place

def test_what_happens_at_a_place_is_listed_stopped_and_goes_with_it(tmp_path):
    places, reminders, _, routines, _, actions = world(tmp_path)
    actions.offered = lambda: [tool("phone_open_app", [], final=True)]
    run(reminders.set({"text": "Buy milk.", "place": "Gym"}))
    run(routines.add({"name": "Day ahead", "task": "x", "place": "Gym", "on": "leave", "every_time": True}))
    run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {"app": "x"}}))
    run(reminders.set({"text": "Elsewhere.", "place": "Work"}))
    there = places.happening("pl1")
    assert [(each["kind"], each["on"], each["every"]) for each in there] == [
        ("reminder", "arrived", False), ("request", "left", True), ("action", "arrived", False)]
    listed = {tool.name: tool for tool in places.tools()}["places_list"]
    words = run(listed.run({}))
    assert "When you get to Gym, once: reminder" in words and "When you leave Gym, every time: request" in words
    places.stop_happening(there[1]["id"])
    assert len(places.happening("pl1")) == 2 and routines.items == []
    with pytest.raises(ToolError, match="gone"):
        places.stop_happening("nope")
    assert places.forget("Gym") == "Forgot Gym, and the 2 things set to happen there."
    assert places.happening("pl1") == [] and actions.items == [] and len(reminders.items) == 1


def test_one_that_breaks_doesnt_stop_the_others(tmp_path, log_dir):
    places, reminders, shown, *_ = world(tmp_path)
    run(reminders.set({"text": "Buy milk.", "place": "Gym"}))

    class Broken:
        def place_event(self, *args):
            raise ValueError("no")
    places.users.insert(0, Broken())
    event(places)
    assert len(shown) == 1


def test_a_page_hears_of_each_change(tmp_path):
    places, reminders, _, routines, _, actions = world(tmp_path)
    actions.offered = lambda: [tool("phone_open_app", [], final=True)]
    changes = []
    places.changed = lambda: changes.append(1)
    run(reminders.set({"text": "a", "place": "Gym"}))
    run(routines.add({"name": "b", "task": "c", "place": "Gym"}))
    run(actions.set({"place": "Gym", "tool": "phone_open_app", "args": {"app": "x"}}))
    assert len(changes) == 3
    event(places, "left")
    assert len(changes) == 3  # nothing was set for leaving
    run(fired(places, actions))
    assert len(changes) == 3 + 3


def test_an_event_with_a_time_to_come_counts_as_now(tmp_path):
    places, reminders, shown, *_ = world(tmp_path)
    run(reminders.set({"text": "Buy milk.", "place": "Gym"}))
    event(places, at=now_ms() + 10 * HOUR)
    assert shown[0]["due"] <= now_ms()


def test_a_message_nobody_can_answer_about_is_not_kept(tmp_path):
    # No one to ask (nothing set tool_calls.ASK): the same as a no.
    send = Tool("phone_send_sms", "x", {"type": "object", "properties": {}}, None, "phone", confirm=True)
    places, actions, told = with_actions(tmp_path, send)
    said = run(actions.set({"place": "Gym", "tool": "phone_send_sms", "args": {"number": "5", "text": "Hi"}}))
    assert said.startswith("I didn't keep it.") and actions.items == []
    run(fired(places, actions))
    assert told == []


def test_a_saved_message_goes_as_it_was_approved_whatever_the_model_says_later(tmp_path):
    # What goes is what's kept: nothing runs through the model when the place fires.
    ran = []

    async def goes(args):
        ran.append(("phone_send_sms", args))
        return "Sent."
    send = Tool("phone_send_sms", "x", {"type": "object", "properties": {"number": {"type": "string"},
                                                                         "text": {"type": "string"}},
                                        "required": ["number", "text"]}, goes, "phone", confirm=True)
    places, actions, told = with_actions(tmp_path, send)

    async def yes(question, details):
        return True

    async def play():
        ASK.set(yes)
        await actions.set({"place": "Gym", "on": "leave", "tool": "phone_send_sms",
                           "args": {"number": "5", "text": "Hi"}})
    run(play())
    run(fired(places, actions, "left"))
    assert ran == [("phone_send_sms", {"number": "5", "text": "Hi"})] and told == ['Texted 5 "Hi": you left Gym.']
