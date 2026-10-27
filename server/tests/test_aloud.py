"""Saying something out loud when he asks: on the device he asked on, or on the other one, whatever "Speak
replies" says there."""
import asyncio

from conftest import FakeClient, message, run, text, tool_use
from natro_agent.agent import Agent
from natro_agent.aloud import SAID, Aloud
from natro_agent.server import Server
from natro_agent.tools import ToolError
from test_shared_chat import devices, hello


def speaking(name, label):
    return {**hello(name, label), "say": True}


def test_said_where_he_asked_even_typed_and_the_reply_isnt_said_again():
    client = FakeClient(message(tool_use("t1", "say_out_loud", text="Good morning."), stop="tool_use"))
    server = Server(Agent(client=client))

    async def play(pc):
        await pc.send(type="text", id="r1", text="say out loud good morning")
        return await pc.next("say"), await pc.next("reply")

    said, reply = run(devices(server, speaking("pc", "PC"), play=play))
    assert said["text"] == "Good morning." and said["from"] == "pc"
    # The words are the reply, with no second model call to word it, and the device doesn't say them again.
    assert reply["text"] == "Good morning." and reply["said"] is True
    assert len(client.requests) == 1


def test_the_phone_has_the_pc_say_it():
    client = FakeClient(message(tool_use("t1", "say_out_loud", text="Dinner's ready.", device="pc"), stop="tool_use"))
    server = Server(Agent(client=client))

    async def play(pc, phone):
        await phone.send(type="text", id="r1", text="say on the PC that dinner's ready")
        reply = await phone.next("reply")
        return await pc.next("say"), reply, phone.seen

    said, reply, on_phone = run(devices(server, speaking("pc", "PC"), speaking("phone", "phone"), play=play))
    assert said["text"] == "Dinner's ready." and said["from"] == "phone"  # the PC shows it, from the phone
    # The phone's reply is spoken there as any reply is; it says nothing out loud itself.
    assert reply["text"] == "I said it out loud on the PC." and "said" not in reply
    assert not any(m["type"] == "say" for m in on_phone)


def test_an_old_app_or_an_offline_device_is_said_so():
    client = FakeClient(
        message(tool_use("t1", "say_out_loud", text="Hello."), stop="tool_use"),
        message(text("Your PC's app needs updating for that.")),
        message(tool_use("t2", "say_out_loud", text="Hello.", device="phone"), stop="tool_use"),
        message(text("Your phone is offline.")),
    )
    server = Server(Agent(client=client))

    async def play(pc):  # the PC's app from before "say"
        await pc.send(type="text", id="r1", text="say out loud hello")
        first = await pc.next("reply")
        await pc.send(type="text", id="r2", text="say hello out loud on the phone")
        return first, await pc.next("reply"), pc.seen

    first, second, seen = run(devices(server, hello("pc", "PC"), play=play))
    results = [block for request in (client.requests[1], client.requests[3])
               for block in request["messages"][-1]["content"]]
    assert results[0]["is_error"] and "needs updating" in results[0]["content"]
    assert results[1]["is_error"] and "phone is offline" in results[1]["content"]
    assert not any(m["type"] == "say" for m in seen)
    assert "said" not in first and "said" not in second


class Speaker:
    name, label, say = "phone", "phone", True

    def __init__(self):
        self.posted = []

    def post(self, **message):
        self.posted.append(message)


def test_no_words_or_no_device_to_say_it_on():
    aloud = Aloud({"phone": Speaker()})
    for args, problem in (({"text": "  "}, "Give the words"), ({"text": "Hi."}, "Say which device")):
        try:
            run(aloud.say(args))
        except ToolError as e:
            assert problem in str(e)
        else:
            raise AssertionError(f"{args} was said")


def test_a_background_job_says_it_without_marking_a_reply():
    phone = Speaker()
    aloud = Aloud({"phone": phone})
    aloud.asking = lambda: phone

    async def job():
        SAID.set(None)  # as the server's _job does
        return await aloud.say({"text": "The flats are in your notes."})

    assert run(job()).startswith("The flats are in your notes.\n")
    assert phone.posted[0]["text"] == "The flats are in your notes."

    async def request():
        said = []
        SAID.set(said)
        await asyncio.gather(aloud.say({"text": "Hi."}))  # a tool runs in a task of its own
        return said

    assert run(request()) == ["Hi."]
