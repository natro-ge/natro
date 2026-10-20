"""The chat both devices show, pictures in it, and requests kept for a device that's offline."""
import asyncio
import base64
import io
import json
import time

from PIL import Image
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent import chats
from natro_agent.agent import PICTURE_MINUTES, Agent
from natro_agent.pending import PendingRequests
from natro_agent.server import Server, health

SCREENSHOT = {"name": "screenshot", "description": "A picture of the screen.", "personal": True,
              "input_schema": {"type": "object", "properties": {}, "required": []}}
CLOSE = {"name": "force_close_app", "description": "Kill an app.", "confirm": True,
         "input_schema": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]}}


def hello(name, label, *tools, queue=True, questions=False):
    return {"type": "hello", "device": name, "label": label, "tools": list(tools), "chat": True, "queue": queue,
            "questions": questions}


class Device:
    """One end of a WebSocket, as an app: everything it got, in order (bytes too)."""

    def __init__(self, ws):
        self.ws = ws
        self.seen = []

    async def send(self, **data):
        await self.ws.send(json.dumps(data))

    async def next(self, kind, answer_tools=None):
        """Read until a message of this kind (answering tool calls with answer_tools(call)); returns it."""
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), 5)
            data = raw if isinstance(raw, bytes) else json.loads(raw)
            self.seen.append(data)
            if isinstance(data, dict) and data["type"] == "tool_call" and answer_tools:
                await self.send(type="tool_result", id=data["id"], **answer_tools(data))
            if isinstance(data, dict) and data["type"] == kind:
                return data

    def entries(self):
        return [m["entry"] for m in self.seen if isinstance(m, dict) and m["type"] == "chat_entry"]


async def devices(server, *hellos, play):
    """Run the server and connect each device (after its welcome); play(*devices) plays them."""
    async with serve(server.handle, "127.0.0.1", 0, process_request=health) as listener:
        port = listener.sockets[0].getsockname()[1]
        opened = []
        try:
            for greeting in hellos:
                ws = await connect(f"ws://127.0.0.1:{port}")
                device = Device(ws)
                await device.send(**greeting)
                await device.next("welcome")
                opened.append(device)
            return await play(*opened)
        finally:
            for device in opened:
                await device.ws.close()


def test_both_devices_see_a_request_and_its_reply_and_a_late_one_catches_up(monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    server = Server(Agent(client=FakeClient(message(text("It's sunny.")))))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="what's the weather")
        await pc.next("reply")
        while not any(e["done"] for e in phone.entries()):
            await phone.next("chat_entry")
        return phone.entries()

    entries = run(devices(server, hello("pc", "PC"), hello("phone", "phone"), play=play))
    assert entries[0]["id"] == "pc:r1" and entries[0]["asked"] == "what's the weather" and entries[0]["label"] == "PC"
    assert entries[-1]["reply"] == "It's sunny." and entries[-1]["done"]

    async def late(phone):  # the chat so far, for a device that connects after
        return await phone.next("chat_all")

    caught = run(devices(server, hello("phone", "phone"), play=late))
    assert [e["reply"] for e in caught["entries"]] == ["It's sunny."]


def test_new_chat_clears_both_and_starts_a_new_conversation():
    server = Server(Agent(client=FakeClient(message(text("Hi.")))))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="hi")
        await pc.next("reply")
        assert server.agent.session is not None
        await phone.send(type="chat_clear")
        await pc.next("chat_cleared")
        await phone.next("chat_cleared")

    run(devices(server, hello("pc", "PC"), hello("phone", "phone"), play=play))
    assert server.chat.entries == {} and server.agent.session is None


def jpeg_of(width, height):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "orange").save(out, "JPEG")
    return out.getvalue()


def picture_in(block):
    assert block["type"] == "image" and block["source"]["type"] == "base64"
    data = base64.b64decode(block["source"]["data"])
    with Image.open(io.BytesIO(data)) as picture:
        return block["source"]["media_type"], picture.size, data


def test_a_picture_he_sends_shows_on_the_other_device_and_natro_sees_it_with_his_next_request():
    client = FakeClient(message(text("An orange square.")), message(text("Yes.")))
    server = Server(Agent(client=client))
    jpeg = jpeg_of(400, 300)

    async def play(pc, phone):
        await phone.send(type="picture", id="p1", mime="image/jpeg", name="cat.jpg")
        await phone.ws.send(jpeg)
        shown = await pc.next("chat_picture")
        data = await asyncio.wait_for(pc.ws.recv(), 5)
        entry = (await pc.next("chat_entry"))["entry"]
        await phone.send(type="text", id="r2", text="what's this?")
        await phone.next("reply")
        await phone.send(type="text", id="r3", text="is it square?")
        await phone.next("reply")
        return shown, data, entry

    shown, data, entry = run(devices(server, hello("pc", "PC"), hello("phone", "phone"), play=play))
    assert shown["mime"] == "image/jpeg" and shown["name"] == "cat.jpg" and data == jpeg
    assert entry["sent"] and entry["device"] == "phone" and entry["pictures"] == [shown["id"]]
    # The picture first, then his words, whose first line says it's there.
    picture, words = client.requests[0]["messages"][0]["content"]
    assert picture_in(picture) == ("image/jpeg", (400, 300), jpeg)  # small enough: as it was
    assert "he sent a picture into the chat from his phone just now" in words["text"].splitlines()[0]
    assert words["text"].endswith("what's this?") and "cat.jpg" not in words["text"]
    # The follow-up has it in the conversation, but doesn't send it again.
    follow_up = client.requests[1]["messages"]
    assert follow_up[0]["content"][0] == picture and isinstance(follow_up[-1]["content"], str)


def test_a_big_picture_is_made_small_and_one_sent_long_ago_is_left_out(monkeypatch):
    client = FakeClient(message(text("A big one.")), message(text("Hello.")))
    agent = Agent(client=client)
    out = io.BytesIO()
    Image.new("RGBA", (4000, 1000), (0, 0, 255, 0)).save(out, "PNG")
    assert agent.picture_sent("image/png", out.getvalue(), "PC")
    assert not agent.picture_sent("image/png", b"not a picture", "PC")  # Claude would refuse the request
    run(agent.respond("what's in it?", Owner()))
    picture, words = client.requests[0]["messages"][0]["content"]
    mime, size, _ = picture_in(picture)
    assert mime == "image/jpeg" and size == (1568, 392) and "from his PC" in words["text"]
    agent.picture_sent("image/jpeg", jpeg_of(10, 10), "PC")
    later = time.monotonic() + PICTURE_MINUTES * 60 + 1
    monkeypatch.setattr(time, "monotonic", lambda: later)
    run(agent.respond("hello", Owner()))
    assert isinstance(client.requests[1]["messages"][-1]["content"], str)


def test_not_a_picture_is_refused():
    server = Server(Agent(client=FakeClient()))

    async def play(phone):
        await phone.send(type="picture", id="p1", mime="application/zip", name="a.zip")
        await phone.ws.send(b"PK")
        return await phone.next("error")

    assert "can't be shown" in run(devices(server, hello("phone", "phone"), play=play))["message"]


def test_a_screenshot_goes_into_the_chat_of_the_request_and_natro_sees_it():
    client = FakeClient(message(tool_use("t1", "pc_screenshot"), stop="tool_use"), message(text("There it is.")))
    server = Server(Agent(client=client))
    out = io.BytesIO()
    Image.new("RGB", (2560, 1440), "white").save(out, "PNG")
    png = out.getvalue()

    def shoot(call):
        assert call["name"] == "screenshot" and call["request"] == "phone:r1"
        return {"ok": True, "result": "The screenshot is in the chat.",
                "picture": {"mime": "image/png", "name": "screen.png", "data": base64.b64encode(png).decode()}}

    async def play(pc, phone):
        await phone.send(type="text", id="r1", text="show me my PC screen")
        answering = asyncio.create_task(pc.next("never", answer_tools=shoot))
        reply = await phone.next("reply")
        answering.cancel()
        return reply

    run(devices(server, hello("pc", "PC", SCREENSHOT), hello("phone", "phone"), play=play))
    entry = server.chat.entries["phone:r1"]
    assert len(entry["pictures"]) == 1 and server.chat.pictures[entry["pictures"][0]].data == png
    result = client.requests[1]["messages"][-1]["content"][0]
    words, picture = result["content"]
    assert words == {"type": "text", "text": "The screenshot is in the chat."} and not result["is_error"]
    assert picture_in(picture)[:2] == ("image/jpeg", (1568, 882))  # a smaller copy


def test_a_question_can_be_answered_on_the_other_device():
    client = FakeClient(
        message(text("I'll force close Spotify."), tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("Done.")))
    server = Server(Agent(client=client))
    closed = []

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="force close Spotify")
        asked = await pc.next("confirm")
        entry = (await phone.next("chat_entry"))["entry"]
        while not entry["question"]:
            entry = (await phone.next("chat_entry"))["entry"]
        assert entry["question"]["id"] == asked["id"] and entry["question"]["text"] == "I'll force close Spotify."
        await phone.send(type="answer", id=asked["id"], yes=True, device="pc")
        answered = await pc.next("answered", answer_tools=lambda call: closed.append(call) or {"ok": True})
        await pc.next("reply", answer_tools=lambda call: closed.append(call) or {"ok": True, "result": "closed"})
        return answered

    answered = run(devices(server, hello("pc", "PC", CLOSE), hello("phone", "phone"), play=play))
    assert answered["yes"] is True and [call["name"] for call in closed] == ["force_close_app"]
    assert server.chat.entries["pc:r1"]["question"]["answer"] == "Yes"


def risky_close():
    return FakeClient(
        message(text("I'll force close Spotify."), tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("Done.")))


def test_the_other_device_asks_too_and_stops_once_its_answered_there():
    server = Server(Agent(client=risky_close()))
    closed = []

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="force close Spotify")
        asked = await pc.next("confirm")
        elsewhere = await phone.next("question_elsewhere")
        await phone.send(type="answer", id=elsewhere["id"], yes=True, device=elsewhere["device"])
        answered = await pc.next("answered", answer_tools=lambda call: closed.append(call) or {"ok": True})
        done = await phone.next("question_done")
        await pc.next("reply", answer_tools=lambda call: closed.append(call) or {"ok": True, "result": "closed"})
        return asked, elsewhere, answered, done, pc

    asked, elsewhere, answered, done, pc = run(devices(
        server, hello("pc", "PC", CLOSE, questions=True), hello("phone", "phone", questions=True), play=play))
    assert elsewhere == {"type": "question_elsewhere", "id": asked["id"], "device": "pc", "label": "PC",
                         "question": "I'll force close Spotify.", "details": asked["details"]}
    assert answered["yes"] is True and [call["name"] for call in closed] == ["force_close_app"]
    assert done == {"type": "question_done", "id": asked["id"], "device": "pc", "label": "PC", "yes": True}
    assert server.chat.entries["pc:r1"]["question"]["answer"] == "Yes" and server._questions == {}
    # The PC never got its own question as another device's.
    assert not any(isinstance(m, dict) and m["type"].startswith("question_") for m in pc.seen)


def test_a_device_that_connects_while_natro_asks_is_asked_too_and_told_when_its_answered():
    server = Server(Agent(client=risky_close()))

    async def play(pc):
        await pc.send(type="text", id="r1", text="force close Spotify")
        asked = await pc.next("confirm")

        async def phone_comes(phone):
            elsewhere = await phone.next("question_elsewhere")
            await pc.send(type="answer", id=asked["id"], yes=False)
            return elsewhere, await phone.next("question_done")

        late = await devices(server, hello("phone", "phone", questions=True), play=phone_comes)
        await pc.next("reply")
        return asked, late, pc

    asked, (elsewhere, done), pc = run(devices(server, hello("pc", "PC", CLOSE, questions=True), play=play))
    assert elsewhere["id"] == asked["id"] and elsewhere["device"] == "pc"
    assert done["yes"] is False
    assert not any(isinstance(m, dict) and m["type"].startswith("question_") for m in pc.seen)


def test_an_unanswered_question_is_given_up_on_everywhere_and_a_late_answer_does_nothing(monkeypatch):
    from natro_agent import agent as agent_module
    monkeypatch.setattr(agent_module, "CONFIRM_SECONDS", 0.2)
    server = Server(Agent(client=risky_close()))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="force close Spotify")
        elsewhere = await phone.next("question_elsewhere")
        done = await phone.next("question_done")
        await pc.next("reply")
        await phone.send(type="answer", id=elsewhere["id"], yes=True, device="pc")  # the notification, tapped late
        await phone.send(type="cancel", id="r9")
        await phone.next("cancelled")  # the answer before it was taken in
        return done, pc

    done, pc = run(devices(server, hello("pc", "PC", CLOSE), hello("phone", "phone", questions=True), play=play))
    assert done["yes"] is None and server._questions == {}
    assert server.chat.entries["pc:r1"]["question"]["answer"] == ""
    assert not any(isinstance(m, dict) and m["type"] == "answered" for m in pc.seen)


def test_a_request_for_the_offline_pc_waits_and_runs_when_he_says_so(tmp_path):
    client = FakeClient(
        message(tool_use("t1", "queue_for_device", device="pc", request="close Discord"), stop="tool_use"),
        message(text("I'll ask on your PC when it's back.")),
        message(text("Closed Discord.")))
    pending = PendingRequests(tmp_path / "pending.json", {})
    agent = Agent(client=client)
    pending.devices = agent.devices
    server = Server(agent, pending=pending)

    async def ask_from_phone(phone):
        await phone.send(type="text", id="r1", text="close Discord on my PC")
        return await phone.next("reply")

    reply = run(devices(server, hello("phone", "phone"), play=ask_from_phone))
    assert reply["text"] == "I'll ask on your PC when it's back."
    kept = json.loads((tmp_path / "pending.json").read_text(encoding="utf-8"))
    assert [(item["device"], item["text"], item["from"]) for item in kept] == [("pc", "close Discord", "phone")]

    async def pc_comes_back(pc):
        queued = await pc.next("queued")
        item = queued["items"][0]
        await pc.send(type="queue_run", id=item["id"])
        return item, await pc.next("reply")

    item, done = run(devices(server, hello("pc", "PC"), play=pc_comes_back))
    assert item["text"] == "close Discord" and item["from"] == "phone"
    assert done["id"] == item["id"] and done["text"] == "Closed Discord."
    asked = client.requests[2]["messages"][-1]["content"]
    assert "asked for this on his phone while this PC was offline" in json.dumps(asked) and "close Discord" in json.dumps(asked)
    assert json.loads((tmp_path / "pending.json").read_text(encoding="utf-8")) == []
    assert server.chat.entries[f"pc:{item['id']}"]["queued"] == "phone"
    assert server.chat.entries[f"pc:{item['id']}"]["asked"] == "close Discord"


def test_skipped_and_old_requests_go(tmp_path, monkeypatch):
    pending = PendingRequests(tmp_path / "pending.json", {})
    run(pending.keep({"device": "pc", "request": "open Steam"}))
    run(pending.keep({"device": "pc", "request": "lock the PC"}))

    class PC:
        name, queue, posted = "pc", True, []

        def post(self, **data):
            self.posted.append(data)

    pc = PC()
    pending.skip(pc, pending.items[0]["id"])
    assert [item["text"] for item in pending.items] == ["lock the PC"]
    pending.items[0]["time"] -= 25 * 3600 * 1000  # a day and an hour ago
    pending.connected(pc)
    assert pending.items == [] and pc.posted == []
    assert PendingRequests(tmp_path / "pending.json", {}).items == []  # and on disk


def test_nothing_is_kept_for_a_device_that_is_online(tmp_path):
    pending = PendingRequests(tmp_path / "pending.json", {"pc": object()})
    try:
        run(pending.keep({"device": "pc", "request": "open Steam"}))
    except Exception as e:
        assert "online" in str(e)
    else:
        raise AssertionError("kept a request for a device that's online")
