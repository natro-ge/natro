import asyncio
import json
import os
import time

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from conftest import FakeClient, message, run, text, tool_use
from natro_agent import files
from natro_agent.agent import Agent
from natro_agent.files import Transfers, human_size, safe_name
from natro_agent.server import Server, health


class Device:
    """A device app's side of the protocol, over a real WebSocket."""

    def __init__(self, ws, name):
        self.ws, self.name = ws, name
        self.seen = []

    @classmethod
    async def join(cls, port, name, files=True):
        ws = await connect(f"ws://127.0.0.1:{port}", max_size=None)
        await ws.send(json.dumps({"type": "hello", "device": name, "label": name, "token": "secret", "tools": [],
                                  "files": files}))
        assert json.loads(await ws.recv())["type"] == "welcome"
        return cls(ws, name)

    async def next(self, kind=None):
        """The next message (of this kind: others before it are kept in seen)."""
        while True:
            data = await asyncio.wait_for(self.ws.recv(), 5)
            if isinstance(data, bytes):
                return data
            data = json.loads(data)
            self.seen.append(data)
            if kind is None or data["type"] == kind:
                return data

    async def send_file(self, id, to, content, name="report.pdf"):
        """Offers a file and sends it in parts, keeping no more than the server allows unanswered."""
        await self.ws.send(json.dumps({"type": "file_offer", "id": id, "to": to, "name": name, "size": len(content)}))
        sent = acked = 0
        while sent < len(content):
            while sent - acked >= files.WINDOW_BYTES:
                acked = (await self.next("file_got"))["bytes"]
            piece = content[sent:sent + files.PART_BYTES]
            await self.ws.send(json.dumps({"type": "file_part", "id": id}))
            await self.ws.send(piece)
            sent += len(piece)
        await self.ws.send(json.dumps({"type": "file_end", "id": id}))

    async def receive_file(self, hold_acks=False):
        """Takes a file as it comes down; returns (its file_incoming, the bytes, most unanswered at once)."""
        incoming = await self.next("file_incoming")
        content, acked, most = b"", 0, 0
        while True:
            data = await self.next()
            if data["type"] == "file_end":
                break
            assert data["type"] == "file_part" and data["id"] == incoming["id"]
            content += await self.next()
            most = max(most, len(content) - acked)
            if not hold_acks or len(content) - acked >= files.WINDOW_BYTES:
                await self.ws.send(json.dumps({"type": "file_got", "id": incoming["id"], "bytes": len(content)}))
                acked = len(content)
        await self.ws.send(json.dumps({"type": "file_saved", "id": incoming["id"], "name": incoming["name"]}))
        return incoming, content, most


def world(tmp_path, client=None):
    agent = Agent(client=client or FakeClient())
    transfers = Transfers(tmp_path / "transfers", agent.devices)
    return Server(agent, token="secret", transfers=transfers), transfers


async def serving(server, play):
    async with serve(server.handle, "127.0.0.1", 0, process_request=health, max_size=files.PART_BYTES * 2) as listener:
        return await play(listener.sockets[0].getsockname()[1])


def left(transfers):
    return sorted(path.name for path in transfers.folder.iterdir())


def test_a_file_goes_from_the_pc_to_the_phone_and_isnt_kept(tmp_path):
    server, transfers = world(tmp_path)
    content = os.urandom(6 * files.PART_BYTES + 1234)

    async def play(port):
        pc, phone = await Device.join(port, "pc"), await Device.join(port, "phone")
        await pc.send_file("pc-1", "phone", content)
        incoming, got, most = await phone.receive_file(hold_acks=True)
        delivered = await pc.next("file_delivered")
        sent = next(m for m in pc.seen if m["type"] == "file_sent")
        return incoming, got, most, sent, delivered

    incoming, got, most, sent, delivered = run(serving(server, play))
    assert incoming == {"type": "file_incoming", "id": "pc-1", "from": "pc", "name": "report.pdf",
                        "size": len(content)}
    assert got == content
    assert most <= files.WINDOW_BYTES  # the server waited for answers
    assert sent["waiting"] is False
    assert delivered == {"type": "file_delivered", "id": "pc-1", "to": "phone", "name": "report.pdf"}
    assert left(transfers) == []


def test_a_file_waits_for_an_offline_phone_and_is_gone_once_it_has_it(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        pc = await Device.join(port, "pc")
        await pc.send_file("pc-2", "phone", b"hello phone", name="note.txt")
        sent = await pc.next("file_sent")
        kept = left(transfers)
        phone = await Device.join(port, "phone")
        incoming, got, _ = await phone.receive_file()
        await pc.next("file_delivered")
        return sent, kept, got

    sent, kept, got = run(serving(server, play))
    assert sent["waiting"] is True
    assert kept == ["pc-2.file", "pc-2.json"]
    assert got == b"hello phone"
    assert left(transfers) == []


def test_an_upload_cut_off_is_deleted(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        pc = await Device.join(port, "pc")
        await pc.ws.send(json.dumps({"type": "file_offer", "id": "pc-3", "to": "phone", "name": "a", "size": 100}))
        await pc.ws.send(json.dumps({"type": "file_part", "id": "pc-3"}))
        await pc.ws.send(b"x" * 10)
        await pc.next("file_got")
        during = left(transfers)
        await pc.ws.close()
        await asyncio.sleep(0.1)
        return during

    assert run(serving(server, play)) == ["pc-3.part"]
    assert left(transfers) == []


def test_too_big_or_to_nowhere_is_refused(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        pc = await Device.join(port, "pc")
        answers = []
        for id, to, size in [("pc-4", "phone", files.MAX_BYTES + 1), ("pc-5", "pc", 10), ("pc-6", "fridge", 10),
                             ("../x", "phone", 10)]:
            await pc.ws.send(json.dumps({"type": "file_offer", "id": id, "to": to, "name": "a", "size": size}))
            answers.append(await pc.next("file_failed"))
        return answers

    answers = run(serving(server, play))
    assert "at most 1 GB" in answers[0]["message"]
    assert all("phone" in answer["message"] for answer in answers[1:3])
    assert "id" in answers[3]["message"]
    assert left(transfers) == []


def test_a_part_bigger_than_the_offer_stops_the_upload(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        pc = await Device.join(port, "pc")
        await pc.ws.send(json.dumps({"type": "file_offer", "id": "pc-7", "to": "phone", "name": "a", "size": 4}))
        await pc.ws.send(json.dumps({"type": "file_part", "id": "pc-7"}))
        await pc.ws.send(b"too long")
        return await pc.next("file_failed")

    assert "bigger" in run(serving(server, play))["message"]
    assert left(transfers) == []


def test_the_phone_failing_to_save_tells_the_pc_and_deletes_it(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        pc, phone = await Device.join(port, "pc"), await Device.join(port, "phone")
        await pc.send_file("pc-8", "phone", b"data")
        incoming = await phone.next("file_incoming")
        await phone.ws.send(json.dumps({"type": "file_failed", "id": incoming["id"], "message": "Storage is full."}))
        return await pc.next("file_failed")

    assert "Storage is full." in run(serving(server, play))["message"]
    assert left(transfers) == []


def test_asking_natro_has_the_pc_send_the_file(tmp_path):
    path = "C:\\Users\\Tuxa\\Downloads\\invoice.pdf"
    client = FakeClient(
        message(tool_use("t1", "copy_to_device", from_device="pc", path=path, to_device="phone"), stop="tool_use"),
        message(text("It's on its way to your phone.")),
    )
    server, transfers = world(tmp_path, client)

    async def play(port):
        pc, phone = await Device.join(port, "pc"), await Device.join(port, "phone")
        await pc.ws.send(json.dumps({"type": "text", "id": "r1", "text": "send the invoice to my phone"}))
        request = await pc.next("file_request")
        await pc.send_file(request["id"], "phone", b"%PDF-1.7", name="invoice.pdf")
        reply = await pc.next("reply")
        _, got, _ = await phone.receive_file()
        return request, reply, got

    request, reply, got = run(serving(server, play))
    assert request["path"] == path and request["to"] == "phone" and request["id"].startswith("natro-")
    result = client.requests[1]["messages"][-1]["content"][0]
    assert not result["is_error"]
    assert result["content"] == "Sending invoice.pdf (8 bytes) to the phone's Downloads folder."
    assert reply["text"] == "It's on its way to your phone." and got == b"%PDF-1.7"


def test_asking_for_a_file_that_isnt_there_says_why(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        pc = await Device.join(port, "pc")
        tool = next(tool for tool in server.agent.offered_tools() if tool.name == "copy_to_device")
        asked = asyncio.create_task(tool.run({"from_device": "pc", "path": "C:\\nope.txt", "to_device": "phone"}))
        request = await pc.next("file_request")
        await pc.ws.send(json.dumps({"type": "file_failed", "id": request["id"], "message": "There's no such file."}))
        with pytest.raises(Exception) as failed:
            await asked
        return str(failed.value)

    assert run(serving(server, play)) == "There's no such file."


def test_asking_for_a_file_on_an_offline_or_old_device(tmp_path):
    server, transfers = world(tmp_path)

    async def play(port):
        tool = next(tool for tool in server.agent.offered_tools() if tool.name == "copy_to_device")
        errors = []
        for args in [{"from_device": "phone", "path": "/sdcard/a.jpg", "to_device": "pc"},
                     {"from_device": "pc", "path": "a", "to_device": "pc"}]:
            with pytest.raises(Exception) as failed:
                await tool.run(args)
            errors.append(str(failed.value))
        await Device.join(port, "phone", files=False)
        with pytest.raises(Exception) as failed:
            await tool.run({"from_device": "phone", "path": "/sdcard/a.jpg", "to_device": "pc"})
        return errors + [str(failed.value)]

    offline, same, old = run(serving(server, play))
    assert "phone is offline" in offline and "two different devices" in same and "too old" in old


def test_old_and_half_files_are_cleared_at_the_start(tmp_path):
    folder = tmp_path / "transfers"
    folder.mkdir()
    (folder / "pc-9.part").write_bytes(b"half")
    (folder / "pc-10.file").write_bytes(b"old")
    (folder / "pc-10.json").write_text(json.dumps({"id": "pc-10", "from": "pc", "to": "phone", "name": "a",
                                                   "size": 3, "at": time.time() - (files.KEEP_DAYS + 1) * 86400}))
    (folder / "pc-11.file").write_bytes(b"new")
    (folder / "pc-11.json").write_text(json.dumps({"id": "pc-11", "from": "pc", "to": "phone", "name": "b",
                                                   "size": 3, "at": time.time()}))
    (folder / "pc-12.file").write_bytes(b"no description")
    assert left(Transfers(folder, {})) == ["pc-11.file", "pc-11.json"]


@pytest.mark.parametrize("name, safe", [
    ("report.pdf", "report.pdf"), ("..\\..\\Windows\\evil.exe", "evil.exe"), ("/etc/passwd", "passwd"),
    ("a<b>c:d|e?.txt", "a_b_c_d_e_.txt"), ("CON.txt", "_CON.txt"), ("  .hidden. ", "hidden"), ("", "file"),
    ("x" * 300 + ".jpg", "x" * 146 + ".jpg"),
])
def test_safe_names(name, safe):
    assert safe_name(name) == safe


def test_sizes_read_well():
    assert [human_size(n) for n in (8, 2048, 5 * 2**20 + 2**19, 3 * 2**30)] == \
        ["8 bytes", "2.0 KB", "5.5 MB", "3.00 GB"]


def test_the_tool_is_offered_only_with_the_server(tmp_path):
    assert "copy_to_device" not in [tool.name for tool in Agent(client=FakeClient()).offered_tools()]
    server, _ = world(tmp_path)
    tool = next(tool for tool in server.agent.offered_tools() if tool.name == "copy_to_device")
    assert tool.personal and not tool.confirm and tool.source == "natro"
