"""The phone's app, updated over the air: who's told, and who may fetch it."""
import asyncio
import json
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from conftest import FakeClient, run
from natro_agent import app_updates
from natro_agent.agent import Agent
from natro_agent.app_updates import AppUpdates
from natro_agent.server import Server

BUILD = b"PK a build" * 1000
LATEST = {"version": 300, "size": len(BUILD), "sha256": "abc"}


class Phone:
    name = "phone"

    def __init__(self, version=299):
        self.version = version
        self.posted = []

    def post(self, **message):
        self.posted.append(message)


def published(tmp_path, latest=LATEST):
    (tmp_path / "natro.apk").write_bytes(BUILD)
    (tmp_path / "app.json").write_text(json.dumps(latest), encoding="utf-8")


def test_an_older_phone_is_told_of_a_new_build(tmp_path, log_dir):
    published(tmp_path)
    phone = Phone()
    AppUpdates(tmp_path, {}).connected(phone)
    assert phone.posted == [{"type": "app_update", "path": "/app/natro.apk", **LATEST}]


def test_he_can_ask_and_hears_either_way(tmp_path, log_dir):
    published(tmp_path)
    updates = AppUpdates(tmp_path, {})
    assert list(updates.messages()) == ["app_update_check"]
    older, newest = Phone(299), Phone(300)
    updates.check(older, {})
    assert older.posted == [{"type": "app_update", "path": "/app/natro.apk", **LATEST}]
    updates.check(newest, {})
    assert newest.posted == [{"type": "app_current", "version": 300}]


@pytest.mark.parametrize("version", [300, 301, None, "299", True])
def test_one_thats_up_to_date_or_says_nothing_isnt(tmp_path, version):
    published(tmp_path)
    phone = Phone(version)
    AppUpdates(tmp_path, {}).connected(phone)
    assert phone.posted == []


def test_with_nothing_published_nobody_is_told(tmp_path):
    phone = Phone()
    AppUpdates(tmp_path, {}).connected(phone)
    assert phone.posted == []
    (tmp_path / "app.json").write_text("not json", encoding="utf-8")
    AppUpdates(tmp_path, {}).connected(phone)
    (tmp_path / "app.json").write_text(json.dumps({"version": 5}), encoding="utf-8")
    AppUpdates(tmp_path, {}).connected(phone)
    assert phone.posted == []


def test_a_new_build_is_told_to_the_phones_once(tmp_path, log_dir, monkeypatch):
    monkeypatch.setattr(app_updates, "CHECK_SECONDS", 0.01)
    phone, other = Phone(), Phone(300)
    updates = AppUpdates(tmp_path, {"phone": phone, "pc": other})

    async def play():
        running = asyncio.create_task(updates.run())
        await asyncio.sleep(0.05)
        assert phone.posted == []  # nothing published yet
        published(tmp_path)
        await asyncio.sleep(0.05)
        running.cancel()

    run(play())
    assert len(phone.posted) == 1 and other.posted == []


async def fetch(port, path="/app/natro.apk", token=None):
    headers = {"X-Natro-Token": token} if token else {}

    def go():
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)) as got:
                return got.status, got.read(), got.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers
    return await asyncio.to_thread(go)


async def serving(updates, play):
    server = Server(Agent(client=FakeClient()), token="secret")
    async with serve(server.handle, "127.0.0.1", 0, process_request=lambda c, r: updates.http(c, r)) as listener:
        return await play(listener.sockets[0].getsockname()[1])


def test_the_build_comes_only_with_the_token(tmp_path, log_dir):
    published(tmp_path)
    updates = AppUpdates(tmp_path, {}, token="secret")

    async def play(port):
        return await fetch(port, token="secret"), await fetch(port, token="wrong"), await fetch(port)

    good, wrong, none = run(serving(updates, play))
    assert good[0] == 200 and good[1] == BUILD
    assert good[2]["Content-Type"] == "application/vnd.android.package-archive"
    assert int(good[2]["Content-Length"]) == len(BUILD)
    assert wrong[0] == 403 and none[0] == 403 and BUILD not in wrong[1]


async def piece(port, asked, token="secret"):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/app/natro.apk",
                                     headers={"X-Natro-Token": token, "Range": asked})

    def go():
        try:
            with urllib.request.urlopen(request) as got:
                return got.status, got.read(), got.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers
    return await asyncio.to_thread(go)


def test_it_comes_piece_by_piece_whatever_is_asked(tmp_path, log_dir, monkeypatch):
    monkeypatch.setattr(app_updates, "MOST_PIECE_BYTES", 4000)
    published(tmp_path)
    updates = AppUpdates(tmp_path, {}, token="secret")

    async def play(port):
        return (await piece(port, "bytes=0-999"), await piece(port, "bytes=0-"), await piece(port, "bytes=9000-99999"),
                await piece(port, f"bytes={len(BUILD)}-"), await piece(port, "bytes=0-99", token="wrong"))

    some, capped, last, past, wrong = run(serving(updates, play))
    assert some[0] == 206 and some[1] == BUILD[:1000] and some[2]["Content-Range"] == f"bytes 0-999/{len(BUILD)}"
    assert capped[1] == BUILD[:4000] and capped[2]["Content-Range"] == f"bytes 0-3999/{len(BUILD)}"
    assert last[1] == BUILD[9000:] and last[2]["Content-Range"] == f"bytes 9000-9999/{len(BUILD)}"
    assert past[0] == 416 and wrong[0] == 403


def test_the_pieces_make_the_build(tmp_path, log_dir, monkeypatch):
    monkeypatch.setattr(app_updates, "MOST_PIECE_BYTES", 3000)
    published(tmp_path)
    updates = AppUpdates(tmp_path, {}, token="secret")

    async def play(port):
        got = b""
        while len(got) < len(BUILD):
            got += (await piece(port, f"bytes={len(got)}-"))[1]
        return got

    assert run(serving(updates, play)) == BUILD


def test_with_no_build_it_says_so_and_other_addresses_arent_its(tmp_path):
    updates = AppUpdates(tmp_path, {}, token="secret")

    async def play(port):
        missing = await fetch(port, token="secret")
        async with connect(f"ws://127.0.0.1:{port}") as ws:  # the devices' WebSocket is as it was
            await ws.send(json.dumps({"type": "hello", "device": "pc", "token": "secret"}))
            return missing, json.loads(await ws.recv())["type"]

    missing, welcome = run(serving(updates, play))
    assert missing[0] == 404 and welcome == "welcome"


def test_the_server_gives_a_device_its_version(tmp_path):
    from natro_agent.devices import RemoteDevice

    class Connection:
        pass
    assert RemoteDevice(Connection(), {"device": "phone", "version": 300}).version == 300
    assert RemoteDevice(Connection(), {"device": "pc"}).version is None
