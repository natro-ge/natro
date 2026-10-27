"""The phone's messages on the PC (shown there, answered from there, let go of when they're read), and messaging
anyone on Instagram and Messenger (send_message, from Natro's own address book)."""
import asyncio
from datetime import date

import pytest

from conftest import FakeClient, message, run, text, tool_use
from natro_agent import chats
from natro_agent.agent import Agent
from natro_agent.jobs import Jobs
from natro_agent.people import People, username_from
from natro_agent.server import Server
from natro_agent.tools import ToolError
from test_jobs import job_hello, take
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


def test_a_message_read_on_the_phone_goes_from_the_pc_but_his_answer_still_goes():
    server = Server(Agent(client=FakeClient()))
    calls = []

    def kept(call):  # the phone answers with the Reply it kept for that chat
        calls.append(call)
        return {"ok": True, "result": "Sent to Nino on Instagram."}

    def none(call):
        return {"ok": False, "result": "That message's notification is gone from the phone (it was read or "
                                       "dismissed), so it can't be answered from there."}

    async def play(pc, phone):
        await phone.send(**NINO)
        shown = await pc.next("phone_message")
        await phone.send(type="phone_message_gone", key=NINO["key"])
        await phone.send(type="phone_message_gone", key=NINO["key"])  # told twice: let go of once
        gone = await pc.next("phone_message_gone")
        await pc.send(type="message_reply", id="r2", to=shown["id"], text="Yes")
        answering = asyncio.create_task(phone.next("never", answer_tools=kept))
        reply = await pc.next("reply")
        answering.cancel()
        await pc.send(type="message_reply", id="r3", to=shown["id"], text="Hello?")
        answering = asyncio.create_task(phone.next("never", answer_tools=none))
        error = await pc.next("error")
        answering.cancel()
        await pc.send(type="message_reply", id="r4", to="pm-unknown", text="Yes")
        return shown, gone, reply, error, await pc.next("error"), pc.seen

    shown, gone, reply, error, unknown, seen = run(devices(server, pc_hello(), hello("phone", "phone", REPLY),
                                                           play=play))
    assert gone["id"] == shown["id"]
    assert sum(1 for m in seen if isinstance(m, dict) and m["type"] == "phone_message_gone") == 1
    assert calls[0]["args"] == {"key": NINO["key"], "text": "Yes"} and reply["text"] == "Sent to Nino on Instagram."
    assert error["id"] == "r3" and "gone from the phone" in error["message"]
    assert server.chat.entries["pc:r3"]["problem"] == error["message"]
    assert unknown["id"] == "r4" and "isn't on your phone any more" in unknown["message"]


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


# --- Messaging anyone: send_message --------------------------------------------------------------

SEND = {"name": "send_message", "description": "Send a message.", "personal": True, "confirm": True, "hidden": True,
        "input_schema": {"type": "object", "properties": {}, "required": []}}
NINO_CHAT = "340282366841710300949128137443944319108"


def book(tmp_path):
    people = People(tmp_path / "people.md")
    people.seen("instagram", "Nino Kapanadze", NINO_CHAT)
    people.seen("instagram", "Giorgi", "3402823668417103009491281374439400001")
    people.seen("messenger", "Andria Beridze", "77001", facebook="100012345678901")
    return people


def sending(tmp_path, *replies, jobs=None):
    client = FakeClient(*replies)
    return client, Server(Agent(client=client), people=book(tmp_path), jobs=jobs)


def send(**args):
    return message(tool_use("t1", "send_message", **args), stop="tool_use")


def results(client):
    """What each tool call gave the model, in order."""
    return [block["content"] for request in client.requests for turn in request["messages"][-1:]
            if isinstance(turn["content"], list) for block in turn["content"] if block.get("type") == "tool_result"]


def test_a_message_goes_after_his_yes_to_the_servers_own_question(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Running 10 min late.", app="instagram"))
    calls = []

    def phone_sends(call):
        calls.append(call)
        return {"ok": True, "result": "Sent to Nino Kapanadze on Instagram."}

    async def play(phone):
        await phone.send(type="text", id="r1", text="message Nino on Instagram: running 10 min late")
        asked = await phone.next("confirm")
        await phone.send(type="answer", id=asked["id"], yes=True)
        return asked, await phone.next("reply", answer_tools=phone_sends)

    asked, reply = run(devices(server, hello("phone", "phone", SEND), play=play))
    assert asked["question"] == 'Send "Running 10 min late." to Nino Kapanadze on Instagram?'
    call, = calls
    # The phone's quiet ways first (its chat's Reply): nothing shows.
    assert call["name"] == "send_message" and call["args"] == {
        "app": "instagram", "name": "Nino Kapanadze", "chat": NINO_CHAT, "facebook": "", "username": "",
        "group": False, "text": "Running 10 min late.", "screen": False}
    # The phone's words are the reply, with one model call; the phone's own send_message isn't offered to it.
    assert reply["text"] == "Sent to Nino Kapanadze on Instagram." and len(client.requests) == 1
    offered = {tool["name"] for tool in client.requests[0]["tools"]}
    assert "send_message" in offered and "phone_send_message" not in offered


def test_nothing_goes_on_no_and_messenger_finds_georgian_letters(tmp_path):
    client, server = sending(tmp_path, send(to="ანდრია", text="See you at 8.", app="messenger"))

    async def play(phone):
        await phone.send(type="text", id="r1", text="message Andria on Messenger: see you at 8")
        asked = await phone.next("confirm")
        await phone.send(type="answer", id=asked["id"], yes=False)
        return asked, await phone.next("reply"), phone.seen

    asked, reply, seen = run(devices(server, hello("phone", "phone", SEND), play=play))
    assert asked["question"] == 'Send "See you at 8." to Andria Beridze on Messenger?'
    assert reply["text"] == "I didn't send it."
    assert not any(isinstance(m, dict) and m["type"] == "tool_call" for m in seen)


@pytest.mark.parametrize("to, question", [
    ("Gi", 'Send "Hi." to Giorgi on Instagram?'),  # part of a name
    ("@nino.k", 'Send "Hi." to nino.k on Instagram?'),  # a username he gave, with nobody in the book
])
def test_whom_it_asks_about(tmp_path, to, question):
    client, server = sending(tmp_path, send(to=to, text="Hi."))

    async def play(phone):
        await phone.send(type="text", id="r1", text=f"DM {to}: hi")
        return await phone.next("confirm")

    assert run(devices(server, hello("phone", "phone", SEND), play=play))["question"] == question


def test_several_fit_or_nobody_can_be_reached_and_nothing_is_asked(tmp_path):
    people = book(tmp_path)
    people.seen("instagram", "Nino Beridze", "3402823668417103009491281374439400002")
    client = FakeClient(send(to="Nino", text="Hi."), message(text("Which Nino?")),
                        send(to="Zura", text="Hi."), message(text("What's Zura's username?")))
    server = Server(Agent(client=client), people=people)

    async def play(phone):
        await phone.send(type="text", id="r1", text="DM Nino: hi")
        first = await phone.next("reply")
        await phone.send(type="text", id="r2", text="DM Zura: hi")
        return first, await phone.next("reply"), phone.seen

    first, second, seen = run(devices(server, hello("phone", "phone", SEND), play=play))
    assert not any(isinstance(m, dict) and m["type"] in ("confirm", "tool_call") for m in seen)
    several, nobody = results(client)
    assert "Nino Kapanadze" in several and "Nino Beridze" in several and "Ask him which one" in several
    assert "username" in nobody and "never guess" in nobody
    assert (first["text"], second["text"]) == ("Which Nino?", "What's Zura's username?")


def test_one_message_per_request(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Hi."),
                             message(tool_use("t2", "send_message", to="Giorgi", text="Hi."), stop="tool_use"),
                             message(text("I sent Nino's; ask me again for Giorgi.")))
    sent = []

    def phone_sends(call):
        sent.append(call)
        return {"ok": True, "result": "Sent."}

    async def play(phone):
        await phone.send(type="text", id="r1", text="message Nino and Giorgi on Instagram: hi")
        asked = await phone.next("confirm")
        await phone.send(type="answer", id=asked["id"], yes=True)
        return await phone.next("reply", answer_tools=phone_sends)

    reply = run(devices(server, hello("phone", "phone", SEND), play=play))
    assert len(sent) == 1 and "One at a time" in results(client)[1]
    assert reply["text"] == "I sent Nino's; ask me again for Giorgi."


NO_QUIET = {"ok": False, "result": "No quiet way on the phone: Nino Kapanadze's chat has no Reply here now."}
LOCKED = {"ok": False, "result": "Your phone is locked: unlock it and tell me to send it."}
SENT = {"ok": True, "result": "Sent to Nino Kapanadze on Instagram."}
NOT_OPENED = {"ok": False, "result": "Your chat with Nino Kapanadze didn't open in Edge, or isn't plainly theirs, so "
                                     "nothing was sent."}


def answering(calls, *answers):
    """A device's answers to its tool calls, in order; calls gets each call."""
    left = list(answers)

    def answer(call):
        calls.append(call)
        return left.pop(0)
    return answer


async def asked_and_answered(phone, pc, phone_answers, pc_answers, id="r1"):
    """A request typed on the phone and said yes to; each device answers its calls; the reply."""
    await phone.send(type="text", id=id, text="message Nino: late")
    asked = await phone.next("confirm")
    await phone.send(type="answer", id=asked["id"], yes=True)
    on_pc = asyncio.create_task(pc.next("never", answer_tools=pc_answers)) if pc else None
    reply = await phone.next("reply", answer_tools=phone_answers)
    if on_pc:
        on_pc.cancel()
    return asked, reply


def test_with_no_quiet_way_on_the_phone_natros_hidden_edge_sends_it(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Late."))
    on_phone, on_pc = [], []

    async def play(pc, phone):
        return await asked_and_answered(phone, pc, answering(on_phone, NO_QUIET), answering(on_pc, SENT))

    asked, reply = run(devices(server, hello("pc", "PC", SEND), hello("phone", "phone", SEND), play=play))
    assert asked["question"] == 'Send "Late." to Nino Kapanadze on Instagram?'  # where it goes is no matter of his
    assert [call["args"]["screen"] for call in on_phone] == [False]  # the phone's screen is never used
    assert len(on_pc) == 1 and "screen" not in on_pc[0]["args"] and on_pc[0]["args"]["chat"] == NINO_CHAT
    assert reply["text"] == "Sent to Nino Kapanadze on Instagram." and len(client.requests) == 1


def test_the_phones_screen_only_when_the_pc_cant(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Late."), send(to="Nino", text="Late."),
                             message(text("Check Instagram on your PC.")))
    on_phone, on_pc = [], []

    async def play(pc, phone):
        first = await asked_and_answered(phone, pc, answering(on_phone, NO_QUIET, SENT), answering(on_pc, NOT_OPENED))
        # Sent from the PC, but not seen there: never sent again from the phone.
        unsure = {"ok": False, "result": "I sent it from the PC, but don't see it in the chat: he should check "
                                         "Instagram. Don't send it again."}
        second = await asked_and_answered(phone, pc, answering(on_phone, NO_QUIET), answering(on_pc, unsure), "r2")
        return first, second

    (_, first), (_, second) = run(devices(server, hello("pc", "PC", SEND), hello("phone", "phone", SEND), play=play))
    assert [call["args"].get("screen") for call in on_phone] == [False, None, False]  # None: the screen allowed
    assert first["text"] == "Sent to Nino Kapanadze on Instagram." and len(on_pc) == 2
    assert "Don't send it again" in results(client)[-1] and second["text"] == "Check Instagram on your PC."


def test_the_phone_locked_and_the_pc_off_says_so(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Late."), message(text("Unlock your phone.")))
    on_phone = []

    async def play(phone):
        return await asked_and_answered(phone, None, answering(on_phone, NO_QUIET, LOCKED), None)

    run(devices(server, hello("phone", "phone", SEND), play=play))
    told, = results(client)
    assert told.startswith("Your phone is locked") and "His PC is offline" in told and len(on_phone) == 2


def test_the_phone_offline_sends_it_from_the_pc(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Late."), send(to="Nino", text="Late."),
                             message(text("Shall I keep it for your phone?")))
    on_pc = []

    async def play(pc):
        replies = []
        for id, answer in (("r1", SENT), ("r2", NOT_OPENED)):
            await pc.send(type="text", id=id, text="message Nino: late")
            asked = await pc.next("confirm")
            assert asked["question"] == 'Send "Late." to Nino Kapanadze on Instagram?'
            await pc.send(type="answer", id=asked["id"], yes=True)
            replies.append(await pc.next("reply", answer_tools=answering(on_pc, answer)))
        return replies

    first, second = run(devices(server, hello("pc", "PC", SEND), play=play))
    assert first["text"] == "Sent to Nino Kapanadze on Instagram." and len(on_pc) == 2
    assert "queue_for_device" in results(client)[-1] and second["text"] == "Shall I keep it for your phone?"


def test_offline_or_an_old_phone_app_says_so(tmp_path):
    client, server = sending(tmp_path, send(to="Nino", text="Hi."), message(text("Your phone is offline.")),
                             send(to="Nino", text="Hi."), message(text("The phone app needs updating.")))

    async def nothing_can_send(pc):  # the PC without Edge's script, the phone offline
        await pc.send(type="text", id="r1", text="message Nino: hi")
        return await pc.next("reply")

    run(devices(server, hello("pc", "PC"), play=nothing_can_send))

    async def old_phone(phone):
        await phone.send(type="text", id="r2", text="message Nino: hi")
        return await phone.next("reply")

    run(devices(server, hello("phone", "phone"), play=old_phone))
    offline, old = results(client)
    assert "offline" in offline and "queue_for_device" in offline and "needs updating" in old


def test_a_background_job_asks_each_time(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    task = "Tell Nino on Instagram that I'm running late."
    client, server = sending(tmp_path, message(tool_use("t1", "start_job", task=task), stop="tool_use"),
                             send(to="Nino", text="I'm running late."), jobs=Jobs())
    sent = []

    def phone_sends(call):
        sent.append(call)
        return {"ok": True, "result": "Sent to Nino Kapanadze on Instagram."}

    async def play(phone):
        await phone.send(type="text", id="r1", text="tell Nino I'm late in the background")
        await take(phone, "reply")
        asked = await take(phone, "confirm")
        assert asked["request"].startswith("natro:jb")
        await phone.send(type="answer", id=asked["id"], yes=True)
        return asked, await take(phone, "job_done", answer_tools=phone_sends)

    asked, done = run(devices(server, job_hello("phone", "phone", SEND), play=play))
    assert asked["question"] == "Send \"I'm running late.\" to Nino Kapanadze on Instagram?"
    # The phone's words are the job's answer too (one model call).
    assert [call["name"] for call in sent] == ["send_message"]
    assert done["text"] == "Sent to Nino Kapanadze on Instagram." and len(client.requests) == 2


# --- The address book ----------------------------------------------------------------------------

def test_the_phone_fills_the_address_book_once_a_day(tmp_path):
    people = People(tmp_path / "people.md")
    server = Server(Agent(client=FakeClient()), people=people)
    andria = {"type": "person", "app": "messenger", "name": "Andria Beridze", "chat": "77001",
              "facebook": "100012345678901", "group": False}

    async def play(phone):
        await phone.send(**andria)
        await phone.send(**{**andria, "name": "Andria T."})  # renamed in the app
        await phone.send(type="person", app="instagram", name="Family", chat="99", group=True)
        await phone.send(type="person", app="whatsapp", name="Nino", chat="1")  # not an app it sends on
        await asyncio.sleep(0.2)

    run(devices(server, hello("phone", "phone"), play=play))
    lines = [line for line in (tmp_path / "people.md").read_text(encoding="utf-8").splitlines()
             if line.startswith("- ")]
    today = date.today().isoformat()
    assert lines == [f"- Andria T. · Messenger · chat 77001 · facebook 100012345678901 · last {today}",
                     f"- Family · Instagram · group · chat 99 · last {today}"]
    # The same chat again on the same day: the file isn't written again; the next day it is.
    assert not people.seen("messenger", "Andria T.", "77001", "100012345678901")
    assert people.seen("messenger", "Andria T.", "77001", "100012345678901", today=date(2030, 1, 1))


def test_his_own_lines_and_edits_are_kept(tmp_path):
    path = tmp_path / "people.md"
    path.write_text("# People\n\nMy notes stay.\n\n- Nino · Instagram · username nino.k\n- nonsense line\n",
                    encoding="utf-8")
    people = People(path)
    people.seen("instagram", "Nino", "3402")  # the one he added by hand gets its chat
    now = path.read_text(encoding="utf-8")
    assert "My notes stay." in now and "- Nino · Instagram · chat 3402 · username nino.k · last " in now
    assert [person.name for person in people.find("instagram", "nino")] == ["Nino"]
    assert people.find("instagram", "Family") == [] and people.find("messenger", "nino") == []


def test_a_group_fits_only_by_its_whole_name(tmp_path):
    people = People(tmp_path / "people.md")
    people.seen("messenger", "Family", "fam", group=True)
    people.seen("messenger", "Fanny Smith", "fs")
    assert [p.name for p in people.find("messenger", "the family group")] == ["Family"]
    assert [p.name for p in people.find("messenger", "fa")] == ["Fanny Smith"]


def test_people_save_checks_the_username_and_saves_it(tmp_path):
    people = book(tmp_path)
    tools = {tool.name: tool for tool in people.tools()}
    assert run(tools["people_save"].run({"name": "Nino", "username": "https://www.instagram.com/nino.k/"})) == \
        "Saved: Nino Kapanadze is nino.k on Instagram."
    assert run(tools["people_save"].run({"name": "Zura", "username": "@zura_99"})) == \
        "Saved: Zura is zura_99 on Instagram."
    assert [person.username for person in people.find("instagram", "zura")] == ["zura_99"]
    assert username_from("messenger", "m.me/andria.beridze") == "andria.beridze"
    for wrong in ("nino k", "nino!", ".nino", "a" * 31, ""):
        with pytest.raises(ToolError):
            username_from("instagram", wrong)
    with pytest.raises(ToolError):
        run(tools["people_save"].run({"name": "Nino", "username": "x", "app": "whatsapp"}))
    assert tools["people_forget"].confirm and tools["people_save"].personal
    assert run(tools["people_forget"].run({"name": "Zura"})) == "Forgot Zura (zura_99) on Instagram."
    assert people.find("instagram", "zura") == []
