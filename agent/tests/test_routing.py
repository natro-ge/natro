"""Routing: Haiku answers plain requests, Sonnet personal or risky ones and the rest of their conversation."""
import pytest

from conftest import FakeClient, Owner, message, run, text
from natro_agent.agent import Agent, personal_or_risky
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.websearch import WebSearch

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
SECRET = "Tuxa's sister is Nino."


def make_agent(tmp_path, claude, budget=15.0):
    memory = Memory(tmp_path / "memory")
    memory.remember(SECRET, "told")
    search = WebSearch(key="tvly-test", post=lambda url, body, key: {"results": []})
    tools = memory.tools() + Notes(tmp_path / "notes").tools() + search.tools()
    agent = Agent(tools, client=claude, budget=budget, memory=memory)
    agent.connect(DryRunDevice("pc", "PC", PC_TOOLS))
    return agent


def models(client):
    return [request["model"] for request in client.requests]


def test_a_plain_request_is_answered_by_haiku_with_everything(tmp_path):
    claude = FakeClient(message(text("Paris.")))
    reply = run(make_agent(tmp_path, claude).respond("What's the capital of France?", Owner()))
    assert reply.text == "Paris." and reply.model == HAIKU
    # The same instructions and tools whichever model answers: the memory, personal and risky tools too.
    request = claude.requests[0]
    assert SECRET in request["system"][0]["text"]
    assert {"remember", "web_search", "pc_force_close_app"} <= {tool["name"] for tool in request["tools"]}


def test_a_personal_request_goes_to_sonnet(tmp_path):
    claude = FakeClient(message(text("I'll remember that.")))
    reply = run(make_agent(tmp_path, claude).respond("Remember that my dentist is on Friday", Owner()))
    assert reply.model == SONNET and models(claude) == [SONNET]


def test_once_a_conversation_turns_personal_sonnet_keeps_it_but_the_pcs(tmp_path):
    claude = FakeClient(message(text("Hello!")), message(text("Noted.")), message(text("You're welcome.")),
                        message(text("It's 192.168.1.5.")))
    agent = make_agent(tmp_path, claude)
    owner = Owner()
    run(agent.respond("hello", owner))
    run(agent.respond("add milk to my shopping note", owner))
    run(agent.respond("thanks", owner))
    run(agent.respond("what's my IP address?", owner))
    assert models(claude) == [HAIKU, SONNET, SONNET, HAIKU]
    # Still one conversation, which only grows.
    for before, after in zip(claude.requests, claude.requests[1:]):
        assert after["messages"][:len(before["messages"])] == before["messages"]


def test_a_new_conversation_starts_on_haiku_again(tmp_path):
    claude = FakeClient(message(text("Noted.")), message(text("Hi!")))
    agent = make_agent(tmp_path, claude)
    run(agent.respond("remember I like jazz", Owner()))
    agent.new_conversation()
    run(agent.respond("hello", Owner()))
    assert models(claude) == [SONNET, HAIKU]


def test_over_budget_every_request_asks_first(tmp_path):
    claude = FakeClient()
    owner = Owner(answer=False)
    reply = run(make_agent(tmp_path, claude, budget=0.0).respond("capital of France?", owner))
    assert reply.text == "OK, I won't use Claude for that." and "budget is used up" in owner.questions[0][0]
    assert claude.requests == []


@pytest.mark.parametrize("request_text, personal", [
    ("open Chrome", False), ("play On Repeat on Spotify", False), ("weather in Tbilisi tomorrow", False),
    ("what's 15% of 80?", False), ("remember that I like jazz", True), ("what's in my calendar today?", True),
    ("read my latest email", True), ("add eggs to the shopping note", True), ("delete that file", True),
    ("call Nino", True), ("what do you know about me?", True),
])
def test_personal_or_risky(request_text, personal):
    assert personal_or_risky(request_text) is personal


def test_a_picture_he_sent_goes_to_sonnet(tmp_path):
    from io import BytesIO

    from PIL import Image

    claude = FakeClient(message(text("A cat on a sofa.")))
    agent = make_agent(tmp_path, claude)
    out = BytesIO()
    Image.new("RGB", (64, 64), "gray").save(out, "JPEG")
    assert agent.picture_sent("image/jpeg", out.getvalue(), "phone")
    reply = run(agent.respond("what is this?", Owner()))
    assert reply.text == "A cat on a sofa." and reply.model == SONNET
    assert claude.requests[0]["messages"][0]["content"][0]["type"] == "image"
