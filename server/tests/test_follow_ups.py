"""Listening after a reply: which replies say "listen", and a follow-up with nothing in it."""
import json
from types import SimpleNamespace

from conftest import FakeClient, message, run, text
from natro_agent.agent import Agent
from natro_agent.follow_ups import MOST_IN_A_ROW, MOST_SECONDS, SECONDS, FollowUps, seconds_to_read, starts_sound
from natro_agent.server import Server
from test_server import HELLO, FakeVoice, session, until_reply


def reply(words="OK.", model="claude", tools=()):
    return SimpleNamespace(text=words, model=model, tool_calls=list(tools))


def called(tool, ok=True, **args):
    return {"tool": tool, "args": args, "ok": ok, "result": ""}


def test_a_long_reply_gets_longer_to_be_read():
    assert seconds_to_read("") == SECONDS
    assert seconds_to_read("x" * 120) == SECONDS
    assert seconds_to_read("x" * 160) == 8
    assert seconds_to_read("x" * 5000) == MOST_SECONDS


def test_what_starts_sound_isnt_followed():
    assert starts_sound(called("spotify_play", query="Yellow"))
    assert starts_sound(called("spotify_control", action="resume"))
    assert starts_sound(called("phone_media", action="next"))
    assert not starts_sound(called("spotify_control", action="pause"))
    assert not starts_sound(called("phone_media", action="pause"))
    assert not starts_sound(called("spotify_play", ok=False))  # nothing plays
    assert not starts_sound(called("pc_open_app", app="Spotify"))
    assert not starts_sound(called("spotify_like"))

    follow_ups = FollowUps()
    assert follow_ups.replied("pc", False, reply(tools=[called("spotify_play")])) is None
    assert follow_ups.replied("pc", False, reply(tools=[called("spotify_control", action="pause")])) == SECONDS


def test_replies_that_didnt_answer_him_arent_followed():
    follow_ups = FollowUps()
    assert follow_ups.replied("pc", False, reply("I didn't catch that.", "none (noise)")) is None
    assert follow_ups.replied("pc", False, reply("OK, I won't use Claude for that.", "none")) is None
    assert follow_ups.replied("pc", False, reply("Opening Spotify.", "none (quick)")) == SECONDS


def test_it_stops_after_three_in_a_row_and_a_new_request_starts_again():
    follow_ups = FollowUps()
    assert follow_ups.replied("pc", False, reply()) == SECONDS  # what he asked with the key
    for _ in range(MOST_IN_A_ROW - 1):
        assert follow_ups.replied("pc", True, reply()) == SECONDS
    assert follow_ups.replied("pc", True, reply()) is None  # the third
    assert follow_ups.replied("phone", True, reply()) == SECONDS, "each device has its own"
    assert follow_ups.replied("pc", False, reply()) == SECONDS
    assert follow_ups.replied("pc", True, reply()) == SECONDS


def serve_requests(voice, *replies, over_budget=False):
    agent = Agent(client=FakeClient(*[message(text(words)) for words in replies]))
    if over_budget:
        agent.ledger.add(agent.ledger.budget + 1)
    return Server(agent, voice=voice, token="secret")


async def said(ws, id, **fields):
    await ws.send(json.dumps({"type": "audio", "id": id, **fields}))
    await ws.send(b"\x01\x00" * 1600)


def test_a_reply_to_something_he_said_says_listen():
    server = serve_requests(FakeVoice(("ქრომი", "Open Chrome.", 0.002)), "I can't yet.")

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await said(ws, "r1")
        return await until_reply(ws)

    assert run(session(server, device))[-1]["listen"] == SECONDS


def test_a_typed_request_doesnt_say_listen():
    server = serve_requests(None, "Hi.")

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await ws.send(json.dumps({"type": "text", "id": "r1", "text": "hello there"}))
        return await until_reply(ws)

    last = run(session(server, device))[-1]
    assert last["text"] == "Hi." and "listen" not in last


def test_nothing_recognized_first_time_says_so_and_isnt_followed():
    server = serve_requests(FakeVoice(("", "", 0.001)))

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await said(ws, "r1")
        return await until_reply(ws)

    last = run(session(server, device))[-1]
    assert last["text"] == "I didn't catch that." and "listen" not in last


def test_not_with_the_budget_gone():
    server = serve_requests(FakeVoice(("ქრომი", "Open Chrome.", 0.002)), over_budget=True)

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await said(ws, "r1")
        return await until_reply(ws, on_confirm=lambda question: ws.send(
            json.dumps({"type": "answer", "id": question["id"], "yes": False})))

    assert "listen" not in run(session(server, device))[-1]


async def until_nothing_heard(ws):
    seen = []
    while not seen or seen[-1]["type"] not in ("nothing_heard", "reply", "error"):
        seen.append(json.loads(await ws.recv()))
    return seen


def test_a_follow_up_with_no_words_ends_quietly():
    # Noise the device took for speech: the recognizer found nothing, or nothing she can use.
    for english in ("", "...", "a"):
        voice = FakeVoice(("", english, 0.001))
        server = serve_requests(voice)
        spent = server.agent.ledger.spent

        async def device(ws, port):
            await ws.send(json.dumps(HELLO))
            await ws.recv()
            await said(ws, "r1", follow_up=True)
            return await until_nothing_heard(ws)

        last = run(session(server, device))[-1]
        assert last["type"] == "nothing_heard" and last["id"] == "r1" and "text" not in last
        assert not server.chat.entries, "nothing in the chat"
        assert abs(server.agent.ledger.spent - spent - 0.001) < 1e-9, "what recognizing it cost still counts"


def test_a_follow_up_with_words_is_a_request_like_any_other():
    voice = FakeVoice(("ქრომი", "Open it too.", 0.002))
    server = serve_requests(voice, "Done.")

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await said(ws, "r1", follow_up=True)
        return await until_reply(ws)

    seen = run(session(server, device))
    assert seen[-1]["type"] == "reply" and seen[-1]["text"] == "Done." and seen[-1]["listen"] == SECONDS
    assert [entry["asked"] for entry in server.chat.entries.values()] == ["Open it too."]


def test_the_third_follow_up_in_a_row_gets_no_more():
    voice = FakeVoice(*[("ქრომი", "And another thing.", 0.001)] * (MOST_IN_A_ROW + 1))
    server = serve_requests(voice, *["OK."] * (MOST_IN_A_ROW + 1))

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        replies = []
        await said(ws, "r0")
        replies.append((await until_reply(ws))[-1])
        for number in range(1, MOST_IN_A_ROW + 1):
            await said(ws, f"r{number}", follow_up=True)
            replies.append((await until_reply(ws))[-1])
        return replies

    assert [last.get("listen") for last in run(session(server, device))] == [SECONDS, SECONDS, SECONDS, None]
