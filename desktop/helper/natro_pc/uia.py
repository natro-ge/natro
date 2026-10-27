"""The PC's screen through Windows UI Automation: read the window in front, click, type, press keys, scroll.

The window in front is read in one cached pass (about 0.2 s, where Windows-MCP took 2 to 14 s and stopped after
500 elements of the whole desktop). Natro's own windows, the dock, her reply and the chat he types to her in,
are never the window in front: she works on the app he was using, and brings it back to the front before
acting. Clicks go where the element can be seen; when one of her always-on-top windows covers it, the app's
own action for it (UI Automation's Invoke, Toggle or Select) is used instead.

Everything runs on one thread with COM in the multithreaded apartment, as UI Automation clients should, and
aware of each monitor's DPI, so the pixels UI Automation gives are the ones the mouse goes to.
"""
import concurrent.futures
import ctypes
import dataclasses
import io
import os
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass

from natro_pc.tools import ToolError

CALL_SECONDS = 20
MAX_ELEMENTS = 150
# Natro's desktop app (desktop/src-tauri): its windows are hers, not his.
OWN_PROCESSES = {"natro.exe"}
# Windows that are never the app he's using.
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
TASKBAR_CLASS = "Shell_TrayWnd"
CHROMIUM_CLASS = "Chrome_WidgetWin_1"  # Chrome, Edge, and Electron apps such as Discord
# Screenshots: the longest side at most this, as JPEG, so one is a few hundred KB.
SHOT_PIXELS = 1920
SHOT_QUALITY = 80
# Characters some apps put in titles ("Microsoft​ Edge") that would stop a name from matching.
INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))

# UI Automation's control types, by id, as she reads them.
KINDS = {
    50000: "button", 50001: "calendar", 50002: "check box", 50003: "combo box", 50004: "field", 50005: "link",
    50006: "image", 50007: "list item", 50008: "list", 50009: "menu", 50011: "menu item", 50013: "radio button",
    50015: "slider", 50016: "spinner", 50018: "tab list", 50019: "tab", 50020: "text", 50023: "tree",
    50024: "tree item", 50025: "custom", 50026: "group", 50029: "item", 50030: "document", 50031: "split button",
    50033: "pane", 50035: "column header",
}
# Shown whenever they're on screen: what can be clicked, typed into or read as a page.
ACTIONABLE = {50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50015, 50016, 50019, 50024, 50029, 50030,
              50031, 50035}
TEXT = 50020
TOGGLE = {0: "off", 1: "on", 2: "mixed"}
EXPAND = {0: "collapsed", 1: "expanded", 2: "partly expanded"}

# Key names as she writes them ("ctrl+shift+s", "page down", "f5"), with Windows' virtual-key codes. Extended
# keys need a flag, or Windows takes them for the number pad's.
KEYS = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B, "cmd": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "capslock": 0x14,
    "printscreen": 0x2C, "menu": 0x5D, "apps": 0x5D, "plus": 0xBB, "minus": 0xBD, "volumeup": 0xAF,
    "volumedown": 0xAE, "volumemute": 0xAD, "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD,
    "'": 0xDE,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D, 0x2C}
VK_SHIFT, VK_CONTROL, VK_MENU, VK_RETURN = 0x10, 0x11, 0x12, 0x0D


@dataclass
class Window:
    hwnd: int
    title: str
    pid: int
    process: str  # its program's file name, lower case ("msedge.exe"); "" if Windows won't say
    cls: str
    tool: bool = False  # a tool window: menus, tooltips, floating bars
    minimized: bool = False


def visible(windows, own_pids=()):
    """The windows that could be his, front first: not Natro's own, not the desktop or the taskbar."""
    return [w for w in windows if w.pid not in own_pids and w.process not in OWN_PROCESSES
            and w.cls not in SHELL_CLASSES]


def pick(windows, own_pids=()):
    """(the window in front, the untitled popups of its app above it, the other windows), from all top-level
    windows in their order on screen, front first."""
    his = visible(windows, own_pids)
    front = next((w for w in his if w.title and not w.tool and not w.minimized), None)
    if front is None:
        return None, [], [w for w in his if w.title and not w.tool]
    above = his[:his.index(front)]
    popups = [w for w in above if w.pid == front.pid and (w.tool or not w.title)]
    others, seen = [], {front.title}
    for w in his[his.index(front) + 1:]:
        if w.title and not w.tool and w.title not in seen:
            seen.add(w.title)
            others.append(w)
    return front, popups, others


def plain(text):
    return " ".join(str(text or "").translate(INVISIBLE).split())


@dataclass
class Raw:
    """One element as UI Automation gives it."""
    kind: int
    name: str
    rect: tuple  # left, top, right, bottom
    window: str
    hwnd: int
    focused: bool = False
    password: bool = False
    enabled: bool = True
    focusable: bool = False
    invokable: bool = False
    value: str | None = None
    toggle: int | None = None
    expand: int | None = None
    scrolls: bool = False
    ref: object = None


def _inside(rect, outer):
    return outer[0] <= (rect[0] + rect[2]) / 2 <= outer[2] and outer[1] <= (rect[1] + rect[3]) / 2 <= outer[3]


def keep(raws, element_type, limit=MAX_ELEMENTS):
    """The elements worth numbering, in the order on screen, and whether some were left out.

    What can be clicked or typed into always; text with something in it, unless it only repeats the label of
    what it's in (a button's own text); other named containers only if they take clicks or focus. With more
    than the limit, text goes first, so every button still has a number.
    """
    kept = []
    for raw in raws:
        left, top, right, bottom = raw.rect
        if right - left < 1 or bottom - top < 1:
            continue
        name = plain(raw.name)
        if raw.kind == TEXT:
            if not name:
                continue
            last = kept[-1] if kept else None
            if last is not None and last[1].kind != TEXT and name in plain(last[1].name) and _inside(raw.rect, last[1].rect):
                continue
        elif raw.kind not in ACTIONABLE and not (name and (raw.focusable or raw.invokable)):
            continue
        kept.append((raw.kind == TEXT, raw))
    cut = len(kept) > limit
    if cut:
        actionable = sum(1 for is_text, _ in kept if not is_text)
        room, texts = max(0, limit - actionable), 0
        trimmed = []
        for is_text, raw in kept:
            if is_text:
                if texts >= room:
                    continue
                texts += 1
            trimmed.append((is_text, raw))
        kept = trimmed[:limit]
    return [_element(raw, element_type) for _, raw in kept], cut


def _element(raw, element_type):
    left, top, right, bottom = raw.rect
    state = EXPAND.get(raw.expand) if raw.expand is not None else None
    if not raw.enabled:
        state = "disabled"
    return element_type(x=(left + right) // 2, y=(top + bottom) // 2, kind=KINDS.get(raw.kind, "element"),
                        name=plain(raw.name), window=raw.window, focused=raw.focused, password=raw.password,
                        value=plain(raw.value) if raw.value else None, toggle=TOGGLE.get(raw.toggle),
                        state=state, scrolls=raw.scrolls, hwnd=raw.hwnd, ref=raw.ref)


def parse_keys(text):
    """[(virtual-key code, extended)] for "ctrl+shift+s": the modifiers, then the key."""
    names = [part.strip().lower().replace(" ", "") for part in str(text or "").split("+")]
    if text and str(text).strip().endswith("+"):  # "ctrl++": the plus key
        names = [n for n in names if n] + ["plus"]
    keys = []
    for name in names:
        if name in KEYS:
            vk = KEYS[name]
        elif len(name) == 1 and name.isalnum() and name.isascii():
            vk = ord(name.upper())
        else:
            raise ToolError(f"Unknown key {name!r}: use names like ctrl, shift, alt, win, enter, tab, esc, space, "
                            "backspace, delete, home, end, page up, page down, up, down, left, right, f1 to f24, "
                            "or a letter or digit.")
        keys.append((vk, vk in EXTENDED))
    if not keys:
        raise ToolError("key needs the keys, like ctrl+s or enter.")
    return keys


def key_events(keys):
    """[(vk, extended, up)]: every key down in order, then up in reverse."""
    return [(vk, ext, False) for vk, ext in keys] + [(vk, ext, True) for vk, ext in reversed(keys)]


def typing(text):
    """What typing the text sends: ("char", UTF-16 unit) for each character, and Shift+Enter for a line break,
    which starts a new line in a chat instead of sending it."""
    events = []
    for line_number, line in enumerate(str(text).replace("\r\n", "\n").split("\n")):
        if line_number:
            events += [("key", vk, ext, up) for vk, ext, up in key_events([(VK_SHIFT, False), (VK_RETURN, False)])]
        data = line.encode("utf-16-le")
        events += [("char", int.from_bytes(data[i:i + 2], "little")) for i in range(0, len(data), 2)]
    return events


# Windows ---------------------------------------------------------------------------------------------------

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSE_BUTTONS = {"left": (0x2, 0x4), "right": (0x8, 0x10)}
MOUSEEVENTF_WHEEL, WHEEL_DELTA = 0x800, 120
PER_MONITOR_AWARE_V2 = -4
GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x80
DWMWA_CLOAKED = 14
GA_ROOT, SW_RESTORE = 2, 9
MONITOR_DEFAULTTOPRIMARY = 1


def _send(inputs):
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs):
        raise ToolError("Windows didn't take the keys or clicks (a window running as administrator may be in front).")


def _key_input(vk, extended, up):
    flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, u=_INPUT(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags)))


def _typing_inputs(events):
    inputs = []
    for event in events:
        if event[0] == "char":
            for up in (False, True):
                inputs.append(INPUT(type=INPUT_KEYBOARD, u=_INPUT(ki=KEYBDINPUT(
                    wVk=0, wScan=event[1], dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)))))
        else:
            inputs.append(_key_input(*event[1:]))
    return inputs


def _mouse_input(flags, data=0):
    return INPUT(type=INPUT_MOUSE, u=_INPUT(mi=MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flags)))


def _title(hwnd):
    buffer = ctypes.create_unicode_buffer(512)
    user32.InternalGetWindowText(hwnd, buffer, 512)  # unlike GetWindowText, never waits on a hung app
    return plain(buffer.value)


def _top_windows():
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(
        lambda hwnd, _: found.append(hwnd) or True)
    user32.EnumWindows(proc, 0)
    return found


_process_names = {}


def _process(pid):
    if pid not in _process_names:
        if len(_process_names) > 500:  # process ids are reused: forget them now and then
            _process_names.clear()
        try:
            import psutil

            _process_names[pid] = psutil.Process(pid).name().lower()
        except Exception:  # gone, or an administrator's process
            _process_names[pid] = ""
    return _process_names[pid]


def windows():
    """Every visible top-level window, front first."""
    result = []
    for hwnd in _top_windows():
        if not user32.IsWindowVisible(hwnd):
            continue
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if cloaked.value or rect.right <= rect.left or rect.bottom <= rect.top:
            continue  # on another virtual desktop, a suspended Store app, or nothing to see
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        result.append(Window(hwnd=hwnd, title=_title(hwnd), pid=pid.value, process=_process(pid.value),
                             cls=cls.value, tool=bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW),
                             minimized=bool(user32.IsIconic(hwnd))))
    return result


def available():
    if sys.platform != "win32":
        return False
    try:
        import comtypes  # noqa: F401
    except ImportError:
        return False
    return True


class Desktop:
    """The screen, as ScreenControl uses it: read, locate, click, type, key, scroll, switch, wait_for."""

    def __init__(self, element_type=None):
        self._element_type = element_type
        self._pool = None
        self._own = {os.getpid()}

    # The one thread

    def _start(self):
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="screen",
                                                               initializer=self._init_thread)
        return self._pool

    def _init_thread(self):
        user32.SetThreadDpiAwarenessContext(PER_MONITOR_AWARE_V2)
        if "comtypes" not in sys.modules:
            sys.coinit_flags = 0  # COINIT_MULTITHREADED, which comtypes uses when it is first imported
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialized in this thread
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        self.UIA = UIA
        self.COMError = comtypes.COMError
        self.uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        try:  # don't wait long on an app that has stopped answering
            uia2 = self.uia.QueryInterface(UIA.IUIAutomation2)
            uia2.ConnectionTimeout, uia2.TransactionTimeout = 2000, 5000
        except Exception:
            pass
        self.cache = self.uia.CreateCacheRequest()
        for prop in ("Name", "ControlType", "BoundingRectangle", "HasKeyboardFocus", "IsPassword", "IsEnabled",
                     "IsKeyboardFocusable", "IsInvokePatternAvailable", "IsValuePatternAvailable", "ValueValue",
                     "IsTogglePatternAvailable", "ToggleToggleState", "IsExpandCollapsePatternAvailable",
                     "ExpandCollapseExpandCollapseState", "IsScrollPatternAvailable", "ScrollVerticallyScrollable"):
            self.cache.AddProperty(getattr(UIA, f"UIA_{prop}PropertyId"))
        self.cache.TreeScope = UIA.TreeScope_Element
        onscreen = self.uia.CreatePropertyCondition(UIA.UIA_IsOffscreenPropertyId, False)
        self.condition = self.uia.CreateAndCondition(self.uia.ControlViewCondition, onscreen)

    def _run(self, work, *args, seconds=CALL_SECONDS):
        future = self._start().submit(work, *args)
        try:
            return future.result(seconds)
        except concurrent.futures.TimeoutError:
            self._pool = None  # that thread may be stuck on a hung app: the next call gets a new one
            raise ToolError(f"The screen didn't answer within {seconds} seconds: an app may be hanging.") from None
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"The screen couldn't be used: {e}") from None

    def warm_up(self):
        """Start the thread ahead of the first screen request (the first time, comtypes writes its UI Automation
        module, a second or two)."""
        try:
            self._run(lambda: None, seconds=60)
        except ToolError as e:
            print(f"Screen control not ready: {e}")

    # Reading

    def read(self, everything=False):
        return self._run(self._read, everything)

    def _read(self, everything):
        from natro_pc.control import Screen

        front, popups, others = pick(windows(), self._own)
        if front is None:
            return Screen(None, [], [w.title for w in others])
        targets = popups + [front]
        if everything:
            targets += [w for w in windows() if w.cls == TASKBAR_CLASS]
        raws = []
        for window in targets:
            raws += self._raws(window, front.title)
        elements, cut = keep(raws, self._type())
        if front.cls == CHROMIUM_CLASS and len(elements) < 5:
            # Chromium (Edge, Chrome, Discord) shows a page to UI Automation only once it has been asked for it,
            # and not while its window is hidden behind others: in front, it comes on a second look.
            self._front(front.hwnd)
            time.sleep(0.3)
            raws = [raw for window in targets for raw in self._raws(window, front.title)]
            elements, cut = keep(raws, self._type())
        screen = Screen(front.title, elements, [w.title for w in others], cut, hwnd=front.hwnd)
        if not elements and not front.process:
            screen.note = "this window runs as administrator, and Natro can't use those"
        return screen

    def _type(self):
        if self._element_type is None:
            from natro_pc.control import Element

            self._element_type = Element
        return self._element_type

    def _raws(self, window, front_title):
        UIA = self.UIA
        try:
            root = self.uia.ElementFromHandle(window.hwnd)
            found = root.FindAllBuildCache(UIA.TreeScope_Descendants, self.condition, self.cache)
        except self.COMError:
            return []  # it closed while being read
        raws = []
        for i in range(found.Length if found else 0):
            e = found.GetElement(i)
            try:
                r = e.CachedBoundingRectangle
                has = lambda pattern: e.GetCachedPropertyValue(  # noqa: E731
                    getattr(UIA, f"UIA_Is{pattern}PatternAvailablePropertyId")) is True
                # Some apps (Chromium) give a state for patterns an element doesn't have: only asked when it has them.
                value = e.GetCachedPropertyValue(UIA.UIA_ValueValuePropertyId) if has("Value") else None
                toggle = e.GetCachedPropertyValue(UIA.UIA_ToggleToggleStatePropertyId) if has("Toggle") else None
                expand = e.GetCachedPropertyValue(UIA.UIA_ExpandCollapseExpandCollapseStatePropertyId)                     if has("ExpandCollapse") else None
                scrolls = has("Scroll") and e.GetCachedPropertyValue(UIA.UIA_ScrollVerticallyScrollablePropertyId)
                invokable = has("Invoke")
                raws.append(Raw(
                    kind=e.CachedControlType, name=e.CachedName or "", rect=(r.left, r.top, r.right, r.bottom),
                    window=window.title or front_title, hwnd=window.hwnd, focused=bool(e.CachedHasKeyboardFocus),
                    password=bool(e.CachedIsPassword), enabled=bool(e.CachedIsEnabled),
                    focusable=bool(e.CachedIsKeyboardFocusable), invokable=invokable,
                    value=value if isinstance(value, str) else None,
                    toggle=toggle if isinstance(toggle, int) else None,
                    expand=expand if isinstance(expand, int) and expand != 3 else None,  # 3: a leaf, nothing to open
                    scrolls=scrolls is True, ref=e))
            except self.COMError:
                continue
        return raws

    def locate(self, element):
        """The element where it is now, or None when it has gone or scrolled out of sight (a few milliseconds)."""
        return self._run(self._locate, element)

    def _locate(self, element):
        try:
            if element.ref.CurrentIsOffscreen:
                return None
            r = element.ref.CurrentBoundingRectangle
        except (self.COMError, ValueError, AttributeError):
            return None
        if r.right - r.left < 1 or r.bottom - r.top < 1:
            return None
        return dataclasses.replace(element, x=(r.left + r.right) // 2, y=(r.top + r.bottom) // 2)

    # Acting

    def _front(self, hwnd):
        """Bring the window to the front; whether it is."""
        if not hwnd or user32.GetForegroundWindow() == hwnd:
            return bool(hwnd)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        front = user32.GetForegroundWindow()
        theirs = user32.GetWindowThreadProcessId(front, None) if front else 0
        mine = kernel32.GetCurrentThreadId()
        attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(mine, theirs, False)
        if not self._became_front(hwnd):
            _send([_key_input(VK_MENU, False, False), _key_input(VK_MENU, False, True)])  # Windows lets a key press through
            user32.SetForegroundWindow(hwnd)
            return self._became_front(hwnd)
        return True

    @staticmethod
    def _became_front(hwnd, seconds=0.5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
        return False

    def _seen_point(self, element):
        """A point of the element that is really its window's on screen (not under one of Natro's own
        always-on-top windows), or None."""
        try:
            r = element.ref.CurrentBoundingRectangle
            rect = (r.left, r.top, r.right, r.bottom)
        except (self.COMError, ValueError, AttributeError):
            rect = (element.x, element.y, element.x + 1, element.y + 1)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        for fx, fy in ((0.5, 0.5), (0.25, 0.5), (0.75, 0.5), (0.5, 0.25), (0.5, 0.75), (0.1, 0.5), (0.9, 0.5)):
            point = wintypes.POINT(int(left + width * fx), int(top + height * fy))
            if user32.GetAncestor(user32.WindowFromPoint(point), GA_ROOT) == element.hwnd:
                return point.x, point.y
        return None

    @contextmanager
    def _mouse_kept(self):
        """The mouse goes back where he left it."""
        was = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(was))
        try:
            yield
        finally:
            time.sleep(0.05)
            user32.SetCursorPos(was.x, was.y)

    def _pattern(self, element, pattern_id, interface):
        try:
            return element.ref.GetCurrentPattern(pattern_id).QueryInterface(interface)
        except (self.COMError, ValueError, AttributeError):
            return None

    def _do_default(self, element):
        """The app's own action for the element, as a screen reader does it; whether there was one."""
        UIA = self.UIA
        for pattern_id, interface, act in (
                (UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern, lambda p: p.Invoke()),
                (UIA.UIA_TogglePatternId, UIA.IUIAutomationTogglePattern, lambda p: p.Toggle()),
                (UIA.UIA_SelectionItemPatternId, UIA.IUIAutomationSelectionItemPattern, lambda p: p.Select()),
                (UIA.UIA_LegacyIAccessiblePatternId, UIA.IUIAutomationLegacyIAccessiblePattern,
                 lambda p: p.DoDefaultAction())):
            pattern = self._pattern(element, pattern_id, interface)
            if pattern is not None:
                try:
                    act(pattern)
                    return True
                except self.COMError:
                    continue
        return False

    def click(self, element, button="left", clicks=1):
        self._run(self._click, element, button, clicks)

    def _click(self, element, button, clicks):
        self._front(element.hwnd)
        point = self._seen_point(element)
        if point is None:
            if button == "left" and clicks == 1 and self._do_default(element):
                return
            raise ToolError(f"{element.kind} {element.name!r} is covered by another window, and it has no action "
                            "Natro can use without the mouse.")
        down, up = MOUSE_BUTTONS[button]
        with self._mouse_kept():
            user32.SetCursorPos(*point)
            _send([_mouse_input(flag) for _ in range(clicks) for flag in (down, up)])

    def type(self, element, text, clear=True, enter=False, window=0):
        self._run(self._type_text, element, text, clear, enter, window)

    def _type_text(self, element, text, clear, enter, window):
        if not self._front(element.hwnd if element is not None else window):
            raise ToolError("Couldn't bring the window to the front to type into it.")
        if element is not None:
            try:
                element.ref.SetFocus()
            except (self.COMError, ValueError, AttributeError):
                self._click(element, "left", 1)
            time.sleep(0.05)
        try:
            focused = self.uia.GetFocusedElement()
            if focused is not None and focused.CurrentIsPassword:
                raise ToolError("That's a password field: Natro never types passwords. He can type it himself.")
        except self.COMError:
            pass
        events = []
        if clear and element is not None:
            events += [("key", vk, ext, up) for vk, ext, up in key_events([(VK_CONTROL, False), (ord("A"), False)])]
        events += typing(text)
        if enter:
            events += [("key", VK_RETURN, False, False), ("key", VK_RETURN, False, True)]
        _send(_typing_inputs(events))

    def key(self, keys, window=0):
        self._run(self._key, keys, window)

    def _key(self, keys, window):
        if not self._front(window):
            raise ToolError("Couldn't bring the window to the front to press keys in it.")
        _send([_key_input(vk, ext, up) for vk, ext, up in key_events(parse_keys(keys))])

    def scroll(self, element, direction="down", window=0, notches=3):
        self._run(self._scroll, element, direction, window, notches)

    def _scroll(self, element, direction, window, notches):
        hwnd = element.hwnd if element is not None else window
        self._front(hwnd)
        point = self._seen_point(element) if element is not None else self._window_point(hwnd)
        if point is None:
            UIA = self.UIA
            pattern = self._pattern(element, UIA.UIA_ScrollPatternId, UIA.IUIAutomationScrollPattern) \
                if element is not None else None
            if pattern is None:
                raise ToolError("What should scroll is covered by another window.")
            amount = UIA.ScrollAmount_LargeIncrement if direction == "down" else UIA.ScrollAmount_LargeDecrement
            pattern.Scroll(UIA.ScrollAmount_NoAmount, amount)
            return
        delta = -WHEEL_DELTA * notches if direction == "down" else WHEEL_DELTA * notches
        with self._mouse_kept():
            user32.SetCursorPos(*point)
            _send([_mouse_input(MOUSEEVENTF_WHEEL, ctypes.c_uint32(delta).value)])

    @staticmethod
    def _window_point(hwnd):
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        point = wintypes.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        return (point.x, point.y) if user32.GetAncestor(user32.WindowFromPoint(point), GA_ROOT) == hwnd else None

    # Screenshots

    def screenshot(self):
        """The screen he's using (the monitor of the window in front), as JPEG bytes."""
        return self._run(self._screenshot)

    def _screenshot(self):
        from PIL import ImageGrab

        front, _, _ = pick(windows(), self._own)
        monitor = user32.MonitorFromWindow(front.hwnd if front else user32.GetForegroundWindow(),
                                           MONITOR_DEFAULTTOPRIMARY)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        area = info.rcMonitor
        image = ImageGrab.grab(bbox=(area.left, area.top, area.right, area.bottom), all_screens=True)
        image.thumbnail((SHOT_PIXELS, SHOT_PIXELS))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=SHOT_QUALITY, optimize=True)
        return out.getvalue()

    # Windows and waiting

    def switch(self, name):
        """Bring the open window best matching the name to the front; its title."""
        return self._run(self._switch, name)

    def _switch(self, name):
        wanted = plain(name).casefold()
        his = [w for w in visible(windows(), self._own) if w.title and not w.tool]

        def score(w):
            title, program = w.title.casefold(), w.process.removesuffix(".exe")
            if title == wanted:
                return 4
            if wanted in title:
                return 3
            if wanted.replace(" ", "") in program or (program and program in wanted.replace(" ", "")):
                return 2
            return 0

        best = max(his, key=score, default=None)
        if best is None or score(best) == 0:
            names = ", ".join(dict.fromkeys(w.title for w in his)) or "none"
            raise ToolError(f"No open window matches {name!r}. Open windows: {names}.")
        if not self._front(best.hwnd):
            raise ToolError(f"Windows didn't let Natro bring {best.title!r} to the front.")
        return best.title

    def wait_for(self, text, seconds=10):
        """(whether the text showed, the screen then)."""
        wanted = plain(text).casefold()
        end = time.monotonic() + seconds
        while True:
            screen = self.read()
            if any(wanted in f"{e.name} {e.value or ''}".casefold() for e in screen.elements) or \
                    wanted in (screen.window or "").casefold():
                return True, screen
            if time.monotonic() >= end:
                return False, screen
            time.sleep(0.4)
