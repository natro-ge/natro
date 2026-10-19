"""The golden list: what Natro must always do, checked over her real tools with scripted models.

Nothing here calls a paid API. Model replies are scripted, and every tool is the real one's
description (name, personal, confirm, changes) with a recorder in place of its run, so these fail
when a tool is added, renamed or re-flagged wrongly, or when the routing or confirmation code
changes. Four promises:

1. Personal data never reaches the free Gemini tier.
2. Risky actions always ask, and a no stops them.
3. When a device is offline, she says so and doesn't pretend.
4. Each plain request ends in the right tools, on the right device, with the right questions.

When a new tool is added, the lists below make you decide, on purpose, whether it is personal,
asks first, or is free for Gemini.
"""
import re

import pytest

import golden
from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent.agent import Agent, needs_claude
from natro_agent.gemini import Gemini
from test_routing import FakeGeminiClient, gemini_calls, gemini_says

SECRET = "Tuxa's sister is Nino."

# --- The decisions, written down ----------------------------------------------------------------

# The only tools the free Gemini tier may ever see: neither personal nor asking first.
FREE_TIER_TOOLS = {
    "web_search", "weather", "web_page",
    "pc_open_app", "pc_close_app", "pc_list_open_apps", "pc_open_link",
    "spotify_play", "spotify_control", "spotify_now_playing", "spotify_like", "spotify_add_to_playlist",
    "spotify_create_playlist",  # makes an empty one and says only its name and URI: nothing of his is read
    "phone_open_app", "phone_set_alarm", "phone_set_timer", "phone_next_alarm", "phone_media",
    "phone_flashlight", "phone_volume",
}

# Tools that must ask first.
ASK_FIRST_TOOLS = {
    "pc_force_close_app", "calendar_delete_event", "tasks_delete", "gmail_send", "docs_replace",
    "forget", "notes_delete", "phone_call", "phone_send_sms", "phone_tap_risky", "pc_run_command_risky",
    "pc_act_risky", "pc_browser_act_risky",
}

# A tool whose name says it deletes, sends, calls, kills or pays must be on the list above.
RISKY_NAME = re.compile(r"delete|send|call|force|replace|risky|forget|pay|buy", re.IGNORECASE)

# Requests that plainly involve personal data or a risky action: they never reach Gemini.
PERSONAL_REQUESTS = [
    "what's on my calendar tomorrow", "read my latest email", "do I have any unread mail",
    "email Nino that I'm late", "add milk to my shopping list", "remember that my dentist is on Friday",
    "forget my dentist appointment", "what did Nino text me", "call Nino", "send Nino a message",
    "delete that note", "find the invoice file on my PC", "open the document about taxes",
    "what do you know about me", "any new notifications", "remind me to call mom at 6",
    "what's in my Drive", "share the file with Nino", "what is my address", "send my last photo to the PC",
    "brief me", "what's my day like", "give me my morning briefing",
    "what are you working on", "in the background, compare flights to Berlin", "cancel that job",
    "let me know when the bank texts me", "always tell me when Nino emails", "stop the meeting heads-ups",
    "what are you watching for", "convert the newest PDF in my Downloads to Word",
    "what was that restaurant I asked about on Tuesday", "what did you tell me yesterday about the flight",
    "how long does it take to get to the airport", "when should I leave for the dentist",
    "how do I get to Vake Park from here", "open it in Maps", "where am I",
]

# Plain requests that are fine on the free tier.
FREE_REQUESTS = ["open Chrome", "what's the capital of France", "play Yellow", "turn the flashlight on",
                 "what's 15 percent of 80", "set a timer for ten minutes", "weather in Tbilisi tomorrow",
                 "what does the article at https://example.com/news say"]


# --- Setup --------------------------------------------------------------------------------------

class World:
    """Natro with the real tool catalog, her devices connected, and scripted models."""

    def __init__(self, tmp_path, claude=(), gemini=None, devices=("pc", "phone"), budget=15.0):
        self.calls = []
        own, pc, phone = golden.catalog(tmp_path, self.calls)
        self.claude = FakeClient(*claude)
        self.gemini = FakeGeminiClient(*(gemini or []))
        self.agent = Agent(own, client=self.claude, budget=budget,
                           gemini=Gemini(client=self.gemini) if gemini is not None else None)
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
    free = {tool.name for tool in tools if not tool.personal and not tool.confirm}
    asks = {tool.name for tool in tools if tool.confirm}
    assert free == FREE_TIER_TOOLS, f"free-tier tools changed: {free ^ FREE_TIER_TOOLS}"
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


def test_screen_and_message_tools_are_personal(tmp):
    personal = {tool.name for tool in golden.all_tools(tmp) if tool.personal}
    assert {"phone_screen", "phone_tap", "phone_type_text", "phone_read_sms", "phone_notifications",
            "phone_contacts", "pc_read_file", "pc_search_files", "pc_search_file_contents", "pc_open_file",
            "spotify_library", "remember", "gmail_read", "gmail_search", "calendar_events", "copy_to_device",
            "phone_search_files", "pc_run_command", "pc_run_command_risky", "pc_screen", "pc_find", "pc_act",
            "pc_act_risky", "pc_switch_window", "pc_wait_for", "pc_screenshot", "phone_screenshot",
            "queue_for_device", "remind", "reminders_list", "reminders_cancel", "briefing", "start_job",
            "jobs_list", "jobs_cancel", "watch", "watchers_list", "watchers_cancel", "convert_file", "history_search", "travel_time", "travel_routes", "travel_plan", "phone_location",
            "phone_open_maps", "pc_browser_open",
            "pc_browser_page", "pc_browser_act", "pc_browser_act_risky", "pc_browser_tabs"} <= personal


# --- 1. Personal data never reaches the free tier -----------------------------------------------

def test_gemini_is_only_offered_the_free_tools(tmp):
    world = World(tmp, gemini=[gemini_says("Paris.")])
    world.ask("what's the capital of France")
    declared = {d.name for tool in world.gemini.requests[0]["config"].tools for d in tool.function_declarations}
    assert declared == FREE_TIER_TOOLS | {"hand_over"}


@pytest.mark.parametrize("request_text", PERSONAL_REQUESTS)
def test_personal_requests_never_reach_gemini(tmp, request_text):
    assert needs_claude(request_text), f"{request_text!r} doesn't look personal to needs_claude"
    world = World(tmp, claude=[message(text("OK."))], gemini=[])
    world.agent.memory = None
    world.ask(request_text)
    assert world.gemini.requests == [] and len(world.claude.requests) == 1


@pytest.mark.parametrize("request_text", FREE_REQUESTS)
def test_plain_requests_start_on_gemini_and_cost_nothing(tmp, request_text):
    world = World(tmp, gemini=[gemini_says("Done.")])
    reply = world.ask(request_text)
    assert reply.model.startswith("gemini") and reply.cost == 0 and world.claude.requests == []


def test_what_gemini_sees_holds_no_memory_and_no_personal_tool_names(tmp):
    from natro_agent.memory import Memory
    memory = Memory(tmp / "memory")
    memory.remember(SECRET, "told")
    world = World(tmp, gemini=[gemini_says("Hi!")])
    world.agent.memory = memory
    world.ask("hello")
    request = world.gemini.requests[0]
    seen = request["config"].system_instruction + "".join(
        part.text or "" for content in request["contents"] for part in content.parts)
    assert SECRET not in seen
    # The list of what she can do right now names only the free tools.
    offered = " ".join(line for line in seen.splitlines() if line.startswith("- Tools from"))
    listed = set(re.findall(r"[a-z_]+", offered)) & {tool.name for tool in world.tools.values()}
    assert listed and listed <= FREE_TIER_TOOLS, listed - FREE_TIER_TOOLS


def test_gemini_handing_over_moves_to_claude_for_good(tmp):
    world = World(tmp, claude=[message(text("Your sister is Nino.")), message(text("Sure."))],
                  gemini=[gemini_calls("hand_over", reason="family")])
    from natro_agent.memory import Memory
    memory = Memory(tmp / "memory")
    memory.remember(SECRET, "told")
    world.agent.memory = memory
    reply = world.ask("who's Nino to me")
    assert reply.model.startswith("claude") and SECRET in world.claude.requests[0]["system"][0]["text"]
    world.ask("thanks")
    assert len(world.gemini.requests) == 1


def test_a_failing_free_tier_falls_back_to_claude_not_to_nothing(tmp):
    world = World(tmp, claude=[message(text("Paris."))], gemini=[RuntimeError("429")])
    assert world.ask("capital of France?").text == "Paris."


def test_over_budget_the_free_tier_still_answers_and_claude_asks(tmp):
    world = World(tmp, claude=[], gemini=[gemini_says("Paris.")], budget=0.0)
    assert world.ask("capital of France?", answer=False).text == "Paris."
    assert world.owner.questions == []
    world.agent.session = None
    assert world.ask("remember I like jazz", answer=False).text == "OK, I won't use Claude for that."
    assert world.claude.requests == []


# --- 2. Risky actions always ask ----------------------------------------------------------------

@pytest.mark.parametrize("tool_name", sorted(ASK_FIRST_TOOLS))
def test_risky_tool_asks_and_no_stops_it(tmp, tool_name):
    world = World(tmp, claude=[message(text("I'll do that."), tool_use("t1", tool_name), stop="tool_use"),
                               message(text("OK, I left it."))])
    world.ask("please do the risky thing", answer=False)
    assert [question for question, _ in world.owner.questions] == ["I'll do that."]
    assert world.calls == []
    assert world.claude.requests[1]["messages"][-1]["content"][0]["content"] == \
        "Your owner said no, so this was not done."


@pytest.mark.parametrize("tool_name", sorted(ASK_FIRST_TOOLS))
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


def test_a_risky_tool_on_the_free_tier_is_impossible(tmp):
    # Even if Gemini invents a call to a risky tool, it isn't in its session, so nothing runs.
    world = World(tmp, gemini=[gemini_calls("pc_force_close_app", app="Spotify"), gemini_says("I can't.")])
    world.ask("close Spotify the hard way")
    assert world.calls == [] and world.owner.questions == []


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
             ("pc_open_app", {"app": "Chrome"}), ("phone_flashlight", {"on": True}), ("phone_screen", {})]
    for name, args in reads:
        world = World(tmp, claude=[message(tool_use("t1", name, **args), stop="tool_use"), message(text("Done."))])
        world.ask("go on")
        assert world.owner.questions == [] and world.calls == [(name, args)], name


# --- 3. Offline devices -------------------------------------------------------------------------

def test_a_device_that_is_offline_is_said_to_be_offline(tmp):
    world = World(tmp, claude=[message(text("Your PC is offline."))], devices=("phone",))
    world.ask("open Chrome", answer=True)
    request = world.claude.requests[0]
    assert "Your owner's PC is offline" in request["system"][0]["text"]
    assert "online: phone" in request["messages"][-1]["content"]
    assert not any(tool["name"].startswith("pc_") for tool in request["tools"])


def test_no_devices_means_no_device_tools_and_it_says_so(tmp):
    world = World(tmp, claude=[message(text("Both are offline."))], devices=())
    world.ask("turn the flashlight on")
    request = world.claude.requests[0]
    assert "online: no devices" in request["messages"][-1]["content"]
    assert not any(tool["name"].startswith(("pc_", "phone_")) for tool in request["tools"])
    assert "phone is offline" in request["system"][0]["text"]


def test_free_tier_is_not_offered_offline_devices_tools(tmp):
    world = World(tmp, gemini=[gemini_says("Your phone is offline.")], devices=("pc",))
    world.ask("turn the flashlight on")
    declared = {d.name for tool in world.gemini.requests[0]["config"].tools for d in tool.function_declarations}
    assert not any(name.startswith("phone_") for name in declared) and "pc_open_app" in declared


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

FLATS_JOB = ("start_job", {"task": "Find three flats in Tbilisi under 600 dollars a month and put them in a note."})

# (request, scripted Claude replies, the tool calls that must run, the model route)
JOURNEYS = [
    ("pause the music", [], [("spotify_control", {"action": "pause"})], "quick"),
    ("lock my PC", [], [("pc_run_command", {"command": "rundll32.exe user32.dll,LockWorkStation"})], "quick"),
    ("open Calculator", [("pc_open_app", {"app": "Calculator"})], [("pc_open_app", {"app": "Calculator"})], "claude"),
    ("set an alarm for 7:30", [("phone_set_alarm", {"time": "07:30"})], [("phone_set_alarm", {"time": "07:30"})],
     "claude"),
    ("what's on my calendar today", [("calendar_events", {})], [("calendar_events", {})], "claude"),
    ("find the newest PDF in Downloads", [("pc_search_files", {"query": "ext:pdf"})],
     [("pc_search_files", {"query": "ext:pdf"})], "claude"),
    ("what's on my PC's screen", [("pc_screen", {})], [("pc_screen", {})], "claude"),
    ("send my last photo to the PC", [("phone_search_files", {"kind": "image"}), SEND_PHOTO],
     [("phone_search_files", {"kind": "image"}), SEND_PHOTO], "claude"),
    ("remind me in 20 minutes to check the oven", [REMIND_OVEN], [REMIND_OVEN], "claude"),
    ("brief me", [("briefing", {})], [("briefing", {})], "claude"),
    ("in the background, find three flats under 600 dollars", [FLATS_JOB], [FLATS_JOB], "claude"),
    ("always tell me when Nino emails", [WATCH_NINO], [WATCH_NINO], "claude"),
    ("make my newest PDF a Word file", [NEWEST_PDF, PDF_TO_WORD], [NEWEST_PDF, PDF_TO_WORD], "claude"),
    ("what was that restaurant I asked about on Tuesday", [RESTAURANT], [RESTAURANT], "claude"),
    ("how long to the airport by bus", [AIRPORT_BY_BUS], [AIRPORT_BY_BUS], "claude"),
    ("what are my options to get to the airport with fewer changes", [AIRPORT_OPTIONS], [AIRPORT_OPTIONS], "claude"),
    ("plan my trip to the airport, first metro then bus", [METRO_THEN_BUS], [METRO_THEN_BUS], "claude"),
    ("how do I get to Vake Park from here", [HERE, VAKE_FROM_HERE], [HERE, VAKE_FROM_HERE], "claude"),
    ("navigate me there", [NAVIGATE], [NAVIGATE], "claude"),
]


@pytest.mark.parametrize("request_text, script, expected, route", JOURNEYS)
def test_journey(tmp, request_text, script, expected, route):
    replies = []
    for name, args in script:
        replies.append(message(tool_use("t1", name, **args), stop="tool_use"))
    if script:
        replies.append(message(text("Done.")))
    world = World(tmp, claude=replies)
    # The quick path needs Spotify's tool to have worked; the recorder says "Done (...)".
    reply = world.ask(request_text)
    assert world.calls == expected
    assert world.owner.questions == []
    assert (reply.model == "none (quick)") == (route == "quick")


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
