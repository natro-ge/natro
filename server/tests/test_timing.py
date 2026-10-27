"""How long each part of a request takes (timing.py), how it's logged, and the dashboard's breakdown of it."""
import asyncio
import json
import threading
from datetime import datetime, timedelta

from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent.agent import Agent
from natro_agent.config import timezone
from natro_agent.dashboard import timings
from natro_agent.server import Server, Voice
from natro_agent.timing import Timing
from natro_agent.tools import Tool
from natro_voice.speech import Recognizer
from test_server import HELLO, FakeVoice, session, until_reply

SCHEMA = {"type": "object", "properties": {}}


def logged(folder, kind):
    return [entry for path in sorted(folder.glob("agent-*.jsonl"))
            for entry in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if entry["kind"] == kind]


def test_a_request_times_its_model_calls_its_tools_and_his_yes():
    async def delete(args):
        await asyncio.sleep(0.1)
        return "Deleted."

    class SlowToAnswer(Owner):
        async def confirm(self, question, details):
            await asyncio.sleep(0.3)
            return True

    client = FakeClient(
        message(text("I'll delete it."), tool_use("t1", "notes_delete"), stop="tool_use", cache_read=900),
        message(text("Deleted it."), cache_read=1000))
    agent = Agent(tools=[Tool(name="notes_delete", description="Delete a note.", input_schema=SCHEMA, run=delete,
                              source="natro", confirm=True)], client=client)
    timing = Timing()
    reply = run(agent.respond("delete my note", SlowToAnswer(), timing=timing))
    record = timing.record()

    assert reply.text == "Deleted it."
    assert [call["model"] for call in record["calls"]] == [agent.model] * 2
    assert all(0 <= call["first_token"] <= call["seconds"] for call in record["calls"])
    assert record["calls"][0] | {"first_token": 0, "seconds": 0} == {
        "model": agent.model, "first_token": 0, "seconds": 0, "input": 1000, "cache_read": 900, "cache_write": 0,
        "output": 50}
    assert [(tool["tool"], tool["source"], tool["ok"]) for tool in record["tools"]] == [("notes_delete", "natro", True)]
    assert record["tools"][0]["seconds"] >= 0.08 and record["asking"] >= 0.25
    # The step is the tool's own time: waiting for his yes is left out.
    assert len(record["steps"]) == 1 and 0.08 <= record["steps"][0] < 0.25
    assert record["total"] >= 0.35 and record["wait"] < 0.05 and record["voice"] == {}


def test_a_request_waits_for_the_one_before():
    async def open_app(args):
        await asyncio.sleep(0.2)
        return "Opened Calculator."

    client = FakeClient(message(tool_use("t1", "open_app"), stop="tool_use"), message(text("Hi!")))
    agent = Agent(tools=[Tool(name="open_app", description="Open an app.", input_schema=SCHEMA, run=open_app,
                              source="natro", final=True)], client=client)

    async def both():
        first, second = Timing(), Timing()
        replies = await asyncio.gather(agent.respond("open calculator", Owner(), timing=first),
                                       agent.respond("hello", Owner(), timing=second))
        return replies, first, second

    replies, first, second = run(both())
    assert [reply.text for reply in replies] == ["Opened Calculator.", "Hi!"]
    assert first.wait < 0.05 and second.wait >= 0.15
    assert len(first.calls) == 1 and len(first.steps) == 1  # the tool's result was the reply: no second call


def test_the_voice_pipeline_times_its_parts():
    recognizer = Recognizer.__new__(Recognizer)
    recognizer.audio_seconds = 0.0
    # Google took it for English, so it's recognized again as Georgian only.
    heard = iter([("Okay Natro, padeli chanishne", {"en-us"}), ("ოკ ნატრო, ფადელი ჩანიშნე", {"ka-ge"})])
    recognizer.recognize = lambda pcm, languages=None: next(heard)

    class Translator:
        cost = 0.0

        def translate(self, georgian, as_georgian=None, on_text=None):
            on_text("Schedule ")
            on_text("padel.")
            return "Schedule padel."

    voice = Voice.__new__(Voice)
    voice.recognizer, voice.translator, voice.has_speech, voice._lock = recognizer, Translator(), bool, threading.Lock()
    pieces, timing = [], {}
    georgian, english, _ = voice.process(b"pcm", pieces.append, timing)

    assert (georgian, english, pieces) == ("ოკ ნატრო, ფადელი ჩანიშნე", "Schedule padel.", ["Schedule ", "padel."])
    assert set(timing) == {"recognize", "georgian", "translate", "translate_first"}
    assert 0 <= timing["translate_first"] <= timing["translate"]


def test_requests_log_their_timing_and_the_device_says_when_it_started_speaking(log_dir):
    voice = FakeVoice(("ქრომი გახსენი", "Open Chrome.", 0.002))
    server = Server(Agent(client=FakeClient(message(text("I can't yet.")), message(text("You're welcome!")))),
                    voice=voice, token="secret")

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await ws.send(json.dumps({"type": "audio", "id": "r1"}))
        await ws.send(b"\x01\x00" * 1600)
        await until_reply(ws)
        await ws.send(json.dumps({"type": "spoken", "id": "r1", "seconds": 1.25}))
        await ws.send(json.dumps({"type": "spoken", "id": "r1", "seconds": "soon"}))  # not a time: not logged
        await ws.send(json.dumps({"type": "text", "id": "r2", "text": "thanks"}))
        await until_reply(ws)

    run(session(server, device))
    timing = logged(log_dir, "timing")
    assert [(entry["device"], entry["id"], entry["spoken"], entry["text"]) for entry in timing] == [
        ("pc", "r1", True, "Open Chrome."), ("pc", "r2", False, "thanks")]
    assert timing[0]["voice"] == {"recognize": 0.5, "translate": 0.25, "translate_first": 0.125}
    assert timing[1]["voice"] == {} and all(len(entry["calls"]) == 1 for entry in timing)
    assert [(entry["device"], entry["id"], entry["seconds"]) for entry in logged(log_dir, "spoken")] == [("pc", "r1", 1.25)]


NOW = datetime(2026, 10, 19, 15, 30, tzinfo=timezone())


def test_the_dashboard_breaks_the_time_down():
    entries = [
        {"kind": "timing", "device": "pc", "id": "r1", "text": "What's on tomorrow?", "spoken": True,
         "model": "claude-sonnet-5-5", "total": 6.0, "voice": {"recognize": 1.0, "georgian": 0.5, "translate": 1.0,
                                                               "translate_first": 0.5},
         "wait": 0.25, "asking": 0.0, "steps": [1.0], "tools": [],
         "calls": [{"first_token": 0.75, "seconds": 1.0, "cache_write": 24000},
                   {"first_token": 0.5, "seconds": 1.0, "cache_write": 100}]},
        {"kind": "spoken", "device": "pc", "id": "r1", "seconds": 1.5},
        {"kind": "timing", "device": "phone", "id": "r1", "text": "Pause", "spoken": False, "model": "none (quick)",
         "total": 0.5, "voice": {}, "wait": 0.0, "asking": 0.0, "calls": [], "steps": [0.25], "tools": []},
        # Nine seconds of it were his, deciding: left out. Its one call wrote nothing, so all of it was waiting.
        {"kind": "timing", "device": "phone", "id": "r2", "text": "Delete the note", "spoken": False,
         "model": "claude-sonnet-5-5", "total": 12.0, "voice": {}, "wait": 0.0, "asking": 9.0, "steps": [0.5],
         "tools": [], "calls": [{"first_token": None, "seconds": 2.0, "cache_write": 0}]},
        {"kind": "spoken", "device": "phone", "id": "r9", "seconds": 0.3},  # no such request
    ]
    found = timings([(None, n, NOW + timedelta(seconds=n), entry) for n, entry in enumerate(entries)])

    groups = {group["key"]: group for group in found["groups"]}
    assert list(groups) == ["spoken", "typed", "quick"] and found["count"] == 3
    assert groups["spoken"]["stages"] == {"speech": 1.5, "translate": 1.0, "first_token": 1.25, "writing": 0.75,
                                          "tools": 1.0, "speaking": 1.5, "other": 0.5}
    assert groups["typed"]["stages"] == {"speech": 0.0, "translate": 0.0, "first_token": 2.0, "writing": 0.0,
                                         "tools": 0.5, "speaking": 0.0, "other": 0.5}
    assert groups["quick"]["median"] == 0.5 and groups["spoken"]["median"] == 7.5
    assert found["first_token"] == {"warm": 0.5, "warm_count": 1, "cold": 0.75, "cold_count": 1}
    assert found["georgian_share"] == 1.0 and found["speaking"] == {"pc": 1.5, "phone": None}
    assert [(request["text"], request["waited"]) for request in found["slowest"]] == [
        ("What's on tomorrow?", 7.5), ("Delete the note", 3.0), ("Pause", 0.5)]
    parts = {part["key"]: part for part in found["parts"]}
    assert {key: parts[key]["count"] for key in parts} == {
        "recognize": 1, "georgian": 1, "translate_first": 1, "translate": 1, "wait": 3, "first_token": 2,
        "writing": 2, "tools": 3, "speaking": 1, "other": 3, "waited": 3}
    assert parts["wait"]["p95"] == 0.25 and parts["waited"]["median"] == 3.0
