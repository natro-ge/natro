"""The phone's messages on the PC: shown there, answered from there, and let go of when they're read."""
import asyncio

from conftest import FakeClient, run
from natro_agent.agent import Agent
from natro_agent.server import Server
from test_shared_chat import devices, hello

REPLY = {"name": "reply_message", "description": "Answer a message.", "personal": True, "confirm": True,
         "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
NINO = {"type": "phone_message", "key": "0|com.whatsapp|1|nino", "app": "WhatsApp", "title": "Nino",
        "text": "Are you coming?", "reply": True}


def pc_hello():
    return {**hello("pc", "PC"), "phone_messages": True}


def test_a_message_shows_on_the_pc_and_his_answer_goes_from_the_phone():
    client = FakeClient()  # no model is asked
    server = Server(Agent(client=client))
    calls = []

    def answer(call):
        calls.append(call)
        return {"ok": True, "result": "Sent to Nino on WhatsApp."}

    async def play(pc, phone):
        await phone.send(**NINO)
        shown = await pc.next("phone_message")
        await pc.send(type="message_reply", id="r9", to=shown["id"], text="On my way!")
        answering = asyncio.create_task(phone.next("never", answer_tools=answer))
        reply = await pc.next("reply")
        answering.cancel()
        return shown, reply

    shown, reply = run(devices(server, pc_hello(), hello("phone", "phone", REPLY), play=play))
    assert {k: shown[k] for k in ("app", "title", "text", "reply", "label")} == {
        "app": "WhatsApp", "title": "Nino", "text": "Are you coming?", "reply": True, "label": "phone"}
    assert "key" not in shown  # the phone's own name for it stays on the server
    call, = calls
    assert call["name"] == "reply_message" and call["args"] == {"key": NINO["key"], "text": "On my way!"}
    assert call["request"] == "pc:r9"
    assert reply["text"] == "Sent to Nino on WhatsApp." and reply["model"] == "none" and reply["cost"] == 0
    entry = server.chat.entries["pc:r9"]
    assert entry["asked"] == "To Nino on WhatsApp: On my way!" and entry["reply"] == "Sent to Nino on WhatsApp."
    assert client.requests == []


def test_a_message_read_on_the_phone_goes_from_the_pc_and_cant_be_answered():
    server = Server(Agent(client=FakeClient()))

    async def play(pc, phone):
        await phone.send(**NINO)
        shown = await pc.next("phone_message")
        await phone.send(type="phone_message_gone", key=NINO["key"])
        gone = await pc.next("phone_message_gone")
        await pc.send(type="message_reply", id="r2", to=shown["id"], text="Yes")
        return shown, gone, await pc.next("error")

    shown, gone, error = run(devices(server, pc_hello(), hello("phone", "phone", REPLY), play=play))
    assert gone["id"] == shown["id"]
    assert error["id"] == "r2" and "isn't on your phone any more" in error["message"]
    assert server.chat.entries["pc:r2"]["problem"] == error["message"]


def test_no_message_goes_where_none_are_shown_and_an_old_phone_app_says_so():
    server = Server(Agent(client=FakeClient()))

    async def play(pc, phone):
        await phone.send(**NINO)
        shown = await pc.next("phone_message")
        await pc.send(type="message_reply", id="r3", to=shown["id"], text="Yes")
        error = await pc.next("error")
        return [m for m in phone.seen if isinstance(m, dict) and m["type"] == "phone_message"], error

    # The phone's app without reply_message; the phone itself never gets messages.
    seen, error = run(devices(server, pc_hello(), hello("phone", "phone"), play=play))
    assert seen == [] and "needs updating" in error["message"]

    async def alone(phone):  # no device shows messages: nothing is kept
        await phone.send(**NINO)
        await asyncio.sleep(0.2)

    nobody = Server(Agent(client=FakeClient()))
    run(devices(nobody, hello("phone", "phone", REPLY), play=alone))
    assert not nobody._messages


def test_his_answer_when_the_phone_is_offline():
    server = Server(Agent(client=FakeClient()))

    async def first(pc, phone):
        await phone.send(**NINO)
        return await pc.next("phone_message")

    shown = run(devices(server, pc_hello(), hello("phone", "phone", REPLY), play=first))

    async def later(pc):
        await pc.send(type="message_reply", id="r4", to=shown["id"], text="Yes")
        return await pc.next("error")

    assert "phone is offline" in run(devices(server, pc_hello(), play=later))["message"]
