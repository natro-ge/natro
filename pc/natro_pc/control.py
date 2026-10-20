"""Using apps on the PC by their screen: read it, click, type, scroll, press keys.

Like the phone's screen control: screen lists the elements of the window in front, numbered ("[4] button:
Send"); act clicks, types, scrolls or presses keys by number, several in one call, and returns the new screen.
A click or key that looks like sending, buying, paying or deleting is refused there and goes through
act_risky, which the brain asks him about first. Passwords are never typed. Everything on screen is personal:
only Claude sees it.

The screen is read through Windows UI Automation (uia.py): the window he was using, never Natro's own, in one
pass of about 0.2 s. Before acting, each element is found where it is now (a few milliseconds), so a click
never lands on something that has moved, and one that has gone is looked for again on a fresh read.
"""
import re
import time
from dataclasses import dataclass, field

from natro_pc import uia
from natro_pc.tools import PCTool, Picture, ToolError

MAX_ELEMENTS = uia.MAX_ELEMENTS
LABEL_CHARS = 60
SETTLE_SECONDS = 0.4  # after acting, before reading the screen again
# The same words as the phone's (Text.RISKY in android/.../Speech.kt; a test keeps them the same).
RISKY = re.compile(
    r"\b(send|buy|purchase|pay|payment|order|checkout|check out|delete|remove|erase|clear all|uninstall|"
    r"transfer|submit|post|publish|share|call|subscribe|unsubscribe|book|block|report|sign out|log out)\b|"
    r"გაგზავნ|წაშალ|წაშლა|ყიდვა|იყიდე|გადახდ|შეკვეთ|გაზიარ|დარეკ", re.IGNORECASE)
# Where Enter sends what was typed: a chat or mail app, or a screen with a Send button.
MESSAGING = re.compile(r"discord|whatsapp|telegram|messenger|slack|teams|signal|viber|skype|outlook|gmail|"
                       r"\bmail\b", re.IGNORECASE)
SENDING = re.compile(r"\b(send|post|reply|submit|publish|comment)\b|გაგზავნ", re.IGNORECASE)


@dataclass(eq=False)
class Element:
    x: int
    y: int
    kind: str
    name: str
    window: str
    focused: bool = False
    password: bool = False
    value: str | None = None
    toggle: str | None = None
    state: str | None = None
    scrolls: bool = False
    hwnd: int = 0  # its window
    ref: object = field(default=None, repr=False)  # the reader's handle on it, to act on and find again

    def line(self, number):
        details = []
        if self.focused:
            details.append("focused")
        if self.password:
            details.append("password")
        if self.value is not None:
            details.append(f'value "{short(self.value, 40)}"')
        if self.toggle:
            details.append(self.toggle)
        if self.state:
            details.append(self.state)
        if self.scrolls:
            details.append("scrolls")
        name = short(self.name) or "(no name)"
        return f"[{number}] {self.kind}: {name}" + (f" ({', '.join(details)})" if details else "")

    def same_as(self, other):
        return (self.kind, self.name, self.window) == (other.kind, other.name, other.window)


@dataclass
class Screen:
    window: str | None  # the window in front
    elements: list[Element] = field(default_factory=list)  # numbered from 1, in this order
    other_windows: list[str] = field(default_factory=list)
    cut: bool = False
    hwnd: int = 0
    note: str | None = None  # why there's nothing to show, when Natro knows

    def element(self, number):
        index = int(number) if isinstance(number, (int, str)) and str(number).strip().isdigit() else 0
        if not 1 <= index <= len(self.elements):
            raise ToolError(f"There is no element [{number}] on the screen: read it again with screen.")
        return self.elements[index - 1]

    def describe(self, only=None):
        numbered = [(n, e) for n, e in enumerate(self.elements, 1) if only is None or e in only]
        lines = [f"Window in front: {self.window or 'none'}"]
        lines += [element.line(number) for number, element in numbered]
        if only is not None and not numbered:
            lines.append("(nothing on screen matches)")
        elif not self.elements:
            lines.append(f"({self.note or 'no elements Natro can use: the app may not expose them, or it is still loading'})")
        if self.cut:
            lines.append("(more on screen than shown)")
        if self.other_windows:
            lines.append(f"Other windows: {', '.join(self.other_windows)}.")
        return "\n".join(lines)

    def sends_on_enter(self):
        return bool(MESSAGING.search(self.window or "")) or any(SENDING.search(e.name) for e in self.elements)


def short(text, length=LABEL_CHARS):
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= length else flat[:length - 1] + "…"


class ScreenControl:
    """desktop: what reads the screen and acts on it (uia.Desktop; a stand-in in tests)."""

    def __init__(self, desktop=None):
        self.desktop = desktop or uia.Desktop(Element)
        self._shown = None  # the screen she last saw: her element numbers refer to it

    def _show(self, screen):
        self._shown = screen
        return screen.describe()

    def screen(self, everything=False):
        return self._show(self.desktop.read(everything))

    def find(self, text, everything=False):
        words = (text or "").casefold().split()
        if not words:
            raise ToolError("Say what to look for.")
        screen = self.desktop.read(everything)
        self._shown = screen
        matches = [e for e in screen.elements if all(w in f"{e.name} {e.value or ''}".casefold() for w in words)]
        return screen.describe(only=matches)

    def _targets(self, actions):
        """The elements the actions name, where they are now; one that has gone is looked for on a fresh read."""
        if self._shown is None:
            raise ToolError("Read the screen first (screen), so the elements have numbers.")
        numbers = {a.get("element") for a in actions if a.get("element") is not None}
        targets = {n: self._shown.element(n) for n in numbers}
        now = None
        for number, old in targets.items():
            here = self.desktop.locate(old)
            if here is None:
                now = now or self.desktop.read()
                same = [e for e in now.elements if e.same_as(old)]
                if not same:
                    self._shown = now
                    raise ToolError(f"The screen changed: [{number}] ({old.kind} {short(old.name, 30)}) isn't there "
                                    f"now. The screen now:\n{now.describe()}")
                here = min(same, key=lambda e: abs(e.x - old.x) + abs(e.y - old.y))
            targets[number] = here
        return targets

    def act(self, actions, risky=False):
        if not isinstance(actions, list) or not actions:
            raise ToolError("Say what to do: a list of actions.")
        targets = self._targets(actions)
        steps = [self._step(action, targets, risky) for action in actions]  # every action is checked before any is done
        for method, args in steps:
            getattr(self.desktop, method)(**args)
        time.sleep(SETTLE_SECONDS)
        return "Done. " + self._show(self.desktop.read())

    def _step(self, action, targets, risky):
        do = action.get("do")
        element = targets.get(action.get("element"))
        window = self._shown.hwnd
        if do in ("click", "double_click", "right_click"):
            if element is None:
                raise ToolError(f"{do} needs an element number.")
            if not risky and RISKY.search(element.name):
                raise ToolError(f"[{action['element']}] ({short(element.name, 40)}) looks like it sends, buys, pays "
                                "or deletes: use act_risky, which asks him first.")
            return "click", {"element": element, "button": "right" if do == "right_click" else "left",
                             "clicks": 2 if do == "double_click" else 1}
        if do == "type":
            if element is not None and element.password:
                raise ToolError("That's a password field: Natro never types passwords. He can type it himself.")
            enter = bool(action.get("enter"))
            if enter and not risky and self._shown.sends_on_enter():
                raise ToolError("Enter here would likely send it: use act_risky, which asks him first.")
            return "type", {"element": element, "text": str(action.get("text") or ""),
                            "clear": action.get("clear", True) is not False, "enter": enter, "window": window}
        if do == "key":
            keys = str(action.get("text") or "").strip().lower()
            pressed = {vk for vk, _ in uia.parse_keys(keys)}  # an unknown key name is refused before anything is done
            sends = uia.VK_RETURN in pressed and self._shown.sends_on_enter()
            if not risky and (sends or RISKY.search(keys.replace("+", " "))):
                raise ToolError(f"Pressing {keys} here looks like it sends or deletes: use act_risky, which asks "
                                "him first.")
            return "key", {"keys": keys, "window": window}
        if do == "scroll":
            direction = action.get("direction") or "down"
            if direction not in ("up", "down"):
                raise ToolError("scroll goes up or down.")
            return "scroll", {"element": element, "direction": direction, "window": window}
        raise ToolError(f"Unknown action {do!r}: click, double_click, right_click, type, key or scroll.")

    def switch_window(self, name):
        if not (name or "").strip():
            raise ToolError("Say which window.")
        self.desktop.switch(name)
        time.sleep(SETTLE_SECONDS)
        return self._show(self.desktop.read())

    def screenshot(self):
        data = self.desktop.screenshot()
        return Picture("The screenshot of his PC's screen is here, and in the chat, where he can see it too.", data,
                       "image/jpeg", f"PC screen {time.strftime('%H.%M')}.jpg")

    def wait_for(self, text, seconds=10):
        if not (text or "").strip():
            raise ToolError("Say what to wait for.")
        seconds = max(1, min(int(seconds or 10), 20))
        found, screen = self.desktop.wait_for(text, seconds)
        said = f"{text!r} is on the screen now." if found else f"{text!r} didn't show within {seconds} seconds."
        return f"{said}\n{self._show(screen)}"


def tools(control=None):
    """The screen tools, or none when this PC can't run them (not Windows, or comtypes missing)."""
    if control is None:
        if not uia.available():
            return []
        control = ScreenControl()
    number = {"type": "integer", "description": "The element's number on the screen."}
    action = {"type": "object", "required": ["do"], "properties": {
        "do": {"type": "string", "enum": ["click", "double_click", "right_click", "type", "key", "scroll"]},
        "element": {**number, "description": "The element's number. type: the field (leave it out to type where "
                                              "the cursor is); scroll: what to scroll (default the window)."},
        "text": {"type": "string", "description": "type: what to type (a line break is Shift+Enter, a new line). "
                                                  "key: the keys, like ctrl+s, enter, alt+tab, page down."},
        "clear": {"type": "boolean", "description": "type into a numbered field: replace what's in it (default "
                                                    "true)."},
        "enter": {"type": "boolean", "description": "type: press Enter after typing."},
        "direction": {"type": "string", "enum": ["up", "down"], "description": "scroll: default down."},
    }}
    actions = {"actions": {"type": "array", "items": action, "description": "Done in this order."}}
    return [
        PCTool("screen", "Read what's on the PC's screen now: the window he is using (web pages included) and "
               "its elements, numbered (buttons, fields, links, list items, text), to act on by number.",
               control.screen, {"everything": {"type": "boolean", "description": "Also the taskbar."}},
               personal=True),
        PCTool("find", "Find elements on the PC's screen by their text: only those, numbered as on the screen.",
               control.find, {"text": {"type": "string"}, "everything": {"type": "boolean"}}, ["text"],
               personal=True),
        PCTool("act", "Do one or more things on the PC's screen, in order, by element number: click, double_click, "
               "right_click, type (into a field, or where the cursor is), key, scroll. Returns the new screen. "
               "Refuses what looks like sending, buying, paying or deleting (a Send button, Enter in a chat): use "
               "act_risky for those.", control.act, actions, ["actions"], personal=True),
        PCTool("act_risky", "The same as act, for a click or key that sends, buys, pays or deletes.",
               lambda actions: control.act(actions, risky=True), actions, ["actions"], personal=True,
               confirm=True),
        PCTool("switch_window", "Bring an open window to the front by its title or app name; returns its screen.",
               control.switch_window, {"name": {"type": "string"}}, ["name"], personal=True),
        PCTool("wait_for", "Wait until some text shows on the PC's screen (a page loading, a dialog), up to 20 "
               "seconds; returns the screen.", control.wait_for,
               {"text": {"type": "string"}, "seconds": {"type": "integer"}}, ["text"], personal=True),
        PCTool("screenshot", "Take a picture of the PC's screen: you see it, and it goes into the chat on his devices "
               "for him (\"show me my PC screen\", \"what's this on my screen?\"). To act on the screen, read it "
               "with screen.",
               control.screenshot, personal=True),
    ]
