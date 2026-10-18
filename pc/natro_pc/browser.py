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
"""
import asyncio
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
from contextlib import AsyncExitStack
from pathlib import Path

from natro_pc.control import RISKY
from natro_pc.tools import PCTool, ToolError

PACKAGE = "@playwright/mcp@0.0.83"
TOKEN = "PLAYWRIGHT_MCP_EXTENSION_TOKEN"
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
KEYS = {"enter": "Enter", "escape": "Escape", "esc": "Escape", "tab": "Tab", "backspace": "Backspace",
        "delete": "Delete", "arrowdown": "ArrowDown", "arrowup": "ArrowUp", "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight", "pagedown": "PageDown", "pageup": "PageUp", "home": "Home", "end": "End",
        "space": " "}


def host(url):
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()


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
    """Playwright's MCP server, connected to his Edge through the extension: one at a time, on a thread of its
    own (the helper's tools are called from threads). Started on first use; started again if it stopped."""

    def __init__(self, token):
        self.token = token
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
        args = ["-y", PACKAGE, "--extension", "--codegen", "none", "--output-dir", str(OUTPUT_DIR),
                "--output-max-size", str(OUTPUT_BYTES)]
        if profile := os.environ.get("NATRO_EDGE_PROFILE"):
            args += ["--profile-dir-name", profile]
        server = StdioServerParameters(command=npx, args=args, env={**os.environ, TOKEN: self.token})
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


class Browser:
    """playwright: something with call(name, args) -> text (Playwright, or a fake in tests)."""

    def __init__(self, playwright):
        self.playwright = playwright
        self.url = ""  # the page she's on, as Playwright last said
        self.elements = {}  # ref -> (role, name), from the page as last read

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


def tools(browser=None):
    """The browser tools, or none without the extension's token (then she can't reach his Edge)."""
    if browser is None:
        token = os.environ.get(TOKEN, "").strip()
        if not token:
            return []
        browser = Browser(Playwright(token))
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
    ]
