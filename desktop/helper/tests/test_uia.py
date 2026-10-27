"""The screen reader's own rules: which window is his, which elements are numbered, keys and typing."""
import pytest

from natro_pc import uia
from natro_pc.control import Element
from natro_pc.tools import ToolError
from natro_pc.uia import Raw, Window, keep, parse_keys, pick, typing


def win(hwnd, title, pid=10, process="app.exe", cls="App", tool=False, minimized=False):
    return Window(hwnd, title, pid, process, cls, tool, minimized)


def test_natros_own_windows_are_never_the_one_in_front():
    windows = [
        win(1, "Natro chat", pid=50, process="natro.exe"),  # he just typed to her: hers is in front
        win(2, "", pid=20, process="msedge.exe", tool=True),  # Edge's open menu
        win(3, "Video - YouTube - Microsoft Edge", pid=20, process="msedge.exe", cls=uia.CHROMIUM_CLASS),
        win(4, "", pid=30, cls="Shell_TrayWnd"),
        win(5, "general - Discord", pid=40, process="discord.exe"),
        win(6, "Notes", pid=60, minimized=True),
        win(7, "Program Manager", pid=70, process="explorer.exe", cls="Progman"),
    ]
    front, popups, others = pick(windows, own_pids={99})
    assert front.title == "Video - YouTube - Microsoft Edge"
    assert [w.hwnd for w in popups] == [2]
    assert [w.title for w in others] == ["general - Discord", "Notes"]
    # The helper's own windows (its process) are left out too.
    front, _, _ = pick([win(8, "Natro", pid=99, process="python.exe")] + windows[2:], own_pids={99})
    assert front.hwnd == 3


def test_no_window_of_his():
    front, popups, others = pick([win(1, "Natro chat", process="natro.exe"), win(4, "", cls="Shell_TrayWnd")])
    assert (front, popups, others) == (None, [], [])


def raw(kind, name, rect=(0, 0, 100, 20), **extra):
    return Raw(kind=kind, name=name, rect=rect, window="App", hwnd=1, **extra)


def test_what_gets_a_number():
    elements, cut = keep([
        raw(50000, "Send", rect=(0, 0, 100, 30)),
        raw(50020, "Send", rect=(10, 5, 90, 25)),  # the button's own label: not twice
        raw(50020, "see you at 8"),  # a chat message
        raw(50020, ""),  # empty text
        raw(50026, "", focusable=True),  # an unnamed group
        raw(50026, "Add a comment…", focusable=True),  # a focusable placeholder
        raw(50033, "Main", rect=(0, 0, 500, 500)),  # a pane: layout only
        raw(50004, "Search", value="cats", focused=True),
        raw(50002, "Notifications", toggle=1),
        raw(50024, "Text channels", expand=0),
        raw(50000, "Save", enabled=False),
        raw(50000, "Hidden", rect=(10, 10, 10, 30)),  # no size
    ], Element)
    assert not cut
    assert [e.line(n) for n, e in enumerate(elements, 1)] == [
        "[1] button: Send",
        "[2] text: see you at 8",
        "[3] group: Add a comment…",
        '[4] field: Search (focused, value "cats")',
        "[5] check box: Notifications (on)",
        "[6] tree item: Text channels (collapsed)",
        "[7] button: Save (disabled)",
    ]
    assert elements[0].x == 50 and elements[0].y == 15


def test_a_long_screen_drops_text_before_buttons():
    raws = []
    for i in range(120):
        raws += [raw(50020, f"message {i}", rect=(0, i * 40, 100, i * 40 + 20)),
                 raw(50000, f"button {i}", rect=(200, i * 40, 300, i * 40 + 20))]
    elements, cut = keep(raws, Element)
    assert cut and len(elements) == uia.MAX_ELEMENTS
    assert sum(e.kind == "button" for e in elements) == 120  # every button kept
    assert [e.name for e in elements[:3]] == ["message 0", "button 0", "message 1"]  # still in screen order


def test_titles_lose_invisible_characters():
    assert uia.plain("Video - Microsoft​ Edge\n") == "Video - Microsoft Edge"


def test_keys():
    assert parse_keys("ctrl+shift+s") == [(0x11, False), (0x10, False), (ord("S"), False)]
    assert parse_keys("Page Down") == [(0x22, True)]
    assert parse_keys("alt+f4") == [(0x12, False), (0x73, False)]
    assert parse_keys("ctrl++") == [(0x11, False), (0xBB, False)]
    assert parse_keys("win+d") == [(0x5B, True), (ord("D"), False)]
    with pytest.raises(ToolError, match="Unknown key 'hyper'"):
        parse_keys("hyper+s")
    assert uia.key_events([(0x11, False), (0x53, False)]) == [
        (0x11, False, False), (0x53, False, False), (0x53, False, True), (0x11, False, True)]


def test_typing_sends_characters_and_a_line_break_as_shift_enter():
    assert typing("hi\nyo") == [
        ("char", ord("h")), ("char", ord("i")),
        ("key", 0x10, False, False), ("key", 0x0D, False, False), ("key", 0x0D, False, True),
        ("key", 0x10, False, True),
        ("char", ord("y")), ("char", ord("o")),
    ]
    assert typing("გ😀") == [("char", 0x10D2), ("char", 0xD83D), ("char", 0xDE00)]  # Georgian, and a surrogate pair
