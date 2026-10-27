import re
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import httpx2
import pytest

from conftest import FakeClient, Owner, fallback, message, run, text, thinking, tool_use
from natro_agent import agent as agent_module
from natro_agent.agent import Agent, echo_content, noise, plain_question, utc_offset
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.tools import ASKS_FIRST, Tool


def make_agent(client, budget=15.0):
    agent = Agent(client=client, budget=budget)
    pc = DryRunDevice("pc", "PC", PC_TOOLS)
    agent.connect(pc)
    return agent, pc


def test_reply_and_cost():
    agent, _ = make_agent(FakeClient(message(thinking(), text("Hi, I'm Natro."))))
    reply = run(agent.respond("what do you know about me?", Owner()))
    assert reply.text == "Hi, I'm Natro."
    assert reply.model == "claude-sonnet-5-5"
    assert abs(reply.cost - (1000 * 2 + 50 * 10) / 1e6) < 1e-12


def test_tool_loop_only_appends_to_the_conversation():
    client = FakeClient(
        message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
        message(text("Chrome is open.")),
        message(text("You asked me to open Chrome.")),
    )
    agent, pc = make_agent(client)
    owner = Owner()
    run(agent.respond("open Chrome and tell me when it's open", owner))
    run(agent.respond("what did I ask?", owner))

    assert pc.calls == [("open_app", {"app": "Chrome"})]
    first, second, third = client.requests
    # Same instructions and tools for the whole conversation...
    assert first["system"] == second["system"] == third["system"]
    assert first["tools"] == second["tools"] == third["tools"]
    # ...and each request repeats the previous one's messages exactly, then adds to them.
    assert second["messages"][:len(first["messages"])] == first["messages"]
    assert third["messages"][:len(second["messages"])] == second["messages"]
    results = second["messages"][-1]["content"]
    assert results == [{"type": "tool_result", "tool_use_id": "t1", "content": "Done (dry run: open_app only pretended to run).",
                        "is_error": False}]


def test_risky_tool_asks_first_with_natros_sentence():
    client = FakeClient(
        message(text("I'll force close Spotify."), tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("OK, I left it.")),
    )
    agent, pc = make_agent(client)
    owner = Owner(answer=False)
    run(agent.respond("force close Spotify", owner))
    assert owner.questions == [("I'll force close Spotify.", 'pc_force_close_app {"app": "Spotify"}')]
    assert pc.calls == []
    assert client.requests[1]["messages"][-1]["content"][0]["content"] == "Your owner said no, so this was not done."


def test_risky_tool_runs_after_yes():
    client = FakeClient(
        message(tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("Done.")),
    )
    agent, pc = make_agent(client)
    run(agent.respond("force close Spotify", Owner(answer=True)))
    assert pc.calls == [("force_close_app", {"app": "Spotify"})]


def test_each_tool_that_runs_is_shown_as_a_step():
    client = FakeClient(
        message(tool_use("t1", "pc_open_app", app="Spotify"), tool_use("t2", "pc_force_close_app", app="Chrome"),
                stop="tool_use"),
        message(text("Spotify is open; I left Chrome.")),
    )
    agent, _ = make_agent(client)
    shown = []
    run(agent.respond("open Spotify and force close Chrome", Owner(answer=False), on_step=shown.append))
    # Chrome's force-close was declined, so it never ran and isn't shown.
    assert shown == [{"text": "Opening Spotify", "icon": "app", "app": "Spotify"}]


def task_tool(added):
    async def add(args):
        added.append(args["title"])
        return "Added."
    return Tool(name="tasks_add", description="Add a task.", run=add, source="google", personal=True, changes=True,
                input_schema={"type": "object", "properties": {"title": {"type": "string"}}})


def test_many_changes_ask_once_with_natros_sentence():
    calls = [tool_use(f"t{i}", "tasks_add", title=f"Task {i}") for i in range(6)]
    client = FakeClient(message(text("I'll add 6 tasks."), *calls, stop="tool_use"), message(text("Added them.")))
    added = []
    owner = Owner(answer=False)
    run(Agent(tools=[task_tool(added)], client=client).respond("add these six tasks", owner))
    assert owner.questions == [("I'll add 6 tasks.", "6 changes: tasks_add x6")]
    assert added == []
    results = client.requests[1]["messages"][-1]["content"]
    assert {result["content"] for result in results} == {"Your owner said no, so this was not done."}


def test_many_changes_counted_over_the_whole_request():
    client = FakeClient(
        message(*(tool_use(f"a{i}", "tasks_add", title=f"A{i}") for i in range(5)), stop="tool_use"),
        message(text("Two more."), *(tool_use(f"b{i}", "tasks_add", title=f"B{i}") for i in range(2)), stop="tool_use"),
        message(tool_use("c0", "tasks_add", title="C0"), stop="tool_use"),
        message(text("Done.")),
    )
    added = []
    owner = Owner(answer=True)
    run(Agent(tools=[task_tool(added)], client=client).respond("add these tasks", owner))
    # Five run without asking; the sixth asks once, and after a yes the rest of the request doesn't ask again.
    assert owner.questions == [("Two more.", "2 changes: tasks_add x2")]
    assert len(added) == 8


def test_device_that_went_offline_mid_conversation():
    client = FakeClient(
        message(text("Hello.")),
        message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
        message(text("Your PC is offline.")),
    )
    agent, pc = make_agent(client)
    run(agent.respond("hi", Owner()))
    agent.disconnect(pc)
    run(agent.respond("open Chrome", Owner()))
    result = client.requests[2]["messages"][-1]["content"][0]
    assert result["is_error"] and "offline" in result["content"]
    assert pc.calls == []
    assert "online: no devices" in client.requests[1]["messages"][-1]["content"]


def test_device_that_reconnected_mid_conversation_is_used():
    client = FakeClient(
        message(text("Hello.")),
        message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
        message(text("Done.")),
    )
    agent, old_pc = make_agent(client)
    run(agent.respond("hi", Owner()))
    agent.disconnect(old_pc)  # the PC app restarted
    new_pc = DryRunDevice("pc", "PC", PC_TOOLS)
    agent.connect(new_pc)
    run(agent.respond("open Chrome and tell me when it's open", Owner()))
    assert new_pc.calls == [("open_app", {"app": "Chrome"})] and old_pc.calls == []
    assert not client.requests[2]["messages"][-1]["content"][0]["is_error"]


def test_follow_up_continues_then_conversation_expires():
    client = FakeClient(message(text("One.")), message(text("Two.")), message(text("Three.")))
    agent, _ = make_agent(client)
    run(agent.respond("first", Owner()))
    run(agent.respond("second", Owner()))
    assert len(client.requests[1]["messages"]) == 3
    agent.session.last_used -= agent_module.SESSION_MINUTES * 60 + 1
    run(agent.respond("third", Owner()))
    assert len(client.requests[2]["messages"]) == 1


def test_echo_after_a_fallback_drops_the_declined_models_internals():
    reply = message(thinking(), text("Partial "), tool_use("t0", "pc_open_app", app="x"), fallback(),
                    thinking(), text("answer."))
    kept = echo_content(reply.content)
    assert [block.type for block in kept] == ["text", "thinking", "text"]
    assert echo_content(message(thinking(), text("Hi")).content) == list(message(thinking(), text("Hi")).content)


def test_api_failure_moves_the_conversation_to_haiku():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    client = FakeClient(error, message(text("Hi.")))
    agent, _ = make_agent(client)
    reply = run(agent.respond("anything in my calendar today?", Owner()))
    assert reply.text == "Hi." and reply.model == "claude-haiku-5-5"
    assert client.requests[1]["model"] == "claude-haiku-5-5"
    assert "betas" not in client.requests[1] and "fallbacks" not in client.requests[1]


def test_refusal_starts_a_fresh_conversation():
    client = FakeClient(message(stop="refusal"), message(text("Hi.")))
    agent, _ = make_agent(client)
    reply = run(agent.respond("read my latest email", Owner()))
    assert reply.text == "Sorry, I can't help with that one."
    run(agent.respond("hi", Owner()))
    assert len(client.requests[1]["messages"]) == 1


def test_too_many_steps_stops_the_loop():
    steps = [message(tool_use(f"t{i}", "pc_open_app", app="Chrome"), stop="tool_use")
             for i in range(agent_module.MAX_STEPS)]
    agent, pc = make_agent(FakeClient(*steps))
    reply = run(agent.respond("open Chrome, then open it again forever", Owner()))
    assert len(pc.calls) == agent_module.MAX_STEPS
    assert "too many steps" in reply.text


def test_budget_warning_then_cap():
    # Each reply costs $0.000125 (Haiku: 1,000 tokens in, 50 out); the budget is $0.0002.
    client = FakeClient(message(text("One.")), message(text("Two.")))
    agent, _ = make_agent(client, budget=0.0002)
    assert "One." == run(agent.respond("first", Owner())).text
    assert "passed 80%" in run(agent.respond("second", Owner())).text
    owner = Owner(answer=False)
    reply = run(agent.respond("third", owner))
    assert reply.text == "OK, I won't use Claude for that." and len(client.requests) == 2
    assert "budget is used up" in owner.questions[0][0]


def test_spending_is_summed_from_this_months_log():
    run(make_agent(FakeClient(message(text("One."))))[0].respond("first", Owner()))
    assert abs(Agent(client=FakeClient()).ledger.spent - 0.000125) < 1e-9  # Haiku's


def test_tools_that_ask_first_say_so_to_claude():
    tools = {tool.name: tool for tool in make_agent(FakeClient())[0].offered_tools()}
    assert tools["pc_force_close_app"].definition()["description"].endswith(ASKS_FIRST)
    assert ASKS_FIRST not in tools["pc_open_app"].definition()["description"]


def test_reply_is_her_last_words_not_the_question_or_her_steps():
    client = FakeClient(
        message(text("Live version matched; use the studio one."), tool_use("t1", "pc_open_app", app="Spotify"),
                stop="tool_use"),
        message(text("I'll force close Chrome."), tool_use("t2", "pc_force_close_app", app="Chrome"), stop="tool_use"),
        message(text("Done, Chrome is closed.")),
    )
    agent, _ = make_agent(client)
    shown = []
    reply = run(agent.respond("open Spotify and force close Chrome", Owner(), on_text=shown.append))
    assert reply.text == "Done, Chrome is closed."
    # Everything showed as she wrote it; devices show the reply in its place.
    assert "".join(shown) == "Live version matched; use the studio one. I'll force close Chrome. Done, Chrome is closed."


def test_with_nothing_after_her_last_tool_the_reply_is_not_the_question():
    client = FakeClient(
        message(text("Spotify is open."), tool_use("t1", "pc_open_app", app="Spotify"), stop="tool_use"),
        message(text("I'll force close Chrome."), tool_use("t2", "pc_force_close_app", app="Chrome"), stop="tool_use"),
        message(),
    )
    reply = run(make_agent(client)[0].respond("open Spotify and force close Chrome", Owner()))
    assert reply.text == "Spotify is open."


def test_too_many_steps_leaves_out_her_planning():
    steps = [message(text("Now add it."), tool_use(f"t{i}", "pc_open_app", app="Chrome"), stop="tool_use")
             for i in range(agent_module.MAX_STEPS)]
    reply = run(make_agent(FakeClient(*steps))[0].respond("add them all", Owner()))
    assert reply.text == "I stopped there: that was taking too many steps."


def test_risky_tool_without_a_sentence_asks_in_plain_words():
    client = FakeClient(message(tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
                        message(text("OK, I left it.")))
    owner = Owner(answer=False)
    run(make_agent(client)[0].respond("force close Spotify", owner))
    assert owner.questions[0][0] == "Force-closing Spotify on the PC. Should I go ahead?"
    assert plain_question("phone_call", {"number": "599184413"}) == "Calling on the phone: 599184413. Should I go ahead?"


def test_header_has_the_utc_offset(monkeypatch):
    monkeypatch.setenv("NATRO_TIMEZONE", "Asia/Kolkata")
    client = FakeClient(message(text("Hi.")))
    run(make_agent(client)[0].respond("hi", Owner()))
    assert re.match(r"\[\w+ \d\d \w+ \d{4}, \d\d:\d\d UTC\+5:30 · ", client.requests[0]["messages"][-1]["content"])

    def at(zone):
        return utc_offset(datetime(2026, 10, 17, 14, 43, tzinfo=ZoneInfo(zone)))
    assert [at(zone) for zone in ("Asia/Tbilisi", "Asia/Tokyo", "America/Sao_Paulo", "UTC")] == \
        ["UTC+4", "UTC+9", "UTC-3", "UTC"]


@pytest.mark.parametrize("said, in_conversation, dropped", [
    ("1 1 1 1 1", False, True), ("1 1 1 1 1", True, True), ("...", True, True), ("", False, True),
    ("a", True, True), ("2", False, True), ("2", True, False), ("07:30", True, False), ("ok", True, False),
    ("open Chrome", False, False), ("გამარჯობა", False, False),
])
def test_noise(said, in_conversation, dropped):
    assert noise(said, in_conversation) is dropped


def test_noise_never_reaches_a_model():
    client = FakeClient(message(text("Hi.")), message(text("Calling the second one.")))
    agent, _ = make_agent(client)
    run(agent.respond("hi", Owner()))
    reply = run(agent.respond("1 1 1 1 1", Owner()))
    assert reply.text == "I didn't catch that." and reply.cost == 0 and len(client.requests) == 1
    run(agent.respond("2", Owner()))  # a number alone in a conversation may answer her question
    assert len(client.requests) == 2 and len(client.requests[1]["messages"]) == 3


def test_the_same_call_twice_in_one_step_runs_once():
    added = []
    claude = FakeClient(message(tool_use("t1", "tasks_add", title="Milk"), tool_use("t2", "tasks_add", title="Milk"),
                                tool_use("t3", "tasks_add", title="Eggs"), stop="tool_use"),
                        message(text("Added milk and eggs.")))
    agent = Agent([task_tool(added)], client=claude)
    run(agent.respond("add milk and eggs to my tasks", Owner()))
    assert added == ["Milk", "Eggs"]
    results = {r["tool_use_id"]: r for r in claude.requests[1]["messages"][-1]["content"]}
    assert results["t1"]["content"] == "Added." and "Not done again" in results["t2"]["content"]
    assert not results["t2"]["is_error"]


def test_what_a_cancelled_request_did_is_told_to_the_next():
    import asyncio

    added = []
    started = asyncio.Event()

    async def wait(args):
        started.set()
        await asyncio.sleep(3600)

    slow = Tool(name="pc_screen", description="Read the screen.", run=wait, source="pc", personal=True,
                input_schema={"type": "object", "properties": {}})
    claude = FakeClient(message(tool_use("t1", "tasks_add", title="Milk"), stop="tool_use"),
                        message(tool_use("t2", "pc_screen"), stop="tool_use"),
                        message(text("Yes, milk is on it.")))
    agent = Agent([task_tool(added), slow], client=claude)

    async def play():
        request = asyncio.create_task(agent.respond("add milk to my tasks, then look at my screen", Owner()))
        await started.wait()
        request.cancel()
        try:
            await request
        except asyncio.CancelledError:
            pass
        return await agent.respond("did you add the milk?", Owner())

    run(play())
    header = claude.requests[2]["messages"][-1]["content"].splitlines()[0]
    assert "he stopped his request \"add milk to my tasks, then look at my screen\" partway" in header
    assert 'tasks_add {"title": "Milk"}: Added.' in header and "pc_screen" not in header
    assert added == ["Milk"]  # nothing is done again
