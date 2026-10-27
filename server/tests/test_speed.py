"""Short commands: run without a model, or with one model call instead of two."""
import pytest

from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent import quick
from natro_agent.agent import Agent
from natro_agent.notes import Notes
from natro_agent.tools import Tool, ToolError


def spotify(fails=False):
    """A stand-in for spotify_control that records its calls."""
    calls = []

    async def control(args):
        calls.append(args)
        if fails:
            raise ToolError("Spotify isn't playing on any device right now.")
        return {"pause": "Paused.", "next": "Skipped to the next track."}.get(args["action"], "Volume 60%.")

    tool = Tool(name="spotify_control", description="Control Spotify.", run=control, source="spotify", final=True,
                input_schema={"type": "object", "properties": {"action": {"type": "string"}}})
    return tool, calls


@pytest.mark.parametrize("said, expected", [
    ("Pause the music.", ("spotify_control", {"action": "pause"})),
    ("Okay, Natro. Pause it, please.", ("spotify_control", {"action": "pause"})),
    ("Skip this song.", ("spotify_control", {"action": "next"})),
    ("next song", ("spotify_control", {"action": "next"})),
    ("Go back to the previous song.", ("spotify_control", {"action": "previous"})),
    ("Turn the volume up a bit.", ("spotify_control", {"action": "volume", "value": "up"})),
    ("Quieter", ("spotify_control", {"action": "volume", "value": "down"})),
    ("Resume the music", ("spotify_control", {"action": "resume"})),
    ("next", ("spotify_control", {"action": "next"})),
    ("pause the video on my phone", None),  # not Spotify's to answer
    ("play Yellow by Coldplay", None),
    ("stop", None),  # stop talking? stop the timer? a model decides
])
def test_quick_commands(said, expected):
    assert quick.command(said, in_conversation=False) == expected


LOCK = ("pc_run_command", {"command": "rundll32.exe user32.dll,LockWorkStation"})
DARK = ("pc_run_command", {"command": quick._theme(light=False)})
LIGHT = ("pc_run_command", {"command": quick._theme(light=True)})


@pytest.mark.parametrize("said, from_pc, expected", [
    ("Lock the PC.", False, LOCK),
    ("OK Natro, lock my computer please", False, LOCK),
    ("Turn on dark mode.", True, DARK),
    ("Dark mode on my PC.", False, DARK),
    ("dark mode on", False, None),  # said on the phone, it may mean the phone
    ("Turn off dark mode.", True, LIGHT),
    ("Switch to light mode", True, LIGHT),
    ("Open Downloads.", True, ("pc_run_command", {"command": "Start-Process shell:Downloads"})),
    ("open my documents folder", True, ("pc_run_command", {"command": "Start-Process shell:Personal"})),
    ("Open Bluetooth settings.", True, ("pc_run_command", {"command": "Start-Process ms-settings:bluetooth"})),
    ("Open Wi-Fi settings on my PC.", False, ("pc_run_command", {"command": "Start-Process ms-settings:network-wifi"})),
    ("open settings", True, ("pc_run_command", {"command": "Start-Process ms-settings:"})),
    ("open the invoice in downloads", True, None),
    ("lock", True, None),
])
def test_quick_pc_commands(said, from_pc, expected):
    # The lines themselves are checked against the PC's harmless list in desktop/helper/tests/test_commands.py.
    assert quick.command(said, in_conversation=False, from_pc=from_pc) == expected


def test_bare_words_only_outside_a_conversation():
    # "Next" while she reads his emails means the next email.
    assert quick.command("Next.", in_conversation=True) is None
    assert quick.command("Next song.", in_conversation=True) == ("spotify_control", {"action": "next"})


@pytest.mark.parametrize("said, one_thing", [
    ("Okay, Natro. Play Yellow by Coldplay.", True),
    ("Open Spotify and play Kendrick's Fear.", False),
    ("Turn the volume up, I can't hear it.", False),
    ("Claude, open it.", True),  # a word and a comma before it
    ("Okay, Natro, pause it.", True),
    # His own words to note, add, remind him of or say may have "and"s and commas, and be long.
    ("Remind me that I need to change the FK relay in about three days.", True),
    ("Make a note that we need to change the Natro design. We should set it up as if Natro is on the website, and it "
     "should be interactive, not just a plain showcase.", True),
    ("add natro feature list task: when I press yes manually in chat it sends no. and I need to fix that", True),
    ("add natro feature to design reply box well when messages come from phone", True),
    ("Say on PC loud, who came in here?", True),
    ('say "who came in here? show yourself, then leave!"', True),
    ("[He asked for this on his other device while this PC was offline, and has just said to go ahead.] open "
     "Calculator", True),
    # ... but not another thing to do.
    ("Remind me to call mom, then open Spotify.", False),
    ("Make a note that the jam is gone and play some music.", False),
    ("add a task: buy milk, also set an alarm for 7", False),
    ('say "hi" and open Spotify', False),
    ("set an alarm for 6:30 and play music", False),  # a time's colon isn't where his words start
])
def test_simple_requests(said, one_thing):
    assert quick.simple(said) == one_thing


def test_quick_command_needs_no_model():
    tool, calls = spotify()
    claude = FakeClient()  # no replies: any model call would fail the test
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("Pause the music.", Owner()))
    assert reply.text == "Paused." and reply.model == "none (quick)" and reply.cost == 0
    assert calls == [{"action": "pause"}] and claude.requests == []


def test_quick_command_that_fails_goes_to_the_model():
    tool, calls = spotify(fails=True)
    claude = FakeClient(message(text("Nothing is playing on Spotify; I paused the phone instead.")))
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("Pause the music.", Owner()))
    assert calls == [{"action": "pause"}] and len(claude.requests) == 1
    assert reply.text.startswith("Nothing is playing")


def test_simple_request_ends_with_a_final_tools_result():
    tool, calls = spotify()
    claude = FakeClient(message(tool_use("t1", "spotify_control", action="next"), stop="tool_use"))
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("play the next one", Owner()))  # not a quick phrase, but simple
    assert reply.text == "Skipped to the next track." and len(claude.requests) == 1
    assert agent.session.messages[-1] == {"role": "assistant", "content": "Skipped to the next track."}


def test_a_note_ends_with_the_notes_own_words(tmp_path):
    notes = {tool.name: tool for tool in Notes(tmp_path).tools()}
    assert sorted(name for name, tool in notes.items() if tool.final) == ["notes_edit", "notes_write"]
    claude = FakeClient(message(tool_use("t1", "notes_write", title="Natro design", mode="create",
                                         text="Interactive, like Natro on the website, and different."),
                                stop="tool_use"))
    agent = Agent(list(notes.values()), client=claude)
    reply = run(agent.respond("Make a note that the Natro design should be interactive, and different.", Owner()))
    assert reply.text == "Created the note 'Natro design'." and len(claude.requests) == 1


def test_several_things_get_the_second_model_call():
    tool, calls = spotify()
    claude = FakeClient(message(tool_use("t1", "spotify_control", action="next"), stop="tool_use"),
                        message(text("Skipped, and it's Fix You now.")))
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("play the one after this and tell me what it is", Owner()))
    assert len(claude.requests) == 2 and reply.text == "Skipped, and it's Fix You now."


def test_a_quick_command_in_a_conversation_is_kept_as_the_call_it_was():
    # Her "Paused." alone, with no call before it, read to Claude as a claim she never acted on (2026-10-20).
    tool, calls = spotify()
    claude = FakeClient(message(text("It's Yellow by Coldplay.")), message(text("Sure.")))
    agent = Agent([tool], client=claude)
    run(agent.respond("what's playing", Owner()))
    run(agent.respond("Pause the music.", Owner()))
    run(agent.respond("thanks", Owner()))
    asked, called, result, said = claude.requests[1]["messages"][2:6]
    assert asked["role"] == "user" and asked["content"].endswith("Pause the music.")
    use, = called["content"]
    assert use["type"] == "tool_use" and use["name"] == "spotify_control" and use["input"] == {"action": "pause"}
    assert result["content"] == [{"type": "tool_result", "tool_use_id": use["id"], "content": "Paused."}]
    assert said == {"role": "assistant", "content": "Paused."} and calls == [{"action": "pause"}]
