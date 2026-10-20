"""Using apps by their screen, against a stand-in for the desktop that UI Automation reads."""
import base64
import dataclasses
import re
from pathlib import Path

import pytest

from natro_pc import control
from natro_pc.control import RISKY, Element, Screen, ScreenControl
from natro_pc.tools import ToolError, result_fields

SPEECH_KT = Path(__file__).resolve().parents[2] / "android/app/src/main/java/dev/natro/Speech.kt"
JPEG = bytes([0xFF, 0xD8]) + b"jpeg"


def discord():
    window = "general - Discord"
    return Screen(window, [
        Element(200, 300, "list item", "general", window, hwnd=7),
        Element(220, 330, "list item", "Delete channel", window, hwnd=7),
        Element(640, 760, "field", "Message #general", window, focused=True, value="see you at 8", hwnd=7),
        Element(900, 100, "field", "Password", window, password=True, hwnd=7),
        Element(1000, 120, "check box", "Notifications", window, toggle="on", hwnd=7),
        Element(1200, 760, "button", "Send", window, hwnd=7),
        Element(700, 400, "document", "Messages", window, scrolls=True, hwnd=7),
    ], ["Google Chrome", "Spotify"], hwnd=7)


def notepad():
    return Screen("Untitled - Notepad", [Element(400, 400, "document", "Text editor", "Untitled - Notepad", hwnd=9),
                                         Element(30, 40, "menu item", "File", "Untitled - Notepad", hwnd=9)],
                  hwnd=9)


class FakeDesktop:
    """Reads give the screens in turn (the last one again once they run out); calls lists what was done."""

    def __init__(self, *screens):
        self.screens = list(screens)
        self.last = None
        self.calls = []
        self.gone = set()  # names of elements that have gone since they were read
        self.moved = {}  # name -> (x, y) where it is now

    def read(self, everything=False):
        self.calls.append(("read", everything))
        if self.screens:
            self.last = self.screens.pop(0)
        return self.last

    def locate(self, element):
        if element.name in self.gone:
            return None
        if element.name in self.moved:
            x, y = self.moved[element.name]
            return dataclasses.replace(element, x=x, y=y)
        return element

    def click(self, element, button="left", clicks=1):
        self.calls.append(("click", element.name, (element.x, element.y), button, clicks))

    def type(self, element, text, clear=True, enter=False, window=0):
        self.calls.append(("type", element.name if element else None, text, clear, enter, window))

    def key(self, keys, window=0):
        self.calls.append(("key", keys, window))

    def scroll(self, element, direction="down", window=0):
        self.calls.append(("scroll", element.name if element else None, direction, window))

    def switch(self, name):
        self.calls.append(("switch", name))
        return name

    def wait_for(self, text, seconds=10):
        self.calls.append(("wait_for", text, seconds))
        return True, self.read()

    def screenshot(self):
        self.calls.append(("screenshot",))
        return JPEG

    def actions(self):
        return [call for call in self.calls if call[0] != "read"]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(control, "SETTLE_SECONDS", 0)


def make(*screens):
    fake = FakeDesktop(*screens)
    return ScreenControl(fake), fake


def test_the_screen_as_she_reads_it():
    pc, _ = make(discord())
    assert pc.screen() == "\n".join([
        "Window in front: general - Discord",
        "[1] list item: general",
        "[2] list item: Delete channel",
        '[3] field: Message #general (focused, value "see you at 8")',
        "[4] field: Password (password)",
        "[5] check box: Notifications (on)",
        "[6] button: Send",
        "[7] document: Messages (scrolls)",
        "Other windows: Google Chrome, Spotify.",
    ])


def test_an_empty_screen_says_why():
    pc, _ = make(Screen("Task Manager", [], note="this window runs as administrator, and Natro can't use those"))
    assert pc.screen() == ("Window in front: Task Manager\n"
                           "(this window runs as administrator, and Natro can't use those)")
    pc, _ = make(Screen(None, []))
    assert pc.screen().startswith("Window in front: none\n(no elements Natro can use")


def test_screen_find_and_act():
    pc, fake = make(discord(), discord(), notepad())
    assert pc.screen().startswith("Window in front: general - Discord\n[1] list item: general")
    found = pc.find("message")
    assert found.splitlines()[1:] == ['[3] field: Message #general (focused, value "see you at 8")',
                                      "[7] document: Messages (scrolls)", "Other windows: Google Chrome, Spotify."]
    result = pc.act([{"do": "click", "element": 1}, {"do": "type", "element": 3, "text": "on my way"},
                     {"do": "scroll", "element": 7, "direction": "up"}, {"do": "key", "text": "ctrl+a"}])
    assert result.startswith("Done. Window in front: Untitled - Notepad")
    assert fake.actions() == [
        ("click", "general", (200, 300), "left", 1),
        ("type", "Message #general", "on my way", True, False, 7),
        ("scroll", "Messages", "up", 7),
        ("key", "ctrl+a", 7),
    ]


def test_typing_where_the_cursor_is_and_scrolling_the_window():
    pc, fake = make(notepad())
    pc.screen()
    pc.act([{"do": "type", "text": "hello"}, {"do": "scroll"}])
    assert fake.actions() == [("type", None, "hello", True, False, 9), ("scroll", None, "down", 9)]


def test_sending_buying_and_deleting_need_act_risky():
    pc, fake = make(discord())
    pc.screen()
    for actions in ([{"do": "click", "element": 6}],  # Send
                    [{"do": "click", "element": 2}],  # Delete channel
                    [{"do": "type", "element": 3, "text": "hi", "enter": True}],  # Enter in a chat sends
                    [{"do": "type", "text": "hi", "enter": True}],  # typed where the cursor is, too
                    [{"do": "key", "text": "enter"}],
                    [{"do": "key", "text": "shift+delete"}],
                    # Nothing is done when any action in the list is refused.
                    [{"do": "click", "element": 1}, {"do": "click", "element": 6}]):
        with pytest.raises(ToolError, match="act_risky"):
            pc.act(actions)
    assert fake.actions() == []
    pc.act([{"do": "type", "element": 3, "text": "hi", "enter": True}], risky=True)
    assert fake.actions() == [("type", "Message #general", "hi", True, True, 7)]


def test_a_comment_box_counts_as_sending():
    window = "Video - YouTube - Microsoft Edge"
    page = Screen(window, [Element(300, 500, "field", "Add a comment…", window, hwnd=3),
                           Element(600, 560, "button", "Comment", window, hwnd=3)], hwnd=3)
    pc, fake = make(page)
    pc.screen()
    with pytest.raises(ToolError, match="act_risky"):
        pc.act([{"do": "type", "element": 1, "text": "hello", "enter": True}])
    pc.act([{"do": "type", "element": 1, "text": "hello"}])  # typing alone is fine; posting asks
    assert fake.actions() == [("type", "Add a comment…", "hello", True, False, 3)]


def test_passwords_are_never_typed_even_after_a_yes():
    pc, fake = make(discord())
    pc.screen()
    with pytest.raises(ToolError, match="password"):
        pc.act([{"do": "type", "element": 4, "text": "hunter2"}], risky=True)
    assert fake.actions() == []


def test_unknown_keys_are_refused_before_anything_is_done():
    pc, fake = make(notepad())
    pc.screen()
    with pytest.raises(ToolError, match="Unknown key 'hyper'"):
        pc.act([{"do": "click", "element": 2}, {"do": "key", "text": "hyper+s"}])
    assert fake.actions() == []


def test_elements_are_found_where_they_are_now():
    pc, fake = make(discord())
    pc.screen()
    fake.moved["general"] = (200, 500)
    pc.act([{"do": "click", "element": 1}])  # it moved: clicked where it is now, without reading the screen again
    assert fake.actions()[-1] == ("click", "general", (200, 500), "left", 1)
    assert [call for call in fake.calls if call[0] == "read"] == [("read", False), ("read", False)]


def test_a_gone_element_is_looked_for_again_or_refused():
    moved_screen = discord()
    moved_screen.elements[0] = dataclasses.replace(moved_screen.elements[0], y=520)
    without = discord()
    del without.elements[0]
    pc, fake = make(discord(), moved_screen, moved_screen, discord(), without)
    pc.screen()
    fake.gone.add("general")  # its handle no longer works (the list was rebuilt), but it is still on screen
    pc.act([{"do": "click", "element": 1}])
    assert fake.actions()[-1] == ("click", "general", (200, 520), "left", 1)
    pc.screen()
    with pytest.raises(ToolError, match=r"The screen changed: \[1\] \(list item general\) isn't there"):
        pc.act([{"do": "click", "element": 1}])
    assert len(fake.actions()) == 1


def test_numbers_need_a_screen_first_and_must_exist():
    pc, fake = make(discord())
    with pytest.raises(ToolError, match="Read the screen first"):
        pc.act([{"do": "click", "element": 1}])
    pc.screen()
    with pytest.raises(ToolError, match=r"no element \[42\]"):
        pc.act([{"do": "click", "element": 42}])
    pc.act([{"do": "click", "element": "1"}])  # a number written as text works too
    assert fake.actions() == [("click", "general", (200, 300), "left", 1)]
    with pytest.raises(ToolError, match="Unknown action"):
        pc.act([{"do": "drag", "element": 1}])


def test_switching_windows_and_waiting():
    pc, fake = make(notepad(), discord())
    assert pc.switch_window("Notepad").startswith("Window in front: Untitled - Notepad")
    assert pc.wait_for("Messages", seconds=99).startswith("'Messages' is on the screen now.\nWindow in front: "
                                                          "general - Discord")
    assert fake.actions() == [("switch", "Notepad"), ("wait_for", "Messages", 20)]


def test_a_screenshot_is_a_picture_for_natro_and_the_chat():
    pc, fake = make()
    shot = pc.screenshot()
    fields = result_fields(shot)
    assert fields["result"].startswith("The screenshot of his PC's screen is here, and in the chat")
    assert fields["picture"]["mime"] == "image/jpeg" and base64.b64decode(fields["picture"]["data"]) == JPEG
    assert result_fields("Opened Spotify.") == {"result": "Opened Spotify."}


def test_the_risky_words_are_the_phones():
    kotlin = SPEECH_KT.read_text(encoding="utf-8")
    pattern = re.search(r"val RISKY = Regex\(\s*(.*?),\s*RegexOption", kotlin, re.DOTALL).group(1)
    phone = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', pattern)).replace("\\\\", "\\")
    assert phone == RISKY.pattern


def test_the_tools_as_the_brain_sees_them():
    tools = {tool.name: tool for tool in control.tools(ScreenControl(FakeDesktop()))}
    assert set(tools) == {"screen", "find", "act", "act_risky", "switch_window", "wait_for", "screenshot"}
    assert all(tool.personal for tool in tools.values())
    assert {name for name, tool in tools.items() if tool.confirm} == {"act_risky"}
