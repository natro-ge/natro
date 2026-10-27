"""His Edge, driven through Playwright: open a site, read the page by its structure (not its pixels), click,
type, choose, press keys, go back, switch tabs. It's his own browser, with his logins.

Playwright's MCP server (PACKAGE, run with npx) connects to his running Edge through Microsoft's "Playwright
Extension", installed in his Edge profile; its token (PLAYWRIGHT_MCP_EXTENSION_TOKEN in the PC's .env) lets it
connect without asking him each time. Without the token the tools aren't offered. It works in a tab group of
its own: his tabs are left alone, and its tabs stay open when it's done (owner, 2026-10-18), for him to see.

Like the screen tools (control.py): a page is a list of elements with refs ('button "Send" [ref=e12]'), and act
does several things in order, each checked before any is done. What looks like sending, buying, paying or
deleting (control.RISKY), and Enter where it would send, go through act_risky, which the brain asks him about
first. Passwords are never typed. On bank and payment sites (READ_ONLY) she only reads: no clicking or typing
at all, even with his yes (owner, 2026-10-18). Everything in his browser is personal: only Claude sees it.

send_message is a script with no model steps, for a message on Instagram or Messenger (server/natro_agent/sending.py
calls it after his yes when his phone has no quiet way to send it; the model isn't offered it): the chat opened, its
title checked against whom he asked for, the text typed and sent, and seen in the chat. It runs in Natro's own Edge,
not his: headless, so nothing ever shows (owner, 2026-10-23), with a profile of its own (SEND_PROFILE) that he signs
into once, Instagram and Messenger and Messenger's PIN, in a window this opens: python -m natro_pc.browser. It's
closed after each message.
"""
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from contextlib import AsyncExitStack
from pathlib import Path

from natro_pc.control import RISKY
from natro_pc.tools import PCTool, ToolError

PACKAGE = "@playwright/mcp@0.0.83"
TOKEN = "PLAYWRIGHT_MCP_EXTENSION_TOKEN"
# Natro's own Edge profile, for sending messages unseen. Out of the repository: it holds his sign-ins.
SEND_PROFILE = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "Natro" / "Edge"
EDGE_PATHS = [Path(os.environ.get(folder) or "") / "Microsoft/Edge/Application/msedge.exe"
              for folder in ("ProgramFiles(x86)", "ProgramFiles")]
CALL_SECONDS = 60
START_SECONDS = 90  # the first start downloads the package
PAGE_CHARS = 18000
# Playwright keeps a copy of each page it reads; they go here, at most OUTPUT_BYTES of them.
OUTPUT_DIR = Path(tempfile.gettempdir()) / "natro-playwright"
OUTPUT_BYTES = 20 << 20
# Its tools that change the page answer with a link to the page saved as a file; she reads it right after.
CHANGING = {"browser_navigate", "browser_click", "browser_type", "browser_select_option", "browser_press_key",
            "browser_navigate_back"}
# Bank and payment sites: read only (owner, 2026-10-18). NATRO_READ_ONLY_SITES adds more (comma-separated).
READ_ONLY = re.compile(
    r"bank|(^|\.)(bog\.ge|tbconline\.ge|tbcpay\.ge|crystal\.ge|paypal\.com|revolut\.com|wise\.com|payoneer\.com|"
    r"stripe\.com|payze\.io|keepz\.me|payriff\.com|unipay\.com)$", re.IGNORECASE)
PASSWORD = re.compile(r"password|passcode|\bpin\b|პაროლ", re.IGNORECASE)
# Fields where Enter looks something up rather than sending something.
SEARCHING = re.compile(r"search|find|look ?up|query|address|url|where|destination|ძებნ|ძიება", re.IGNORECASE)
ELEMENT = re.compile(r'^\s*- (?P<role>[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?[^\n]*?\[ref=(?P<ref>[\w-]+)\]',
                     re.MULTILINE)
# send_message: how long a chat may take to show, and the message to show in it once sent.
CHAT_SECONDS = 10
SEEN_SECONDS = 4
LABELS = {"instagram": "Instagram", "messenger": "Messenger"}
# The words on a line of a page's snapshot: an element's name ('- heading "Nino" [level=2]') or its text
# ('- text: Nino', '- link "x" [ref=e3]:' has its name).
WORDS = re.compile(r'^\s*- (?:[\w-]+ "(?P<name>(?:[^"\\]|\\.)*)"|[\w-]+(?: \[[^\]]*\])*: (?P<text>.+))', re.MULTILINE)
KEYS = {"enter": "Enter", "escape": "Escape", "esc": "Escape", "tab": "Tab", "backspace": "Backspace",
        "delete": "Delete", "arrowdown": "ArrowDown", "arrowup": "ArrowUp", "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight", "pagedown": "PageDown", "pageup": "PageUp", "home": "Home", "end": "End",
        "space": " "}


def host(url):
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()


def chat_urls(app, chat="", facebook="", username=""):
    """Where his chat with someone opens on the web, best first. chat: the app's shortcut id for it, as the phone's
    notifications name it (Instagram: thread_shortcut_<his id>_<thread id>; Messenger: thread_shortcut_GROUP:<id>
    or thread_shortcut_ADVANCED_CRYPTO_ONE_TO_ONE:<id>)."""
    thread = chat.rsplit("_", 1)[-1].rsplit(":", 1)[-1]
    if app == "instagram":
        return ([f"https://www.instagram.com/direct/t/{thread}/"] if thread.isdigit() else []) + \
            ([f"https://ig.me/m/{username}"] if username else [])
    urls = [f"https://www.messenger.com/t/{who}" for who in (facebook, username) if who]
    if thread.isdigit() and "GROUP:" in chat:
        urls.insert(0, f"https://www.messenger.com/t/{thread}")
    elif thread.isdigit() and "CRYPTO" in chat:
        urls.append(f"https://www.messenger.com/e2ee/t/{thread}")
    return urls


def plain(text):
    return " ".join(str(text).casefold().split())


def words_on(page):
    """Every name and text on a page's snapshot."""
    return [(found["name"] or found["text"] or "").replace('\\"', '"').strip() for found in WORDS.finditer(page)]


def read_only(url):
    """Whether a page is on a bank or payment site, where Natro only reads."""
    name = host(url)
    extra = [site.strip().lower() for site in os.environ.get("NATRO_READ_ONLY_SITES", "").split(",") if site.strip()]
    return bool(name) and (bool(READ_ONLY.search(name)) or any(name == s or name.endswith("." + s) for s in extra))


def tidy(text):
    """Playwright's answer, cut to PAGE_CHARS (the code it ran is left out: --codegen none)."""
    text = re.sub(r"### Ran Playwright code\n```[^`]*```\n*", "", text)
    if len(text) > PAGE_CHARS:
        text = text[:PAGE_CHARS] + "\n[the page is longer than this: act on what's here, or open a part of it]"
    return text.strip()


class Playwright:
    """Playwright's MCP server, in an Edge (his_edge, hidden_edge): one call at a time, on a thread of its own (the
    helper's tools are called from threads). Started on first use; started again if it stopped. args: its own,
    after the package; env: added to the environment."""

    def __init__(self, args, env=None):
        self.args = list(args)
        self.env = dict(env or {})
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._stack = None
        self._lock = threading.Lock()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def call(self, name, args):
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._client is None:
                        self._wait(self._start(), START_SECONDS)
                    return self._wait(self._call(name, args), CALL_SECONDS)
                except ToolError:
                    raise
                except Exception as e:  # the server stopped, or never started
                    self._wait(self._stop(), 10)
                    if attempt == 2:
                        raise ToolError(f"Natro couldn't reach Edge through Playwright: {type(e).__name__}: {e}")

    def _wait(self, coroutine, seconds):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(seconds)

    async def _start(self):
        from mcp import Client, StdioServerParameters

        npx = shutil.which("npx") or "npx"
        args = ["-y", PACKAGE, *self.args, "--codegen", "none", "--output-dir", str(OUTPUT_DIR),
                "--output-max-size", str(OUTPUT_BYTES)]
        server = StdioServerParameters(command=npx, args=args, env={**os.environ, **self.env})
        self._stack = AsyncExitStack()
        self._client = await self._stack.enter_async_context(Client(server))

    async def _call(self, name, args):
        result = await self._client.call_tool(name, args)
        text = "\n".join(item.text for item in result.content if item.type == "text")
        if result.is_error:
            raise ToolError(text or f"{name} didn't work.")
        return text

    async def _stop(self):
        stack, self._stack, self._client = self._stack, None, None
        if stack:
            try:
                await stack.aclose()
            except Exception:
                pass


def his_edge(token):
    """Playwright in his own running Edge, through the extension: his logins, in tabs of its own."""
    args = ["--extension", "--browser", "msedge"]  # Edge named: otherwise it looks for the extension in Chrome
    if profile := os.environ.get("NATRO_EDGE_PROFILE"):
        args += ["--profile-dir-name", profile]
    return Playwright(args, {TOKEN: token})


def hidden_edge(profile=SEND_PROFILE):
    """Playwright in Natro's own Edge: headless (no window ever), with its own profile, which he signs into once
    (sign_in)."""
    return Playwright(["--browser", "msedge", "--headless", "--user-data-dir", str(profile)])


def sign_in(profile=SEND_PROFILE):
    """Open Natro's own Edge, in a window this once, at Instagram and Messenger: he signs in to both (and enters
    Messenger's PIN, for its encrypted chats), then closes it. Natro never types any of it."""
    edge = next((path for path in EDGE_PATHS if path.is_file()), None) or shutil.which("msedge")
    if not edge:
        raise SystemExit("Edge isn't installed where it usually is.")
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(edge), f"--user-data-dir={profile}", "--no-first-run", "--new-window",
                      "https://www.instagram.com/", "https://www.messenger.com/"])
    print(f"Natro's own Edge is open ({profile}): sign in to Instagram and Messenger there, enter Messenger's PIN "
          "if it asks, then close that window.")


class Browser:
    """playwright: something with call(name, args) -> text (Playwright, or a fake in tests)."""

    def __init__(self, playwright):
        self.playwright = playwright
        self.url = ""  # the page she's on, as Playwright last said
        self.elements = {}  # ref -> (role, name), from the page as last read
        self.snapshot = ""  # the page as last read

    def _run(self, name, args=None):
        text = self.playwright.call(name, args or {})
        if name in CHANGING:  # its answer links to the page in a file: read the page itself
            text = self.playwright.call("browser_snapshot", {})
        if found := re.search(r"Page URL: (\S+)", text):
            self.url = found.group(1)
        found = {match["ref"]: (match["role"], (match["name"] or "").replace('\\"', '"'))
                 for match in ELEMENT.finditer(text)}
        if found:
            self.elements = found
            self.snapshot = text
        return tidy(text)

    # Tools

    def open(self, url, new_tab=False):
        url = str(url or "").strip()
        if not re.match(r"https?://", url):
            if not url or " " in url:
                raise ToolError("Give a web address (example.com, or https://...).")
            url = "https://" + url
        if new_tab:
            self._run("browser_tabs", {"action": "new"})
        said = self._run("browser_navigate", {"url": url})
        return f"(A bank or payment site: you can only read here.)\n{said}" if read_only(self.url) else said

    def page(self):
        return self._run("browser_snapshot")

    def tabs(self, action="list", index=None):
        if action not in ("list", "select", "close", "new"):
            raise ToolError("action is list, new, select or close.")
        args = {"action": action}
        if action in ("select", "close"):
            if index is None:
                raise ToolError(f"{action} needs the tab's index (from list).")
            args["index"] = int(index)
        return self._run("browser_tabs", args)

    def act(self, actions, risky=False):
        if not isinstance(actions, list) or not actions:
            raise ToolError("Say what to do: a list of actions.")
        steps, searching = [], False
        for action in actions:  # every action is checked before any is done
            step, searching = self._step(action if isinstance(action, dict) else {}, risky, searching)
            steps.append(step)
        said = ""
        for name, args in steps:
            said = self._run(name, args)
        return "Done.\n" + said

    def send_message(self, app, text, name="", chat="", facebook="", username="", group=False):
        label = LABELS.get(app)
        if label is None:
            raise ToolError("Edge sends messages on Instagram and Messenger only.")
        text = str(text or "").strip()
        if not text:
            raise ToolError("The message is empty.")
        shown = name or username
        urls = chat_urls(app, str(chat or ""), str(facebook or ""), str(username or ""))
        if not urls:
            raise ToolError(f"Edge can't open your chat with {shown} on {label}: ask him for their username.")
        wanted = {plain(name), plain(username)} - {""}
        said = ""
        for url in urls:
            said = self._run("browser_navigate", {"url": url})
            if self._wait(lambda: self._box() is not None and bool(wanted & set(map(plain, words_on(self.snapshot)))),
                          CHAT_SECONDS):
                break
            if "/login" in self.url or any(role == "textbox" and PASSWORD.search(name)
                                           for role, name in self.elements.values()):
                raise ToolError(f"Natro's own Edge on the PC isn't signed in to {label} (or wants Messenger's PIN): "
                                "he signs in there once himself (python -m natro_pc.browser opens it); Natro never "
                                "types it. Nothing was sent.")
        else:
            raise ToolError(f"Your chat with {shown} didn't open in Edge, or isn't plainly theirs, so nothing was "
                            f"sent. The page now:\n{said}")
        ref, box = self._box()
        before = self._shows(text)
        self._run("browser_type", {"element": f"textbox {box}", "target": ref, "text": text, "submit": False})
        sends = [ref for ref, (role, name) in self.elements.items() if role == "button" and plain(name) == "send"]
        if len(sends) > 1:
            raise ToolError("The chat has two Send buttons, so nothing was sent (the message is typed in its box).")
        if sends:
            self._run("browser_click", {"element": "button Send", "target": sends[0]})
        else:  # Instagram's web page sends on Enter
            self._run("browser_press_key", {"key": "Enter"})
        if not self._wait(lambda: self._shows(text) > before, SEEN_SECONDS):
            raise ToolError(f"I sent it from the PC, but don't see it in the chat: he should check {label}. Don't "
                            "send it again.")
        return f"Sent to {shown} on {label}."

    def close(self):
        """Close the browser (Natro's own Edge, after a message): nothing of it keeps running."""
        try:
            self.playwright.call("browser_close", {})
        except ToolError:
            pass

    def _box(self):
        """(ref, name) of the chat's box, where a message is written, if the page has one: the text box named for a
        message, else the only one that isn't for searching (Instagram's has no name)."""
        boxes = [(ref, name) for ref, (role, name) in self.elements.items()
                 if role == "textbox" and not SEARCHING.search(name) and not PASSWORD.search(name)]
        named = [box for box in boxes if re.search(r"message|მესიჯ|შეტყობინ", box[1], re.IGNORECASE)]
        found = named or boxes
        return found[0] if len(found) == 1 else None

    def _shows(self, text):
        """How many times the page shows these words, not counting the box."""
        return sum(1 for line in self.snapshot.splitlines() if text in line and "textbox" not in line)

    def _wait(self, check, seconds):
        """Read the page again until check() is true, for up to seconds."""
        until = time.monotonic() + seconds
        while not check():
            if time.monotonic() > until:
                return False
            time.sleep(0.5)
            self._run("browser_snapshot")
        return True

    def _step(self, action, risky, searching):
        """(Playwright's tool and its arguments, whether Enter would now look something up)."""
        do = action.get("do")
        if do == "back":
            return ("browser_navigate_back", {}), False
        if read_only(self.url):
            raise ToolError("This is a bank or payment site: Natro only reads here, and never clicks or types, "
                            "even with his yes. He can do it himself.")
        ref = str(action.get("ref") or "")
        role, name = self.elements.get(ref, ("", ""))
        if do in ("click", "double_click", "type", "select") and not role:
            raise ToolError(f"There's no element {ref or '(none)'} on the page as last read: read it again "
                            "(browser_page) and use its refs.")
        element = f"{role} {name}".strip()
        if do in ("click", "double_click"):
            if not risky and RISKY.search(name):
                raise ToolError(f"{ref} ({element}) looks like it sends, buys, pays or deletes: use act_risky, which "
                                "asks him first.")
            args = {"element": element, "target": ref}
            if do == "double_click":
                args["doubleClick"] = True
            return ("browser_click", args), False
        if do == "type":
            if PASSWORD.search(name):
                raise ToolError("That's a password field: Natro never types passwords. He can type it himself.")
            submit = bool(action.get("submit"))
            looks_up = bool(SEARCHING.search(name))
            if submit and not risky and not looks_up:
                raise ToolError(f"Enter in {element} would likely send it: use act_risky, which asks him first.")
            return ("browser_type", {"element": element, "target": ref, "text": str(action.get("text") or ""),
                                     "submit": submit}), looks_up and not submit
        if do == "select":
            return ("browser_select_option", {"element": element, "target": ref,
                                              "values": [str(action.get("text") or "")]}), False
        if do == "key":
            pressed = str(action.get("text") or "").strip()
            key = KEYS.get(pressed.lower().replace(" ", ""), pressed)
            if not key:
                raise ToolError("Say which key (Enter, Escape, Tab, PageDown...).")
            if not risky and ((key == "Enter" and not searching) or key == "Delete"):
                raise ToolError(f"Pressing {key} here could send or delete something: use act_risky, which asks "
                                "him first.")
            return ("browser_press_key", {"key": key}), False
        raise ToolError(f"Unknown action {do!r}: click, double_click, type, select, key or back.")


def tools(browser=None, sender=None):
    """The browser tools in his Edge, or none without the extension's token (then she can't reach it); and sending a
    message from Natro's own hidden Edge (sender)."""
    sender = sender or Browser(hidden_edge())

    def send(**args):
        try:
            return sender.send_message(**args)
        finally:
            sender.close()

    sending = PCTool("send_message", "Send a message on Instagram or Messenger from Natro's own hidden Edge on the "
                     "PC, after his yes to the brain's own question (server/natro_agent/sending.py).", send,
                     {key: {"type": "string"} for key in ("app", "text", "name", "chat", "facebook", "username")}
                     | {"group": {"type": "boolean"}}, ["app", "text"], personal=True, confirm=True, hidden=True)
    if browser is None:
        token = os.environ.get(TOKEN, "").strip()
        if not token:
            return [sending]
        browser = Browser(his_edge(token))
    action = {"type": "object", "required": ["do"], "properties": {
        "do": {"type": "string", "enum": ["click", "double_click", "type", "select", "key", "back"]},
        "ref": {"type": "string", "description": "The element's ref from the page (e12)."},
        "text": {"type": "string", "description": "type: what to type. select: the option. key: Enter, Escape, Tab, "
                                                  "PageDown..."},
        "submit": {"type": "boolean", "description": "type: press Enter after (a search box)."},
    }}
    actions = {"actions": {"type": "array", "items": action, "description": "Done in this order."}}
    return [
        PCTool("browser_open", "Open a website in his own Edge on the PC, logged in as him (his accounts), in "
               "Natro's own tabs. Returns the page: its elements with refs, to act on.", browser.open,
               {"url": {"type": "string"}, "new_tab": {"type": "boolean", "description": "Keep the page that's "
                                                                                          "open; default false."}},
               ["url"], personal=True),
        PCTool("browser_page", "Read the page open in his Edge now: its text and elements with refs.", browser.page,
               personal=True),
        PCTool("browser_act", "Do one or more things on the page in his Edge, in order, by ref: click, "
               "double_click, type (submit: press Enter, for a search box), select (an option), key, back. Returns "
               "the page after. Refuses what looks like sending, buying, paying or deleting: use browser_act_risky "
               "for those. On bank and payment sites, only reading.", browser.act, actions, ["actions"],
               personal=True),
        PCTool("browser_act_risky", "The same as browser_act, for a click or key that sends, buys, pays or deletes.",
               lambda actions: browser.act(actions, risky=True), actions, ["actions"], personal=True, confirm=True),
        PCTool("browser_tabs", "Natro's tabs in his Edge: list them, open a new one, switch to one or close one "
               "(by index from list).", browser.tabs,
               {"action": {"type": "string", "enum": ["list", "new", "select", "close"]},
                "index": {"type": "integer"}}, ["action"], personal=True),
        sending,
    ]


if __name__ == "__main__":
    sign_in()
