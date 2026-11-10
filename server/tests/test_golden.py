"""The golden list: what Natro must always do, checked over her real tools with scripted models.

Nothing here calls a paid API. Model replies are scripted, and every tool is the real one's
description (name, confirm, changes, final) with a recorder in place of its run, so these fail
when a tool is added, renamed or re-flagged wrongly, or when the routing or confirmation code
changes. Four promises:

1. Plain requests go to Haiku; personal or risky ones to Sonnet (the PC's to Haiku), which keeps
   the rest of their conversation.
2. Risky actions always ask, and a no stops them.
3. When a device is offline, she says so and doesn't pretend.
4. Each plain request ends in the right tools, on the right device, with the right questions.

A tool that asks first must be in the list below, so asking is decided on purpose, and a tool whose name
sounds risky must ask.
"""
import asyncio
import re

import pytest

import golden
from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent.agent import Agent, pc_task, personal_or_risky

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
SECRET = "Tuxa's sister is Nino."

# --- The decisions, written down ----------------------------------------------------------------

# Tools that must ask first. Those in OWN_QUESTION ask in their own words, once they know what to ask.
ASK_FIRST_TOOLS = {
    "pc_force_close_app", "calendar_delete_event", "tasks_delete", "gmail_send", "docs_replace",
    "forget", "notes_delete", "phone_call", "phone_send_sms", "phone_reply_message", "phone_tap_risky",
    "pc_run_command_risky", "pc_act_risky", "pc_browser_act_risky",
    "send_message",  # asks in its own words: whom, where and what, before anything opens
    "people_forget", "places_forget", "card_forget",
    "place_action",  # asks in its own words, only for a message: anything else that asks first it refuses
}
OWN_QUESTION = {"send_message", "place_action"}

# A tool whose name says it deletes, sends, calls, kills or pays must be on the list above.
RISKY_NAME = re.compile(r"delete|send|call|force|replace|risky|forget|pay|buy", re.IGNORECASE)

# Requests that plainly involve personal data or a risky action: Sonnet answers them (Haiku the PC's).
PERSONAL_REQUESTS = [
    "what's on my calendar tomorrow", "read my latest email", "do I have any unread mail",
    "email Nino that I'm late", "add milk to my shopping list", "remember that my dentist is on Friday",
    "forget my dentist appointment", "what did Nino text me", "call Nino", "send Nino a message",
    "reply to Nino on WhatsApp that I'm on my way", "answer the message from Giorgi",
    "delete that note", "find the invoice file on my PC", "open the document about taxes",
    "what do you know about me", "any new notifications", "remind me to call mom at 6",
    "what's in my Drive", "share the file with Nino", "what is my address", "send my last photo to the PC",
    "brief me", "what's my day like", "give me my morning briefing",
    "plan my week", "plan next week", "make a plan for the coming week", "find time for the gym this week",
    "fit in three runs", "am I free on Friday", "review my week", "how did my week go", "summarize this week",
    "what slipped", "what did I get done", "do my weekly review",
    "what are you working on", "in the background, compare flights to Berlin", "cancel that job",
    "let me know when the bank texts me", "always tell me when Nino emails", "stop the meeting heads-ups",
    "what are you watching for", "convert the newest PDF in my Downloads to Word",
    "what was that restaurant I asked about on Tuesday", "what did you tell me yesterday about the flight",
    "how long does it take to get to the airport", "when should I leave for the dentist",
    "how do I get to Vake Park from here", "open it in Maps", "where am I",
    "every weekday at 8, brief me", "what routines do I have", "run my morning routine now",
    "message Nino on Instagram: running 10 min late", "DM Nino that I'm outside", "Nino's Instagram is nino.k",
    "save where I am as the gym", "what places do you know", "forget the gym", "how long to the gym",
    "remind me to buy milk when I get to Carrefour", "when I leave work, text Nino I'm on my way",
    "when I arrive at the office tell me what's on today",
    "save this as my Carrefour card", "show my voucher", "where's my train ticket",
    "how much money do I have", "what's my balance", "how much did I spend on groceries last month",
    "can I afford a new laptop", "should I buy this phone", "how do I save up for a car", "am I over budget",
    "was that purchase a good idea", "rate my spending this month", "what subscriptions do I have",
    "who owes me money", "when is my salary coming", "what's my net worth", "how much is 2500 lari of my income",
]

# Plain requests: Haiku answers them.
PLAIN_REQUESTS = ["open Chrome", "what's the capital of France", "play Yellow", "turn the flashlight on",
                 "what's 15 percent of 80", "set a timer for ten minutes", "weather in Tbilisi tomorrow",
                 "what does the article at https://example.com/news say", "say out loud good morning"]


# --- Setup --------------------------------------------------------------------------------------

class World:
    """Natro with the real tool catalog, her devices connected, and scripted models."""

    def __init__(self, tmp_path, claude=(), devices=("pc", "phone"), budget=15.0):
        self.calls = []
        own, pc, phone = golden.catalog(tmp_path, self.calls)
        self.claude = FakeClient(*claude)
        self.agent = Agent(own, client=self.claude, budget=budget)
        for device in (pc, phone):
            if device.name in devices:
                self.agent.connect(device)
        self.tools = {tool.name: tool for tool in golden.all_tools(tmp_path)}

    def ask(self, request, answer=True):
        self.owner = Owner(answer)
        return run(self.agent.respond(request, self.owner))


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


# --- The catalog: every tool is flagged on purpose ----------------------------------------------

def test_every_tool_is_decided(tmp):
    tools = golden.all_tools(tmp)
    names = {tool.name for tool in tools}
    assert len(names) == len(tools), "two tools share a name"
    asks = {tool.name for tool in tools if tool.confirm}
    assert asks == ASK_FIRST_TOOLS, f"ask-first tools changed: {asks ^ ASK_FIRST_TOOLS}"


def test_risky_sounding_tools_ask_first(tmp):
    for tool in golden.all_tools(tmp):
        if RISKY_NAME.search(tool.name):
            assert tool.confirm, f"{tool.name} sounds risky but doesn't ask first"


def test_the_phones_tools_are_all_read(tmp):
    # If PhoneTools.kt is reorganized and the reader finds nothing, the checks above would pass empty.
    names = {tool.name for tool in golden.all_tools(tmp) if tool.source == "phone"}
    assert {"phone_call", "phone_send_sms", "phone_tap_risky", "phone_screen", "phone_flashlight"} <= names
    assert len(names) >= 18


def test_the_devices_sending_is_never_offered(tmp):
    # Only the server's send_message calls them, after his yes to its own question.
    names = {tool.name for tool in golden.all_tools(tmp)}
    assert "send_message" in names and not {"phone_send_message", "pc_send_message"} & names


# --- 1. Haiku for plain requests, Sonnet for personal or risky ones ----------------------------

@pytest.mark.parametrize("request_text", PERSONAL_REQUESTS)
def test_personal_requests_go_to_sonnet_but_the_pcs_to_haiku(tmp, request_text):
    assert personal_or_risky(request_text), f"{request_text!r} doesn't look personal to personal_or_risky"
    world = World(tmp, claude=[message(text("OK."))])
    reply = world.ask(request_text)
    assert reply.model == (HAIKU if pc_task(request_text) else SONNET) and len(world.claude.requests) == 1


@pytest.mark.parametrize("request_text", PLAIN_REQUESTS)
def test_plain_requests_go_to_haiku(tmp, request_text):
    world = World(tmp, claude=[message(text("Done."))])
    reply = world.ask(request_text)
    assert reply.model == HAIKU and len(world.claude.requests) == 1


def test_sonnet_keeps_a_conversation_once_it_turned_personal(tmp):
    from natro_agent.memory import Memory
    world = World(tmp, claude=[message(text("Hi!")), message(text("Your sister is Nino.")), message(text("Sure."))])
    memory = Memory(tmp / "memory")
    memory.remember(SECRET, "told")
    world.agent.memory = memory
    world.ask("hello")
    world.ask("what do you know about me")
    world.ask("thanks")
    assert [request["model"] for request in world.claude.requests] == [HAIKU, SONNET, SONNET]
    assert all(SECRET in request["system"][-1]["text"] for request in world.claude.requests)


def test_over_budget_every_request_asks_first(tmp):
    world = World(tmp, claude=[], budget=0.0)
    assert world.ask("capital of France?", answer=False).text == "OK, I won't use Claude for that."
    assert len(world.owner.questions) == 1 and world.claude.requests == []


# --- 2. Risky actions always ask ----------------------------------------------------------------

def test_tools_that_ask_in_their_own_words(tmp):
    own = {tool.name for tool in golden.all_tools(tmp) if tool.own_question}
    assert own == OWN_QUESTION and own <= ASK_FIRST_TOOLS


@pytest.mark.parametrize("tool_name", sorted(ASK_FIRST_TOOLS - OWN_QUESTION))
def test_risky_tool_asks_and_no_stops_it(tmp, tool_name):
    world = World(tmp, claude=[message(text("I'll do that."), tool_use("t1", tool_name), stop="tool_use"),
                               message(text("OK, I left it."))])
    world.ask("please do the risky thing", answer=False)
    assert [question for question, _ in world.owner.questions] == ["I'll do that."]
    assert world.calls == []
    assert world.claude.requests[1]["messages"][-1]["content"][0]["content"] == \
        "Your owner said no, so this was not done."


def test_sending_a_message_asks_in_its_own_words_and_no_stops_it(tmp):
    # The real send_message, with a phone that would send it: her sentence before it isn't the question.
    from natro_agent.people import People
    from natro_agent.sending import Sending

    people = People(tmp / "people.md")
    people.seen("instagram", "Nino Kapanadze", "3402")
    phone = golden.Recording("phone", "phone", [{"name": "send_message", "hidden": True}], [])
    sending = Sending(people, {"phone": phone})
    for answer in (False, True):
        world = World(tmp, claude=[message(text("I'll message Nino."), tool_use("t1", "send_message", to="Nino",
                                                                               text="Late."), stop="tool_use"),
                                   message(text("OK."))])
        world.agent.own_tools = [tool for tool in world.agent.own_tools if tool.name != "send_message"]             + sending.tools()
        phone.all_calls = world.calls
        phone.call_tool = lambda name, args: phone._run(name, args)
        world.ask("message Nino on Instagram: late", answer=answer)
        assert [question for question, _ in world.owner.questions] == ['Send "Late." to Nino Kapanadze on Instagram?']
        assert len(world.calls) == answer


def place_world(tmp):
    """Natro with the real send_message and place_action, a saved place, and a phone that records what it's asked."""
    from natro_agent.people import People
    from natro_agent.place_actions import PlaceActions
    from natro_agent.places import Places
    from natro_agent.sending import Sending

    people = People(tmp / "people.md")
    people.seen("instagram", "Nino Kapanadze", "3402")
    phone = golden.Recording("phone", "phone", [{"name": "send_message", "hidden": True},
                                                *golden.phone_specs()], [])
    places = Places(tmp / "places.json", {"phone": phone})
    places.items.append({"id": "pl1", "name": "Work", "names": [], "lat": 41.7, "lng": 44.8, "radius": 150, "address": ""})
    told = []
    actions = PlaceActions(tmp / "place_actions.json", places)
    actions.sending, actions.tell = Sending(people, {"phone": phone}), told.append

    def world_for(*claude):
        world = World(tmp, claude=[*claude, message(text("OK."))])
        world.agent.own_tools = [tool for tool in world.agent.own_tools if tool.name != "place_action"] + actions.tools()
        phone.all_calls = world.calls
        phone.call_tool = lambda name, args: phone._run(name, args)
        actions.offered = world.agent.offered_tools
        return world

    async def fire():
        places._event(phone, {"id": "pl1", "event": "left"})
        await asyncio.gather(*actions._tasks)

    return world_for, actions, told, fire


def set_message(words):
    return message(tool_use("t1", "place_action", place="Work", on="leave", tool="send_message", every_time=True,
                            args={"to": "Nino", "text": words}), stop="tool_use")


QUESTION = 'When you leave Work, I\'ll send Nino Kapanadze on Instagram: "{}" (every time). OK?'


def test_a_message_at_a_place_no_keeps_nothing(tmp):
    world_for, actions, told, fire = place_world(tmp)
    world = world_for(set_message("On my way."))
    world.ask("when I leave work, message Nino I'm on my way", answer=False)
    assert [question for question, _ in world.owner.questions] == [QUESTION.format("On my way.")]
    assert actions.items == []
    run(fire())
    assert world.calls == [] and told == []


def test_a_message_at_a_place_a_yes_sends_once_it_fires_asking_nothing_then(tmp):
    world_for, actions, told, fire = place_world(tmp)
    world = world_for(set_message("On my way."))
    world.ask("when I leave work, message Nino I'm on my way", answer=True)
    assert len(world.owner.questions) == 1 and world.calls == []  # nothing goes until the place fires
    run(fire())
    assert len(world.owner.questions) == 1  # nothing's asked then
    (name, args), = world.calls
    assert name == "phone_send_message" and args["text"] == "On my way." and args["name"] == "Nino Kapanadze"
    assert told == ['Sent Nino Kapanadze "On my way.": you left Work.']
    run(fire())  # walking in and out, within the hour
    assert len(world.calls) == 1


def test_a_message_at_a_place_changed_words_ask_again(tmp):
    world_for, actions, _, _ = place_world(tmp)
    for words in ("On my way.", "Running late."):
        world = world_for(set_message(words))
        world.ask(f"when I leave work, message Nino: {words}", answer=True)
        assert [question for question, _ in world.owner.questions] == [QUESTION.format(words)]
    assert [item["call"]["text"] for item in actions.items] == ["On my way.", "Running late."]


@pytest.mark.parametrize("tool_name", sorted(ASK_FIRST_TOOLS - {"send_message", "place_action", "phone_send_sms"}))
def test_nothing_else_that_asks_first_can_be_set_at_a_place(tmp, tool_name):
    world_for, actions, told, fire = place_world(tmp)
    world = world_for(message(tool_use("t1", "place_action", place="Work", tool=tool_name, args={}), stop="tool_use"))
    world.ask("when I leave work, do the risky thing", answer=True)
    assert world.owner.questions == [] and actions.items == []  # refused, with nothing asked and nothing kept
    assert "can't do that" in world.claude.requests[1]["messages"][-1]["content"][0]["content"]
    run(fire())
    assert world.calls == []


def test_a_text_at_a_place_asks_too(tmp):
    world_for, actions, told, fire = place_world(tmp)
    world = world_for(message(tool_use("t1", "place_action", place="Work", on="leave", tool="phone_send_sms",
                                       args={"number": "555123", "text": "Late."}), stop="tool_use"))
    world.ask("when I leave work, text 555123 late", answer=True)
    assert [question for question, _ in world.owner.questions] == [
        'When you leave Work, I\'ll text 555123: "Late." (once). OK?']
    run(fire())
    assert world.calls == [("phone_send_sms", {"number": "555123", "text": "Late."})]
    assert told == ['Texted 555123 "Late.": you left Work.'] and actions.items == []  # once


@pytest.mark.parametrize("tool_name", sorted(ASK_FIRST_TOOLS - OWN_QUESTION))
def test_risky_tool_runs_once_after_a_yes(tmp, tool_name):
    world = World(tmp, claude=[message(text("I'll do that."), tool_use("t1", tool_name, x="1"), stop="tool_use"),
                               message(text("Done."))])
    world.ask("please do the risky thing", answer=True)
    assert len(world.owner.questions) == 1
    assert world.calls == [(tool_name, {"x": "1"})]


def test_risky_tool_never_runs_when_nobody_can_answer(tmp):
    # The Device base class answers no, as when a device can't be asked.
    from natro_agent.tools import Device
    world = World(tmp, claude=[message(tool_use("t1", "gmail_send", to="a@b.c", subject="s", body="b"),
                                       stop="tool_use"), message(text("I didn't send it."))])
    run(world.agent.respond("email a@b.c hello", Device()))
    assert world.calls == []


def test_many_changes_in_one_request_ask_once_and_no_stops_all(tmp):
    adds = [tool_use(f"t{i}", "tasks_add", title=f"Task {i}") for i in range(6)]
    world = World(tmp, claude=[message(text("I'll add 6 tasks."), *adds, stop="tool_use"), message(text("OK."))])
    world.ask("add these six tasks", answer=False)
    assert len(world.owner.questions) == 1 and world.calls == []


def test_a_few_changes_just_run(tmp):
    adds = [tool_use(f"t{i}", "tasks_add", title=f"Task {i}") for i in range(3)]
    world = World(tmp, claude=[message(*adds, stop="tool_use"), message(text("Added."))])
    world.ask("add these three tasks")
    assert world.owner.questions == [] and len(world.calls) == 3


def test_reading_and_playing_never_ask(tmp):
    reads = [("calendar_events", {}), ("gmail_search", {"query": "x"}), ("spotify_play", {"query": "Yellow"}),
             ("pc_open_app", {"app": "Chrome"}), ("phone_flashlight", {"on": True}), ("phone_screen", {}),
             ("finance_now", {}), ("financist", {"question": "can I afford a laptop?"})]
    for name, args in reads:
        world = World(tmp, claude=[message(tool_use("t1", name, **args), stop="tool_use"), message(text("Done."))])
        world.ask("go on")
        assert world.owner.questions == [] and world.calls == [(name, args)], name


# --- 3. Offline devices -------------------------------------------------------------------------

def test_a_device_that_is_offline_is_said_to_be_offline(tmp):
    world = World(tmp, claude=[message(text("Your PC is offline."))], devices=("phone",))
    world.ask("open Chrome", answer=True)
    request = world.claude.requests[0]
    assert "Each message's first line says which are online" in request["system"][0]["text"]
    assert "online: phone" in request["messages"][-1]["content"]
    assert not any(tool["name"].startswith("pc_") for tool in request["tools"])  # it never connected


def test_no_devices_means_no_device_tools_and_it_says_so(tmp):
    world = World(tmp, claude=[message(text("Both are offline."))], devices=())
    world.ask("turn the flashlight on")
    request = world.claude.requests[0]
    assert "online: no devices" in request["messages"][-1]["content"]
    assert not any(tool["name"].startswith(("pc_", "phone_")) for tool in request["tools"])


def test_a_device_that_went_offline_keeps_its_tools_and_they_say_so(tmp):
    # Its tools stay listed, so the instructions and tools are the ones cached while it was online.
    world = World(tmp, claude=[message(text("Hi.")), message(tool_use("t1", "pc_open_app", app="Chrome"),
                                                             stop="tool_use"), message(text("Your PC is offline."))])
    world.ask("hi")
    world.agent.disconnect(world.agent.devices["pc"])
    world.agent.new_conversation()
    world.ask("open Chrome")
    online, offline = world.claude.requests[0], world.claude.requests[1]
    assert offline["system"] == online["system"] and offline["tools"] == online["tools"]
    assert "online: phone" in offline["messages"][-1]["content"]
    result = world.claude.requests[2]["messages"][-1]["content"][0]
    assert result["is_error"] and "offline" in result["content"] and "queue_for_device" in result["content"]
    assert world.calls == []


def test_a_tool_on_a_device_that_just_left_returns_an_error_not_a_success(tmp):
    world = World(tmp, claude=[message(text("Hi.")), message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
                               message(text("Your PC is offline."))])
    world.ask("hi")
    world.agent.disconnect(world.agent.devices["pc"])
    world.ask("open Chrome")
    result = world.claude.requests[2]["messages"][-1]["content"][0]
    assert result["is_error"] and "offline" in result["content"] and world.calls == []


# --- 4. Plain requests end in the right tools ---------------------------------------------------

SEND_PHOTO = ("copy_to_device", {"from_device": "phone", "path": "/storage/emulated/0/DCIM/Camera/a.jpg",
                                 "to_device": "pc"})

REMIND_OVEN = ("remind", {"text": "Check the oven.", "in_minutes": 20})

WATCH_NINO = ("watch", {"kind": "email", "from": "Nino"})

NEWEST_PDF = ("pc_search_files", {"query": "ext:pdf"})
AIRPORT_BY_BUS = ("travel_time", {"destination": "Tbilisi airport", "mode": "transit"})
AIRPORT_OPTIONS = ("travel_routes", {"destination": "Tbilisi airport", "prefer": "fewer_changes"})
METRO_THEN_BUS = ("travel_plan", {"legs": [{"to": "Station Square metro station, Tbilisi", "use": ["metro"]},
                                           {"to": "Tbilisi airport", "use": ["bus"]}]})
HERE = ("phone_location", {})
VAKE_FROM_HERE = ("travel_routes", {"destination": "Vake Park", "origin": "41.69339,44.80153"})
NAVIGATE = ("phone_open_maps", {"link": "https://www.google.com/maps/dir/?api=1&destination=Vake+Park&travelmode=transit",
                                "navigate": True})
RESTAURANT = ("history_search", {"words": "restaurant", "start": "2026-10-13", "end": "2026-10-13"})
PDF_TO_WORD = ("convert_file", {"from_device": "pc", "path": "C:/Users/Tuxa/Downloads/report.pdf", "to": "docx"})
COMPRESS_PDF = ("edit_pdf", {"action": "compress", "from_device": "pc", "paths": ["C:/Users/Tuxa/Downloads/report.pdf"]})
GOOD_MORNING = ("say_out_loud", {"text": "Good morning."})
DINNER_ON_PC = ("say_out_loud", {"text": "Dinner's ready.", "device": "pc"})
MESSAGE_NINO = ("send_message", {"to": "Nino", "text": "Running 10 min late.", "app": "instagram"})
MESSAGE_NINO_LATE = ("send_message", {"to": "Nino", "text": "I'm running late."})

FLATS_JOB = ("start_job", {"task": "Find three flats in Tbilisi under 600 dollars a month and put them in a note."})
MORNING_ROUTINE = ("routine_add", {"name": "Morning briefing", "task": "Brief me.", "at": "2026-10-20T08:00",
                                   "repeat": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"})

# (request, scripted Claude replies, the tool calls that must run, the model route: quick, haiku or sonnet)
JOURNEYS = [
    ("pause the music", [], [("spotify_control", {"action": "pause"})], "quick"),
    ("lock my PC", [], [("pc_run_command", {"command": "rundll32.exe user32.dll,LockWorkStation"})], "quick"),
    ("open Calculator", [("pc_open_app", {"app": "Calculator"})], [("pc_open_app", {"app": "Calculator"})], "haiku"),
    ("set an alarm for 7:30", [("phone_set_alarm", {"time": "07:30"})], [("phone_set_alarm", {"time": "07:30"})],
     "haiku"),
    ("what's on my calendar today", [("calendar_events", {})], [("calendar_events", {})], "sonnet"),
    ("find the newest PDF in Downloads", [("pc_search_files", {"query": "ext:pdf"})],
     [("pc_search_files", {"query": "ext:pdf"})], "haiku"),
    ("what's on my PC's screen", [("pc_screen", {})], [("pc_screen", {})], "haiku"),
    ("send my last photo to the PC", [("phone_search_files", {"kind": "image"}), SEND_PHOTO],
     [("phone_search_files", {"kind": "image"}), SEND_PHOTO], "haiku"),
    ("remind me in 20 minutes to check the oven", [REMIND_OVEN], [REMIND_OVEN], "sonnet"),
    ("brief me", [("briefing", {})], [("briefing", {})], "sonnet"),
    ("plan my week", [("week_plan", {})], [("week_plan", {})], "sonnet"),
    ("how did my week go", [("week_review", {})], [("week_review", {})], "sonnet"),
    ("in the background, find three flats under 600 dollars", [FLATS_JOB], [FLATS_JOB], "sonnet"),
    ("every weekday at 8, brief me", [MORNING_ROUTINE], [MORNING_ROUTINE], "sonnet"),
    ("always tell me when Nino emails", [WATCH_NINO], [WATCH_NINO], "sonnet"),
    ("make my newest PDF a Word file", [NEWEST_PDF, PDF_TO_WORD], [NEWEST_PDF, PDF_TO_WORD], "sonnet"),
    ("compress my newest PDF", [NEWEST_PDF, COMPRESS_PDF], [NEWEST_PDF, COMPRESS_PDF], "haiku"),
    ("what was that restaurant I asked about on Tuesday", [RESTAURANT], [RESTAURANT], "sonnet"),
    ("how long to the airport by bus", [AIRPORT_BY_BUS], [AIRPORT_BY_BUS], "sonnet"),
    ("what are my options to get to the airport with fewer changes", [AIRPORT_OPTIONS], [AIRPORT_OPTIONS], "sonnet"),
    ("plan my trip to the airport, first metro then bus", [METRO_THEN_BUS], [METRO_THEN_BUS], "sonnet"),
    ("how do I get to Vake Park from here", [HERE, VAKE_FROM_HERE], [HERE, VAKE_FROM_HERE], "sonnet"),
    ("navigate me there", [NAVIGATE], [NAVIGATE], "sonnet"),
    ("say out loud good morning", [GOOD_MORNING], [GOOD_MORNING], "haiku"),
    ("say on the PC that dinner's ready", [DINNER_ON_PC], [DINNER_ON_PC], "haiku"),
    ("message Nino on Instagram: running 10 min late", [MESSAGE_NINO], [MESSAGE_NINO], "sonnet"),
    ("message Nino I'm running late", [MESSAGE_NINO_LATE], [MESSAGE_NINO_LATE], "sonnet"),
    ("work on the phone", [], [("work_on", {"device": "phone"})], "quick"),
    ("make the PC work on the phone", [], [("work_on", {"device": "phone", "for": "pc"})], "quick"),
    ("from now on, do what I ask on the PC on my phone", [("work_on", {"device": "phone"})],
     [("work_on", {"device": "phone"})], "haiku"),
]


@pytest.mark.parametrize("request_text, script, expected, route", JOURNEYS)
def test_journey(tmp, request_text, script, expected, route):
    replies = []
    for name, args in script:
        # Built whole, as a tool's arguments may have a name of their own (routine_add's).
        replies.append(message({"type": "tool_use", "id": "t1", "name": name, "input": args}, stop="tool_use"))
    if script:
        replies.append(message(text("Done.")))
    world = World(tmp, claude=replies)
    # The quick path needs Spotify's tool to have worked; the recorder says "Done (...)".
    reply = world.ask(request_text)
    assert world.calls == expected
    assert world.owner.questions == []
    assert reply.model == {"quick": "none (quick)", "haiku": HAIKU, "sonnet": SONNET}[route]


REPORT = "C:/Users/Tuxa/Documents/report.docx"
THIS_REQUESTS = [
    ("convert this to a PDF", ("convert_file", {"from_device": "pc", "path": REPORT, "to": "pdf"}), SONNET, 1),
    ("send this to my phone", ("copy_to_device", {"from_device": "pc", "path": REPORT, "to_device": "phone"}), SONNET, 2),
    ("summarize this", ("pc_read_file", {"path": REPORT}), SONNET, 2),
    ("what's this song?", None, HAIKU, 1),
]


@pytest.mark.parametrize("request_text, tool, model, model_calls", THIS_REQUESTS)
def test_this_needs_no_naming_and_no_model_call_to_find_out(tmp, request_text, tool, model, model_calls):
    script = [] if tool is None else [message(tool_use("t1", tool[0], **tool[1]), stop="tool_use")]
    world = World(tmp, claude=[*script, message(text("Done."))])
    reply = run(world.agent.respond(request_text, world.agent.devices["pc"]))
    assert world.calls == [("pc_this", {}), *([tool] if tool else [])]  # asked by the server, before any model
    assert "[in front of him on his PC · Done (pc_this).]" in world.claude.requests[0]["messages"][0]["content"]
    assert len(world.claude.requests) == model_calls and reply.model == model


def test_a_quick_command_that_says_this_runs_as_it_did(tmp):
    world = World(tmp)
    reply = run(world.agent.respond("skip this song", world.agent.devices["pc"]))
    assert reply.model == "none (quick)" and world.calls == [("spotify_control", {"action": "next"})]


def test_a_request_that_doesnt_say_this_asks_the_device_nothing(tmp):
    world = World(tmp, claude=[message(text("Paris."))])
    run(world.agent.respond("what's the capital of France", world.agent.devices["pc"]))
    assert world.calls == []


def test_a_plan_is_added_on_his_yes_with_no_more_model_calls(tmp):
    blocks = {"blocks": [{"title": f"Block {day}", "start": f"2026-11-{day}T18:00"} for day in range(9, 16)]}
    world = World(tmp, claude=[
        message(tool_use("t1", "week_plan"), stop="tool_use"),
        message(text("Monday at six, the gym. Should I add them?")),
        message(tool_use("t2", "week_plan_add", **blocks), stop="tool_use")])
    assert world.ask("plan my week").text == "Monday at six, the gym. Should I add them?"
    assert world.calls == [("week_plan", {})]
    reply = world.ask("yes")
    assert world.calls[1] == ("week_plan_add", blocks)
    assert reply.text == "Done (week_plan_add)."  # its first line
    assert len(world.claude.requests) == 3
    assert reply.model == SONNET
    assert world.owner.questions == []  # all of them, on the yes he already gave


def test_quick_music_commands_never_need_a_model(tmp):
    for phrase in ["pause the music", "next song", "volume up", "resume", "OK Natro, pause it please"]:
        world = World(tmp)
        reply = world.ask(phrase)
        assert reply.model == "none (quick)" and world.claude.requests == [], phrase
        assert len(world.calls) == 1 and world.calls[0][0] == "spotify_control", phrase


def test_the_header_names_the_time_the_device_and_who_is_online(tmp):
    world = World(tmp, claude=[message(text("Hi."))])
    world.ask("hi there friend")
    header = world.claude.requests[0]["messages"][-1]["content"].splitlines()[0]
    # Tbilisi is UTC+4 all year: the offset lets her work out other time zones.
    assert re.fullmatch(r"\[\w+ \d\d \w+ \d{4}, \d\d:\d\d UTC\+4 · from the typed chat · online: PC, phone\]", header)
