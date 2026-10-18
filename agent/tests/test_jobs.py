"""Background jobs: Natro starts them, they run beside his requests in the chat, and he's told when they're done."""
import asyncio

import pytest

from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent import chats
from natro_agent.agent import Agent
from natro_agent.gemini import Gemini
from natro_agent.jobs import MAX_STEPS, MOST_AT_ONCE, RUNNING, Jobs, lead
from natro_agent.server import Server
from natro_agent.tools import ToolError
from test_routing import FakeGeminiClient
from test_shared_chat import CLOSE, SCREENSHOT, devices, hello

TASK = "Find three flats in Tbilisi under 600 dollars a month and put them in a note called Flats."
FOUND = "I found three flats under 600 dollars and put them in your note Flats."


def job_hello(name, label, *tools):
    return {**hello(name, label, *tools), "jobs": True}


async def take(device, kind, answer_tools=None):
    """The first message of this kind not taken yet, whether it came already or comes next: with scripted models
    a job can finish before the request that started it has sent its reply."""
    taken = device.__dict__.setdefault("taken", set())
    for i, seen in enumerate(device.seen):
        if isinstance(seen, dict) and seen["type"] == kind and i not in taken:
            taken.add(i)
            return seen
    found = await device.next(kind, answer_tools)
    taken.add(len(device.seen) - 1)
    return found


class Handle:
    """Stands in for a job's asyncio task."""

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def make_jobs(asking=None):
    jobs = Jobs()
    jobs.started = []
    jobs.run = lambda job: jobs.started.append(job) or Handle()
    jobs.asking = lambda: asking
    return jobs


# --- The tools ----------------------------------------------------------------------------------

def test_start_says_so_and_runs_it():
    phone = Owner()
    phone.name = "phone"
    jobs = make_jobs(phone)
    said = run(jobs.start({"task": f"  {TASK} "}))
    assert said.splitlines()[0] == "OK, I'm on it. I'll tell you when it's done."
    job, = jobs.started
    assert job.task == TASK and job.asked_on == "phone" and job.state == "running" and job.id == "jb1"
    assert said.splitlines()[1] == f"Started job jb1: \"{TASK}\""


def test_what_start_refuses():
    jobs = make_jobs()
    with pytest.raises(ToolError, match="Say what the job is"):
        run(jobs.start({"task": " "}))
    with pytest.raises(ToolError, match="under 1000 characters"):
        run(jobs.start({"task": "x" * 1001}))
    for number in range(MOST_AT_ONCE):
        run(jobs.start({"task": f"Job {number}"}))
    with pytest.raises(ToolError, match="already working on 3 jobs"):
        run(jobs.start({"task": "One more"}))

    async def inside_a_job():
        RUNNING.set(jobs.items[0])
        await make_jobs().start({"task": "A job of its own"})
    with pytest.raises(ToolError, match="background job yourself"):
        run(inside_a_job())


def test_list_and_cancel():
    jobs = make_jobs()
    assert run(jobs.list({})) == "Nothing in the background right now."
    run(jobs.start({"task": TASK}))
    run(jobs.start({"task": "Compare flights to Berlin in December."}))
    flats, flights = jobs.items
    flats.step = "Searching flats Tbilisi"
    jobs.ended(flights, "done", "The cheapest is 240 dollars on the 12th.")
    listed = run(jobs.list({}))
    assert listed.startswith(f"Working on:\n- jb1 (started 0 min ago, now: Searching flats Tbilisi): \"{TASK}\"")
    assert ("Finished in the last day:\n- jb2, done 0 min ago, took 0 min: \"Compare flights to Berlin in "
            "December.\"\n  The cheapest is 240 dollars on the 12th.") in listed

    assert run(jobs.cancel({"id": "jb1"})) == f"Cancelled: \"{TASK}\""
    assert flats.task_handle.cancelled and flats.state == "cancelled"
    assert run(jobs.cancel({"id": "jb2"})) == "Job jb2 has already done."
    with pytest.raises(ToolError, match="no job 'jb9'"):
        run(jobs.cancel({"id": "jb9"}))


def test_only_the_first_paragraph_is_said():
    found = "Imereti first, then Kartli.\n\nImereti harvests late.\n\nKartli is an hour away."
    assert lead(found) == "Imereti first, then Kartli."
    assert lead("\n\nJust one.") == "Just one."
    assert len(lead("word " * 200)) == 400


def test_the_tools_are_personal_and_start_job_is_a_reply_by_itself():
    tools = {tool.name: tool for tool in Jobs().tools()}
    assert set(tools) == {"start_job", "jobs_list", "jobs_cancel"}
    assert all(tool.personal and not tool.confirm for tool in tools.values())
    assert tools["start_job"].final


# --- The agent ----------------------------------------------------------------------------------

def test_a_worker_has_a_conversation_of_its_own_on_claude():
    agent = Agent(client=FakeClient(), gemini=Gemini(client=FakeGeminiClient()))
    agent.notes.append("he sent a picture")
    worker = agent.worker(MAX_STEPS)
    assert worker.devices is agent.devices and worker.ledger is agent.ledger and worker.own_tools is agent.own_tools
    assert worker.gemini is None and worker.session is None and worker.notes == [] and worker.max_steps == 30
    assert worker._busy is not agent._busy and worker._asking is agent._asking
    assert agent.notes == ["he sent a picture"] and agent.max_steps == 20


def test_after_a_job_the_next_request_goes_to_claude():
    gemini = FakeGeminiClient()  # nothing scripted: a call would fail the test
    agent = Agent(client=FakeClient(message(text("Three flats, in your note."))), gemini=Gemini(client=gemini))
    agent.claude_next = True
    reply = run(agent.respond("what did you find", Owner()))
    assert reply.text == "Three flats, in your note." and gemini.requests == [] and not agent.claude_next


# --- Through the server -------------------------------------------------------------------------

def server_with_jobs(*replies):
    agent = Agent(client=FakeClient(*replies))
    return Server(agent, jobs=Jobs())


def test_a_job_runs_then_is_shown_everywhere_and_spoken_where_he_is(monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server = server_with_jobs(message(tool_use("t1", "start_job", task=TASK), stop="tool_use"),
                              message(text(FOUND)))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="flats in the background")
        reply = await take(pc, "reply")
        return reply, await take(pc, "job_done"), await take(phone, "job_done")

    reply, on_pc, on_phone = run(devices(server, job_hello("pc", "PC"), job_hello("phone", "phone"), play=play))
    assert reply["text"] == "OK, I'm on it. I'll tell you when it's done."  # start_job's own words: one call
    assert on_pc["text"] == on_phone["text"] == on_pc["say"] == FOUND and on_pc["task"] == TASK and on_pc["ok"]
    assert (on_pc["speak"], on_phone["speak"]) == (True, False)  # he used the PC last
    entry = server.chat.entries[f"natro:{on_pc['id']}"]
    assert entry["job"] == TASK and entry["reply"] == FOUND and entry["done"] and entry["asked"] == ""
    job, = server.jobs.items
    assert job.state == "done" and job.result == FOUND
    # The job's own conversation: told it's a job, with the task in full.
    request = server.agent.client.requests[1]["messages"][0]["content"]
    assert "[A background job: he asked for this on his PC at " in request and request.endswith(TASK)
    # Claude hears what it found with the next request.
    assert server.agent.claude_next and FOUND in server.agent.private_notes[0]


def test_his_requests_go_on_while_a_job_works(monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server = server_with_jobs(
        message(tool_use("t1", "start_job", task=TASK), stop="tool_use"),
        message(tool_use("j1", "pc_screenshot"), stop="tool_use"),  # the job: waits on the PC
        message(text("It's half past three.")),  # his next request, meanwhile
        message(text(FOUND)))

    async def play(pc):
        await pc.send(type="text", id="r1", text="flats in the background")
        await take(pc, "reply")
        call = await take(pc, "tool_call")
        assert call["request"].startswith("natro:jb")  # the job's chat entry, not his request's
        await pc.send(type="text", id="r2", text="what time is it")
        second = await take(pc, "reply")
        await pc.send(type="tool_result", id=call["id"], ok=True, result="The screenshot is in the chat.")
        return second, await take(pc, "job_done")

    second, done = run(devices(server, job_hello("pc", "PC", SCREENSHOT), play=play))
    assert second["text"] == "It's half past three." and done["text"] == FOUND


def test_a_jobs_question_asks_where_he_is_and_can_be_answered_in_the_chat(monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server = server_with_jobs(
        message(tool_use("t1", "start_job", task="Close Spotify when you're done looking."), stop="tool_use"),
        message(text("I'll force close Spotify."), tool_use("j1", "pc_force_close_app", app="Spotify"),
                stop="tool_use"),
        message(text("Closed Spotify.")))
    closed = []

    async def play_on_pc(pc, phone):
        await pc.send(type="text", id="r1", text="do that in the background")
        await take(pc, "reply")
        asked = await take(pc, "confirm")
        assert asked["request"].startswith("natro:jb")  # the PC shows it with the job, not with its own request
        entry = (await take(phone, "chat_entry"))["entry"]
        while not (entry["job"] and entry["question"]):
            entry = (await take(phone, "chat_entry"))["entry"]
        assert entry["question"] == {"id": asked["id"], "text": "I'll force close Spotify.", "answer": "",
                                     "device": "pc"}
        # The phone shows the job's question as the device "natro"'s: the server finds where it was asked.
        await phone.send(type="answer", id=asked["id"], yes=True, device="natro")
        answered = await take(pc, "answered", answer_tools=lambda call: closed.append(call) or {"ok": True})
        done = await take(pc, "job_done", answer_tools=lambda call: closed.append(call) or {"ok": True})
        return answered, done

    answered, done = run(devices(server, job_hello("pc", "PC", CLOSE), job_hello("phone", "phone"),
                                 play=play_on_pc))
    assert answered["yes"] is True and [call["name"] for call in closed] == ["force_close_app"]
    assert done["text"] == "Closed Spotify."
    job_entry = next(entry for entry in server.chat.entries.values() if entry["job"])
    assert job_entry["question"]["answer"] == "Yes"


def test_cancel_that(monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server = server_with_jobs(
        message(tool_use("t1", "start_job", task=TASK), stop="tool_use"),
        message(tool_use("j1", "pc_screenshot"), stop="tool_use"),  # the job waits on the PC, which never answers
        message({"type": "tool_use", "id": "t2", "name": "jobs_cancel", "input": {"id": "jb1"}}, stop="tool_use"),
        message(text("OK, I stopped looking.")))

    async def play(pc):
        await pc.send(type="text", id="r1", text="flats in the background")
        await take(pc, "reply")
        await take(pc, "tool_call")
        await pc.send(type="text", id="r2", text="cancel that")
        reply = await take(pc, "reply")
        await asyncio.sleep(0.05)
        return reply

    reply = run(devices(server, job_hello("pc", "PC", SCREENSHOT), play=play))
    assert reply["text"] == "OK, I stopped looking."
    job, = server.jobs.items
    assert job.state == "cancelled"
    entry = server.chat.entries["natro:jb1"]
    assert entry["problem"] == "Cancelled" and entry["done"]
    assert not server.agent.claude_next  # nothing to tell


def test_apps_that_dont_show_jobs_only_see_it_in_the_chat(monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server = server_with_jobs(message(tool_use("t1", "start_job", task=TASK), stop="tool_use"),
                              message(text(FOUND)))

    async def play(old_pc):
        await old_pc.send(type="text", id="r1", text="flats in the background")
        await take(old_pc, "reply")
        while True:
            entry = (await take(old_pc, "chat_entry"))["entry"]
            if entry["job"] and entry["done"]:
                return entry

    entry = run(devices(server, hello("pc", "PC"), play=play))
    assert entry["reply"] == FOUND
    assert server.jobs.items[0].state == "done"
