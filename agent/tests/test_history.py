"""Longer memory: his earlier requests and her replies, found in the log."""
import json
from datetime import datetime, timedelta

import pytest

from conftest import run
from natro_agent.config import timezone
from natro_agent.history import MOST, History
from natro_agent.tools import ToolError


@pytest.fixture(autouse=True)
def tbilisi(monkeypatch):
    monkeypatch.setenv("NATRO_TIMEZONE", "Asia/Tbilisi")


# A Sunday evening where he is.
NOW = datetime(2026, 10, 18, 20, 0, tzinfo=timezone())


def logged(folder, entries):
    """Writes log lines as the server does: its own clock, no zone, in a file named by its day."""
    for at, fields in entries:
        server_time = at.astimezone().replace(tzinfo=None)
        path = folder / f"agent-{server_time:%Y-%m-%d}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": server_time.isoformat(timespec="seconds"), **fields}) + "\n")


def request(device, text, reply, **more):
    return {"kind": "request", "device": device, "text": text, "reply": reply, **more}


def make(tmp_path, entries):
    logged(tmp_path, entries)
    history = History(tmp_path)
    history.now = lambda: NOW
    return history


def search(history, **args):
    return run(history.search(args))


TUESDAY = datetime(2026, 10, 13, 13, 5, tzinfo=timezone())
YESTERDAY = datetime(2026, 10, 17, 9, 30, tzinfo=timezone())


def a_week(tmp_path):
    return make(tmp_path, [
        (TUESDAY, request("phone", "find a good Georgian restaurant near Vake", "Try Barbarestan on Kostava.")),
        (TUESDAY + timedelta(hours=1), {"kind": "tool", "device": "phone", "tool": "web_search"}),
        (YESTERDAY, request("pc", "what time is my flight to Berlin", "It leaves at 6:40 tomorrow morning.")),
        (YESTERDAY + timedelta(minutes=5), request("job", "Compare restaurants in Berlin", "Kin-Za is the best.")),
        (YESTERDAY + timedelta(minutes=9), request("test", "restaurant test", "test reply")),  # a test device
        (YESTERDAY + timedelta(minutes=10), request("chat", "restaurant typed", "typed reply")),  # the typed chat
        (YESTERDAY + timedelta(minutes=11), request("pc", "restaurant", "", cancelled=True)),
    ])


def test_words_newest_first_in_what_he_asked_or_what_she_said(tmp_path):
    history = a_week(tmp_path)
    found = search(history, words="restaurants")
    assert found.splitlines() == [
        "- Sat 17 Oct 2026, 09:35, a background job: he asked \"Compare restaurants in Berlin\"; you said "
        "\"Kin-Za is the best.\"",
        "- Tue 13 Oct 2026, 13:05, on his phone: he asked \"find a good Georgian restaurant near Vake\"; you said "
        "\"Try Barbarestan on Kostava.\""]
    assert "Barbarestan" in search(history, words="Barbarestan Kostava")  # in her reply
    assert search(history, words="restaurant Vake", start="2026-10-14").startswith("Nothing with \"restaurant vake\"")


def test_a_day_without_words(tmp_path):
    history = a_week(tmp_path)
    found = search(history, start="2026-10-17")
    assert "flight to Berlin" in found and "Compare restaurants" in found and "Vake" not in found
    assert "on his PC" in found
    assert search(history, start="2026-10-15", end="2026-10-16") == "Nothing from Thu 15 Oct 2026 to Fri 16 Oct 2026."


def test_his_days_not_the_servers(tmp_path):
    # 01:30 on Sunday where he is is still Saturday on a server in UTC.
    late = datetime(2026, 10, 18, 1, 30, tzinfo=timezone())
    history = make(tmp_path, [(late, request("phone", "set an alarm", "Done."))])
    assert "Sun 18 Oct 2026, 01:30" in search(history, start="2026-10-18")
    assert search(history, start="2026-10-17").startswith("Nothing")


def test_too_many_say_so(tmp_path):
    history = make(tmp_path, [(NOW - timedelta(minutes=i), request("pc", f"play song {i}", "Playing."))
                              for i in range(MOST + 3)])
    found = search(history, words="play")
    assert len(found.splitlines()) == MOST + 1 and found.endswith("(and 3 more: narrow it with more words or fewer days)")


def test_what_it_needs(tmp_path):
    history = a_week(tmp_path)
    with pytest.raises(ToolError, match="words to look for, or the days"):
        search(history)
    with pytest.raises(ToolError, match="Can't read the day"):
        search(history, start="last Tuesday")


def test_the_tool_is_personal(tmp_path):
    tool, = History(tmp_path).tools()
    assert tool.name == "history_search" and tool.personal and not tool.confirm
