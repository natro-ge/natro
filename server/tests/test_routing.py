"""Routing: Haiku answers plain requests, Sonnet personal or risky ones and the rest of their conversation."""
import pytest

from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent.agent import Agent
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.routing import georgian, pc_task, personal_or_risky
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
    assert SECRET in request["system"][-1]["text"]
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
    ("call Nino", True), ("what do you know about me?", True), ("DM Nino that I'm outside", True),
    ("tell Nino on insta I'm late", True), ("Instagram Andria: see you at 8", True),
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


@pytest.mark.parametrize("request_text", [
    # All his, from the logs: Latin letters, Georgian ones, and with English in them.
    "4ze gamaxsene rom zoomershi wavide", "ekrani machvene", "kompiuteri gamorte", "rodis maqvs gamocdebi",
    "xval shemaxsene ilovepdf MCP unda davayeno", "spotify gaxseni da homecoming charte", "ra folderebi maqvs desktopze",
    "kanye west homecoming charte", "andrias miwere yo instagramze", "23shi sagamos shemekitxe", "ekrans shexede da yvela",
    "\u10ee\u10d5\u10d0\u10da 3\u10e1\u10d9\u10d4\u10dc \u10d2\u10d0\u10db\u10d0\u10ee\u10e1\u10d4\u10dc\u10d4",
    "tetsus daureke", "telefonze ios daureke", "davureko", "nini natroshvilis instagram gaseni", "ipconfig gaige",
    "musika tame impala", "pcze vambob",
])
def test_georgian_is_seen(request_text):
    assert georgian(request_text)


@pytest.mark.parametrize("request_text", [
    "What's the capital of France?", "open Chrome", "play On Repeat on Spotify", "analyze this picture", "what size is it",
    "remember that my dentist is on Friday", "take a screenshot of my PC screen", "the next song", "text Nino that I'm late",
    "how much free space is left on drive C?", "turn on dark mode", "summarize the entire week", "call a taxi",
    "force close Calculator", "check my email and tasks", "order Giro Mari from Mekhi on Wolt",
])
def test_english_isnt_taken_for_georgian(request_text):
    assert not georgian(request_text)


def test_georgian_goes_to_sonnet_and_it_keeps_the_conversation(tmp_path):
    claude = FakeClient(message(text("Which store?")), message(text("OK.")))
    agent = make_agent(tmp_path, claude)
    owner = Owner()
    run(agent.respond("4ze gamaxsene rom wavide", owner))
    run(agent.respond("magaziaa", owner))  # one word it doesn't know: the conversation is Sonnet's by now
    assert models(claude) == [SONNET, SONNET]


def test_georgian_about_the_pc_stays_on_sonnet_through_its_screen(tmp_path):
    claude = FakeClient(message(tool_use("t1", "pc_screen"), stop="tool_use"), message(text("There's a calculator.")))
    agent = make_agent(tmp_path, claude)
    assert pc_task("kompiuteris screen machvene")
    reply = run(agent.respond("kompiuteris screen machvene", Owner()))
    assert models(claude) == [SONNET, SONNET] and reply.model == SONNET
