"""Which Claude model answers: Haiku for plain requests, the PC and screens, Sonnet for personal or risky ones and
whenever Haiku is stuck (test_routing.py has the plain and personal split)."""
import anthropic
import httpx2
import pytest

from conftest import FakeClient, Owner, message, run, text, thinking, tool_use
from natro_agent.agent import STUCK_STEPS, Agent, pc_task, personal_or_risky
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.tools import Tool, ToolError

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"


def failing_tool():
    async def run_it(args):
        raise ToolError("That didn't work.")
    return Tool("flaky", "A tool that always fails.", {"type": "object", "properties": {}}, run_it, "natro")


def make_agent(client, tools=()):
    agent = Agent(list(tools), client=client, budget=15.0)
    agent.connect(DryRunDevice("pc", "PC", PC_TOOLS))
    return agent


def models(client):
    return [request["model"] for request in client.requests]


@pytest.mark.parametrize("request_text", [
    "turn on dark mode", "what's my IP address", "how much disk space is left", "open Bluetooth settings",
    "close the Chrome tab with YouTube", "what's on my screen", "click the blue button", "run ipconfig",
    "which apps are open on my PC", "find the invoice on my computer",
])
def test_pc_and_screen_requests_start_on_haiku(request_text):
    assert pc_task(request_text)


@pytest.mark.parametrize("request_text", [
    "what's on my calendar today", "read my latest email", "remember that I like jazz", "who is my sister",
    "play Yellow", "set a timer for ten minutes", "weather in Tbilisi tomorrow",
])
def test_the_rest_are_not_pc_tasks(request_text):
    assert not pc_task(request_text)


def test_screens_and_commands_are_personal_but_start_on_haiku():
    client = FakeClient(*(message(text("Done.")) for _ in range(4)))
    for request_text in ["what's on my screen", "turn on dark mode", "run a command", "what's my IP address"]:
        assert personal_or_risky(request_text) and pc_task(request_text), request_text
        agent = make_agent(client)
        run(agent.respond(request_text, Owner()))
    assert models(client) == [HAIKU] * 4


def test_one_conversation_mixes_models_but_only_ever_grows():
    client = FakeClient(message(thinking(), text("Dark mode is on.")), message(text("Nothing today.")),
                        message(text("It's 192.168.1.5.")))
    agent = make_agent(client)
    owner = Owner()
    run(agent.respond("turn on dark mode", owner))
    reply = run(agent.respond("anything on my calendar today?", owner))
    assert reply.model == SONNET
    reply = run(agent.respond("and what's my IP address?", owner))
    assert reply.model == HAIKU
    assert models(client) == [HAIKU, SONNET, HAIKU]
    first, second, third = client.requests
    assert first["system"] == second["system"] == third["system"] and first["tools"] == third["tools"]
    assert third["messages"][:len(second["messages"])] == second["messages"]
    assert "fallbacks" not in first and "fallbacks" in second  # Haiku has no server-side fallback


def test_haiku_costs_its_own_prices():
    agent = make_agent(FakeClient(message(text("Done."))))
    reply = run(agent.respond("how much disk space is left on my computer?", Owner()))
    assert reply.model == HAIKU and abs(reply.cost - (1000 * 0.10 + 50 * 0.50) / 1e6) < 1e-12


def test_when_haiku_declines_sonnet_answers_the_same_request():
    client = FakeClient(message(stop="refusal"), message(text("Here's what's on your screen.")))
    agent = make_agent(client)
    reply = run(agent.respond("what's on my screen?", Owner()))
    assert reply.text == "Here's what's on your screen." and reply.model == SONNET
    assert models(client) == [HAIKU, SONNET]
    assert client.requests[0]["messages"] == client.requests[1]["messages"]
    assert agent.session is not None  # the conversation goes on


def test_when_haikus_tools_keep_failing_sonnet_takes_over():
    client = FakeClient(message(tool_use("t1", "flaky"), stop="tool_use"),
                        message(tool_use("t2", "flaky"), stop="tool_use"),
                        message(text("I found another way.")))
    agent = make_agent(client, [failing_tool()])
    reply = run(agent.respond("change the screen brightness", Owner()))
    assert models(client) == [HAIKU, HAIKU, SONNET]
    assert reply.model == SONNET and reply.text == "I found another way."


def test_one_failure_is_not_stuck():
    client = FakeClient(message(tool_use("t1", "flaky"), stop="tool_use"),
                        message(tool_use("t2", "pc_open_app", app="Chrome"), stop="tool_use"),
                        message(tool_use("t3", "flaky"), stop="tool_use"), message(text("Done.")))
    agent = make_agent(client, [failing_tool()])
    run(agent.respond("open the browser on my PC", Owner()))
    assert models(client) == [HAIKU] * 4


def test_a_long_task_moves_to_sonnet():
    steps = [message(tool_use(f"t{i}", "pc_list_open_apps"), stop="tool_use") for i in range(STUCK_STEPS + 1)]
    client = FakeClient(*steps, message(text("Done.")))
    agent = make_agent(client)
    run(agent.respond("tidy up the windows on my PC", Owner()))
    assert models(client) == [HAIKU] * STUCK_STEPS + [SONNET] * 2


def test_when_haikus_api_fails_sonnet_answers_for_the_rest_of_the_conversation():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    client = FakeClient(error, message(text("Dark mode is on.")), message(text("Light mode is on.")))
    agent = make_agent(client)
    run(agent.respond("turn on dark mode", Owner()))
    run(agent.respond("now light mode on my PC", Owner()))
    assert models(client) == [HAIKU, SONNET, SONNET]


def test_a_chosen_model_answers_everything():
    client = FakeClient(message(text("Done.")))
    agent = Agent(client=client, model=SONNET, cheap_model=SONNET)
    run(agent.respond("turn on dark mode", Owner()))
    assert models(client) == [SONNET]


def test_a_request_that_turns_to_the_pc_screen_goes_on_with_haiku():
    client = FakeClient(message(thinking(), tool_use("t1", "pc_open_app", app="Discord"), stop="tool_use"),
                        message(tool_use("t2", "pc_screen"), stop="tool_use"),
                        message(tool_use("t3", "pc_act", actions=[{"do": "click", "element": 1}]), stop="tool_use"),
                        message(text("You're in the voice channel.")))
    agent = make_agent(client)
    request_text = "open discord and join the call in the general voice channel"
    assert personal_or_risky(request_text) and not pc_task(request_text)
    reply = run(agent.respond(request_text, Owner()))
    assert models(client) == [SONNET, SONNET, HAIKU, HAIKU] and reply.model == HAIKU
    # The conversation only grew: Haiku gets everything Sonnet saw, its thinking included.
    assert client.requests[2]["messages"][:len(client.requests[1]["messages"])] == client.requests[1]["messages"]


def test_once_sonnet_has_taken_over_it_keeps_the_request():
    client = FakeClient(message(tool_use("t1", "flaky"), stop="tool_use"),
                        message(tool_use("t2", "flaky"), stop="tool_use"),
                        message(tool_use("t3", "pc_screen"), stop="tool_use"),
                        message(text("Done.")))
    agent = make_agent(client, [failing_tool()])
    run(agent.respond("click the blue button on my screen", Owner()))
    assert models(client) == [HAIKU, HAIKU, SONNET, SONNET]


def test_other_tools_keep_sonnet():
    client = FakeClient(message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
                        message(text("Opened Chrome.")))
    run(make_agent(client).respond("start Chrome and tell me when my email is open", Owner()))
    assert models(client) == [SONNET, SONNET]


def test_the_instructions_are_cached_for_an_hour_and_the_conversation_for_five_minutes():
    client = FakeClient(message(text("Hi.")))
    run(Agent(client=client).respond("hello", Owner()))
    request = client.requests[0]
    assert request["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert request["cache_control"] == {"type": "ephemeral"}  # after it: longer ones must come first


def test_an_hours_cache_write_costs_twice_the_input():
    from types import SimpleNamespace

    from natro_agent.models import cost

    usage = SimpleNamespace(input_tokens=10, output_tokens=100, cache_read_input_tokens=1000,
                            cache_creation_input_tokens=25000,
                            cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=24000,
                                                           ephemeral_5m_input_tokens=1000))
    expected = (10 * 2.0 + 100 * 10.0 + 24000 * 2.0 * 2.0 + 1000 * 2.0 * 1.25 + 1000 * 2.0 * 0.1) / 1e6
    assert abs(cost(SONNET, usage) - expected) < 1e-12
    usage.cache_creation = None  # an older answer: every write counted as 5 minutes'
    assert abs(cost(SONNET, usage) - (10 * 2.0 + 100 * 10.0 + 25000 * 2.0 * 1.25 + 1000 * 2.0 * 0.1) / 1e6) < 1e-12
