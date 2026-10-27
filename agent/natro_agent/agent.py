"""Natro's brain: the conversation loop with Claude, tools, confirmations and costs.

There is one conversation at a time for all devices: a follow-up within
SESSION_MINUTES continues it, from any device; after that a new one starts.

Claude's model is picked per request. The much cheaper Haiku answers plain
requests and those about the PC or a screen (see pc_task). Sonnet answers
requests that plainly involve personal data or a risky action (see
personal_or_risky), a picture he sent, the request after a background job or
an alert, and, once one of those came up, the rest of the conversation except
PC requests. Sonnet takes over the rest of a request when Haiku gets stuck. A
request that turns out to use the PC's screen ("join the voice channel in
Discord") moves to Haiku once it does, unless Sonnet has already taken over.
The conversation stays one, whichever answers.

Claude's prompt cache and its thinking blocks are tied to the exact
conversation, so within a Claude conversation the instructions and the tool list
never change and messages are only ever appended. They're the same from one conversation to the next too, whichever
devices are online: an offline device's tools stay listed as it last offered them (calling one returns "offline"),
and while he's about the cached copy is read again before its hour runs out (keep_warm). A background job (jobs.py)
runs on a worker: a copy of the agent with a conversation of its own, beside his requests.
A cancelled request is taken back off the end, so the conversation is as it was
before it (and its cached start still applies).
"""
import asyncio
import copy
import json
import os
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime

import anthropic

from natro_agent import costs, identity, log, pictures, quick, steps
from natro_agent.config import monthly_budget, timezone
from natro_agent.models import CHEAP_MODEL, DEFAULT_MODEL, FALLBACK_MODEL, STRONGER, cost, options
from natro_agent.timing import Timing
from natro_agent.tools import KnownTools, ToolError, WithPictures, result_text

# A follow-up within this time continues the conversation (it matches how long
# Claude keeps the conversation in its prompt cache; the instructions and tools stay an hour).
SESSION_MINUTES = 5
# Model calls per request, so a confused tool loop can't run up costs. Using an app on the phone by its
# screen takes a step per tap.
MAX_STEPS = 20
# When a model with a STRONGER one counts as stuck: its tools failed this many steps in a row, or the request
# has taken this many steps.
STUCK_FAILURES = 2
STUCK_STEPS = 10
# The PC's screen tools (pc/natro_pc/control.py): clicking through an app takes many steps, each with a whole
# screen in it, so a request that uses them goes on with the cheaper model.
PC_SCREEN_TOOLS = {"screen", "find", "act", "act_risky", "switch_window", "wait_for", "browser_open", "browser_page",
                   "browser_act", "browser_act_risky", "browser_tabs"}
MAX_TOKENS = 16000
TOOL_SECONDS = 60
CONFIRM_SECONDS = 60
# More changes than this in one request (events, tasks, notes added or changed) ask the owner once first.
MANY_CHANGES = 5
# Longer tool results are cut, to keep requests small.
RESULT_CHARS = 20000
# Both models' cached instructions are kept from running out (an hour after they were last read) for this long
# after his last request, while it's between these hours his time, by reading them again WARM_EVERY minutes after
# the last read. On his requests of 20-26 Oct it would have left 8 of 217 starting cold instead of 42, and the
# cache's cost at $0.18 a day instead of $0.35.
WARM_FOR_HOURS = 6
WARM_FROM, WARM_UNTIL = 8, 1  # 08:00 to 01:00
WARM_EVERY_MINUTES = 55
# A picture he sends goes with his next request if it comes within this time (he sent it to ask about it).
PICTURE_MINUTES = 10
MOST_PICTURES = 4
# NATRO_CHECK_HISTORY=1 makes Claude reject any edit to a past conversation (see models.options).
CHECK_HISTORY = os.environ.get("NATRO_CHECK_HISTORY") == "1"
TOKEN_KINDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")

# How a tool that asks in its own words (Tool.own_question: send_message) asks the owner: await ASK.get()(question,
# details) is his yes, asked as any question of the request (or the job) running it is. Set for each tool call.
ASK = ContextVar("ask", default=None)

# Words that mark a request as personal or risky: it goes to Sonnet (unless it's about the PC or a screen).
# Broad on purpose: a wrong guess here only costs a little money.
PERSONAL_HINTS = re.compile(
    r"\b(remember|forget|remind|reminder|memory|note|notes|to-?do|tasks?|shopping|groceries|grocery|my lists?|e-?mails?|gmail|inbox|mail|calendar|"
    r"brief|briefing|my day|background|jobs?|working on|watchers?|watching|alerts?|heads-?up|keep an eye|"
    r"routines?|every (?:morning|evening|night|day|weekday|weekend|week|month|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday)s?|"
    r"convert|conversion|i asked|did i ask|told me|did you (?:tell|say)|you said|we talked|last time|history|"
    r"travel|how long to|how long does it take|leave for|drive to|by bus|by metro|routes?|directions|navigat\w*|"
    r"get to|metro|bus|buses|in (?:google )?maps|on (?:the )?map|location|where am i|where i am|from here|"
    r"schedule|meeting|appointment|events?|files?|documents?|docs?|drive|sheets?|spreadsheets?|folders?|photos?|"
    r"messages?|sms|texts?|whatsapp|messenger|telegram|viber|repl(?:y|ies)|instagram|insta|dms?|"
    r"notifications?|contacts?|call|delete|remove|send|share|pay|password|bank|address|about me|my name|"
    # What's on a screen and what a command says are personal too.
    r"screens?|screenshot|click|scroll|commands?|powershell|terminal|ipconfig|dark mode|light mode|brightness|"
    r"bluetooth|wi-?fi|ip address|disk space|battery)\b",
    re.IGNORECASE)

# Words that mark a request as using the PC or a screen: commands, settings, apps, windows, clicking. These
# start on the CHEAP_MODEL, even when they're personal.
PC_TASK_HINTS = re.compile(
    r"\b(pc|computer|laptop|desktop|screens?|screenshot|windows?|apps?|application|programs?|commands?|"
    r"powershell|terminal|ipconfig|click|scroll|browser|tabs?|settings|dark mode|light mode|brightness|"
    r"bluetooth|wi-?fi|ip address|disk space|battery)\b", re.IGNORECASE)


def personal_or_risky(text):
    """Whether a request plainly involves personal data or a risky action."""
    return bool(PERSONAL_HINTS.search(text))


def pc_task(text):
    """Whether a request is about the PC or a screen (an app, a window, a command, a setting)."""
    return bool(PC_TASK_HINTS.search(text))


def noise(text, in_conversation=False):
    """Whether a request is only noise the voice pipeline wrote down ("1 1 1 1 1", "...", "a"): no model gets it.

    A number alone is kept in a conversation, as it may answer her question ("Which one?" "2"), unless it's
    the same one over and over.
    """
    words = re.findall(r"\w+", text)
    if any(char.isalpha() for char in text):
        return sum(len(word) for word in words) < 2
    if not words:
        return True
    return not in_conversation or (len(words) > 1 and len(set(words)) == 1)


def utc_offset(moment):
    """A time's offset from UTC as people write it: "UTC+4", "UTC+5:30", "UTC-3" or "UTC"."""
    minutes = round(moment.utcoffset().total_seconds() / 60)
    if not minutes:
        return "UTC"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{'+' if minutes > 0 else '-'}{hours}" + (f":{rest:02d}" if rest else "")


def plain_question(name, args):
    """The question before a risky tool when the model wrote no sentence first: what it will do, in plain words."""
    what = steps.describe(name, args)["text"]
    where = {"pc": " on the PC", "phone": " on the phone"}.get(name.partition("_")[0], "")
    details = [str(value) for value in args.values()
               if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip()
               and str(value).casefold() not in what.casefold()]
    details = f": {steps.short(', '.join(details), 60)}" if details else ""
    return f"{what}{where}{details}. Should I go ahead?"


@dataclass
class Reply:
    text: str
    model: str
    cost: float = 0.0
    seconds: float = 0.0
    tool_calls: list = field(default_factory=list)
    # Tokens over all Claude calls, to see what caching saves.
    tokens: dict = field(default_factory=lambda: dict.fromkeys(TOKEN_KINDS, 0))


@dataclass
class Warmth:
    """When each model last read Natro's instructions (time.monotonic() at a call's start: any call reads them, or
    writes them if they weren't cached), and when he last asked something. The workers (jobs) share it."""
    used: dict = field(default_factory=dict)
    asked: float = None


@dataclass
class Changes:
    """The owner's items one request has added or changed so far, and whether he allowed many."""
    made: int = 0
    allowed: bool = False


class Session:
    """A conversation with Claude: fixed instructions and tools, and messages that only grow."""

    def __init__(self, model, tools, memory=None):
        self.last_used = time.monotonic()
        self.model = model  # the model answering the request now
        self.failed = set()  # models whose API failed in this conversation: their fallback answers instead
        # A request in it was personal or risky (or came with a picture or a job's or alert's note): Sonnet
        # answers the rest of it, but for PC requests.
        self.personal = False
        self.tools = {tool.name: tool for tool in tools}
        self.tool_definitions = [tool.definition() for tool in sorted(tools, key=lambda tool: tool.name)]
        # The cache markers let the next conversation reuse the cached instructions and tools, for an hour: most
        # conversations start within an hour of the one before, and writing the ~25,000 tokens again each time
        # was most of the spending (2026-10-20). An hour's write costs 2x the input price (5 minutes': 1.25x);
        # a read 0.1x. What she remembers has a marker of its own after them, so a fact remembered or forgotten
        # writes only itself again. The conversation itself is cached for 5 minutes (_stream), after these.
        an_hour = {"type": "ephemeral", "ttl": "1h"}
        self.system = [{"type": "text", "text": identity.system_prompt(tools), "cache_control": an_hour}]
        if memory is not None:
            self.system.append({"type": "text", "text": identity.memory_prompt(memory), "cache_control": an_hour})
        self.messages = []

    @property
    def expired(self):
        return time.monotonic() - self.last_used > SESSION_MINUTES * 60


def echo_content(content):
    """The assistant turn to send back with the next request.

    Unchanged, thinking blocks included (Claude needs them exactly as they were),
    except when the API switched to a fallback model partway through the reply:
    then the declined model's thinking and tool calls before the switch are left
    out, as the API requires. The switch marker itself is dropped too.
    """
    switches = [i for i, block in enumerate(content) if block.type == "fallback"]
    if not switches:
        return list(content)
    boundary = switches[-1]
    return [block for block in content[:boundary] if block.type == "text"] + list(content[boundary + 1:])


def call_key(block):
    """A tool call's name and arguments, to find the same call twice."""
    return block.name, json.dumps(block.input, sort_keys=True, default=str)


async def repeated(block):
    """The tool_result for a call made twice in one step: the first one ran."""
    return {"type": "tool_result", "tool_use_id": block.id, "is_error": False,
            "content": "Not done again: the very same call is in this step already, and that one ran."}


def quick_turn(request, name, args, result, said, offered):
    """A quick command (run with no model) as Claude's conversation keeps it: the call and its result, then her
    words. Her words alone ("Paused.", with no call before them) read to Claude as a claim she never acted on,
    and she'd "correct" it later by doing it again (2026-10-20). offered: the tool is in the conversation's list
    (a call to one that isn't would be refused)."""
    if not offered:
        return [{"role": "user", "content": request},
                {"role": "assistant", "content": f"{said} [ran {name} {json.dumps(args)}]"}]
    call = f"toolu_quick_{int(time.time() * 1000):x}"
    return [{"role": "user", "content": request},
            {"role": "assistant", "content": [{"type": "tool_use", "id": call, "name": name, "input": args}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call, "content": result}]},
            {"role": "assistant", "content": said}]


def with_pictures(request, sent):
    """A request with the pictures he sent for it: the pictures first, then his words, whose first line says
    they're there."""
    if not sent:
        return request
    devices = sorted({label for _, label in sent})
    note = (f"he sent {'a picture' if len(sent) == 1 else f'{len(sent)} pictures'} into the chat from his "
            f"{' and '.join(devices)} just now, for this request: {'it is' if len(sent) == 1 else 'they are'} here")
    header, _, rest = request.partition("\n")
    words = f"{header.removesuffix(']')} · {note}]\n{rest}" if header.startswith("[") else f"[{note}]\n{request}"
    return [*(block for block, _ in sent), {"type": "text", "text": words}]


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else text


def text_of(content):
    return "".join(block.text for block in content if block.type == "text").strip()


class Agent:
    """memory: a Memory (its contents go into Claude's instructions).

    model answers personal or risky requests (personal_or_risky); cheap_model answers plain ones and those about
    the PC or a screen (pc_task).
    """

    def __init__(self, tools=(), model=DEFAULT_MODEL, client=None, budget=None, memory=None,
                 cheap_model=CHEAP_MODEL, known_tools=None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model
        self.cheap_model = cheap_model
        self.own_tools = list(tools)  # Natro's own and the MCP servers'
        self.memory = memory
        self.devices = {}  # name -> Device, while connected
        self.known_tools = known_tools or KnownTools()  # what his devices offered, for while they're offline
        self.warmth = Warmth()
        # What happened since the last request that Natro should know (a picture he sent): told with the next one.
        self.notes = []
        # The same, but personal (a reminder that went off): told with the next request a model answers (a quick
        # command doesn't take them).
        self.private_notes = []
        # Pictures he sent into the chat, for his next request: (when, Claude's image block, the device's label).
        self.pictures = []
        self._on_step = lambda step: None  # the request being handled's (one at a time: _busy)
        self._asked = []  # the questions asked so far in the request being handled
        self._done = []  # what its tools did so far (told to Claude if it's cancelled)
        self._timing = Timing()  # how long its parts take (timing.py)
        # A background job finished or an alert was shown: the next request counts as personal (Sonnet answers it),
        # and is told what it found (private_notes).
        self.personal_next = False
        self.max_steps = MAX_STEPS
        self.session = None
        self.ledger = costs.Ledger(monthly_budget() if budget is None else budget)
        self._busy = asyncio.Lock()  # one request at a time
        self._asking = asyncio.Lock()  # one confirmation question at a time

    def connect(self, device):
        self.devices[device.name] = device
        if device.name in identity.KNOWN_DEVICES:
            self.known_tools.update(device)

    def worker(self, max_steps=MAX_STEPS):
        """An agent for a background job (jobs.py): the same tools, devices, memory, models and spending, with a
        conversation of its own, so it runs beside his requests. Its request counts as personal (Sonnet, or Haiku
        for a PC task): a job is long work, often with his data. Its questions wait their turn with those of his
        requests (one question at a time)."""
        worker = copy.copy(self)
        worker.session = None
        worker.personal_next = True
        worker.notes, worker.private_notes, worker.pictures = [], [], []
        worker._busy = asyncio.Lock()
        worker._on_step, worker._asked, worker._done, worker._timing = (lambda step: None), [], [], Timing()
        worker.max_steps = max_steps
        return worker

    def picture_sent(self, mime, data, label):
        """He sent a picture into the chat (from his device with that label): Claude sees it with his next
        request. False if it isn't a picture that can be read."""
        block = pictures.for_model(mime, data)
        if block is None:
            return False
        self.pictures = (self.pictures + [(time.monotonic(), block, label)])[-MOST_PICTURES:]
        return True

    def _take_pictures(self):
        """The pictures he sent lately, for this request (they go with it, and no further)."""
        fresh = [(block, label) for when, block, label in self.pictures
                 if time.monotonic() - when <= PICTURE_MINUTES * 60]
        self.pictures = []
        return fresh

    def new_conversation(self):
        """New chat on a device: the next request starts a new conversation (one under way finishes)."""
        self.session = None

    def disconnect(self, device):
        if self.devices.get(device.name) is device:
            del self.devices[device.name]

    def offered_tools(self):
        """Every tool on offer: Natro's own, each of his devices' (an offline one's as it last offered them, so the
        list is the same whichever are online), and any other connected device's (a test one)."""
        tools = list(self.own_tools)
        for name, label in identity.KNOWN_DEVICES.items():
            device = self.devices.get(name)
            listed = (device.tools() if device else []) or self.known_tools.tools(name)
            tools += [replace(tool, run=self._on_device(name, label, tool)) for tool in listed]
        for device in self.devices.values():
            if device.name not in identity.KNOWN_DEVICES:
                tools += [replace(tool, run=self._on_device(device.name, device.label, tool)) for tool in device.tools()]
        return tools

    def _on_device(self, name, label, tool):
        """Runs tool on the device called name as it is connected when it's called (its app may have restarted, or
        the network blinked, since the tool was listed: its tool of the same name)."""
        async def run_on_device(args):
            current = self.devices.get(name)
            if current is None:
                raise ToolError(f"Your owner's {label} is offline right now: queue_for_device keeps the request "
                                "for when it's back.")
            same = next((t for t in current.tools() if t.name == tool.name), None)
            if same is None:
                raise ToolError(f"Your owner's {label} no longer offers {tool.name}.")
            return await same.run(args)
        return run_on_device

    async def respond(self, text, device, on_text=None, voice_cost=0.0, on_step=None, on_start=None, context="",
                      timing=None):
        """Handle one request (English text) from a device and return Natro's reply.

        on_text receives what she writes in pieces as it is written, including her words before and between
        tool calls (a question she asked, what she's doing); the reply is only her words after her last tool
        call, and devices show it in place of the pieces. on_step receives what she's
        doing as each tool starts (steps.describe: {"text": "Opening Spotify", ...}). voice_cost is what
        recognizing and translating the request cost, for the monthly total. on_start is called once she
        turns to it (she handles one request at a time; others wait). context: a line from the system put
        before the request (a background job's), not used to route it. timing: a Timing (timing.py) to fill with
        how long its parts take.

        Cancelling the task that runs this (the owner cancelled on the device) stops the request where
        it is and leaves the conversation as it was before it.
        """
        on_text = on_text or (lambda piece: None)
        waiting = time.monotonic()
        self.warmth.asked = waiting
        async with self._busy:
            self._timing = timing or Timing()
            self._timing.wait = time.monotonic() - waiting
            if on_start:
                on_start()
            self._on_step = on_step or (lambda step: None)
            self._asked, self._done = [], []
            started = time.monotonic()
            before = self._mark()
            try:
                reply = await self._respond(text, device, on_text, context)
            except asyncio.CancelledError:
                self._rewind(before)
                if self._done:
                    # The conversation forgets the request, but what it did is done: Claude hears of it, so a
                    # "go on" or "try again" doesn't do it twice (personal: only Claude hears it).
                    self.private_notes.append(f"he stopped his request \"{text[:200]}\" partway, so it isn't in "
                                              f"the conversation; before that it had already done: "
                                              f"{'; '.join(self._done)}")
                self.ledger.add(voice_cost)
                log.write("request", device=device.name, text=text, cancelled=True, voice_cost=round(voice_cost, 6),
                          seconds=round(time.monotonic() - started, 2))
                raise
            except anthropic.APIError as e:
                log.write("error", device=device.name, text=text, error=repr(e))
                reply = Reply("Sorry, I can't reach Claude right now, so I can't do that. Please try again in a bit.",
                              model=self.model)
                on_text(reply.text)
            reply.seconds = time.monotonic() - started
            if self.ledger.add(reply.cost + voice_cost):
                note = (f"By the way, this month's spending has passed {costs.WARN_AT:.0%} "
                        f"of the {self.ledger.budget:g} dollar budget.")
                reply.text = f"{reply.text} {note}".strip()
                on_text(f" {note}")
            log.write("request", device=device.name, text=text, reply=reply.text, model=reply.model,
                      cost=round(reply.cost, 6), voice_cost=round(voice_cost, 6), tokens=reply.tokens,
                      seconds=round(reply.seconds, 2), tools=[call["tool"] for call in reply.tool_calls])
            return reply

    async def _respond(self, text, device, on_text, context=""):
        if noise(text, in_conversation=self.session is not None and not self.session.expired):
            reply = Reply("I didn't catch that.", "none (noise)")
            on_text(reply.text)
            return reply
        request = "\n".join(line for line in (self._header(device), context, text) if line)
        if (reply := await self._quick(text, request, device, on_text)) is not None:
            return reply
        sent = self._take_pictures()
        if self.session is None or self.session.expired:
            self.session = self._claude_session()
        if personal_or_risky(text) or self.personal_next or sent:
            self.session.personal = True  # Sonnet keeps the rest of the conversation, as it knows it
        self.personal_next = False
        model = self.cheap_model if pc_task(text) or not self.session.personal else self.model
        return await self._claude_turn(self.session, with_pictures(self._with_private_notes(request), sent), device,
                                       on_text, quick.simple(text), model)

    async def _quick(self, text, request, device, on_text):
        """A common short command run without a model (see quick.py), or None if it isn't one or didn't work."""
        in_conversation = self.session is not None and not self.session.expired
        found = quick.command(text, in_conversation, from_pc=device.name == "pc")
        tools = {tool.name: tool for tool in self.offered_tools()}
        if found is None or found[0] not in tools:
            return None
        name, args = found
        reply = Reply("", "none (quick)")
        with self._timing.step():
            content, failed, _ = await self._call_tool(tools, name, args, device, "", reply)
        if failed:
            return None  # a model may know better (say, the phone's media rather than Spotify)
        reply.text = first_line(content)
        on_text(reply.text)
        # Keep the conversation whole, for follow-ups.
        if self.session is not None and in_conversation:
            self.session.messages += quick_turn(request, name, args, content, reply.text, name in self.session.tools)
            self.session.last_used = time.monotonic()
        return reply

    def _mark(self):
        """Where the conversation stands now, to go back to if a request is cancelled."""
        session = self.session
        return session, len(session.messages) if session else 0

    def _rewind(self, mark):
        """The conversation as it was at mark: a cancelled request's turns taken off its end."""
        session, length = mark
        self.session = session
        if session is not None:
            del session.messages[length:]

    def _claude_session(self):
        memory = self.memory.prompt_section() if self.memory else None
        return Session(self.model, self.offered_tools(), memory)

    async def _claude_turn(self, session, request, device, on_text, simple=False, model=None):
        if self.ledger.over_budget and not await self._ask(
                device, f"This month's {self.ledger.budget:g} dollar budget is used up. Use Claude anyway?",
                f"spent this month: ${self.ledger.spent:.2f}"):
            return Reply("OK, I won't use Claude for that.", model="none")

        session.messages.append({"role": "user", "content": request})
        session.model = self._usable(session, model or self.model)
        reply = Reply("", session.model)
        said = []  # everything she wrote, step by step
        last = 0  # where her words after her last tool call start in said: her reply
        changes = Changes()
        failing = 0  # steps in a row whose tools all failed
        taken_over = False  # a stronger model took over this request: it keeps it
        for step in range(self.max_steps):
            if session.model in STRONGER and (failing >= STUCK_FAILURES or step >= STUCK_STEPS):
                self._take_over(session, "its tools kept failing" if failing >= STUCK_FAILURES else "many steps")
                taken_over = True
            response, model = await self._call(session, self._spaced(on_text, bool(said)))
            reply.cost += cost(model, response.usage)
            for kind in TOKEN_KINDS:
                reply.tokens[kind] += getattr(response.usage, kind) or 0
            content = echo_content(response.content)
            if response.stop_reason == "refusal":
                if session.model in STRONGER:
                    self._take_over(session, "it declined")
                    taken_over = True
                    continue  # the stronger model answers the same request (nothing was added to it)
                said, last = ["Sorry, I can't help with that one."], 0
                self.session = None  # don't carry a declined request into follow-ups
                break
            if text_of(content):
                said.append(text_of(content))
            if response.stop_reason == "max_tokens":
                self.session = None  # the reply was cut off, maybe mid tool call
                break
            if content:
                session.messages.append({"role": "assistant", "content": content})
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [block for block in content if block.type == "tool_use"]
            if not tool_uses:
                break
            # Natro's sentence before a risky tool (or before many changes) is the confirmation question.
            declined = await self._many_changes(changes, session.tools, tool_uses, device, text_of(content))
            # The same call twice in one step runs once: two taps on the same element at once would tap twice.
            first = {}
            for block in tool_uses:
                first.setdefault(call_key(block), block.id)
            with self._timing.step():
                results = await asyncio.gather(*(self._use_tool(session, block, device, text_of(content), reply,
                                                                declined=block.id in declined)
                                                 if first[call_key(block)] == block.id else repeated(block)
                                                 for block in tool_uses))
            session.messages.append({"role": "user", "content": list(results)})
            last = len(said)
            failing = failing + 1 if all(result["is_error"] for result in results) else 0
            if not taken_over and session.model == self.model != self.cheap_model and                     self._uses_pc_screen(session, tool_uses):
                cheaper = self._usable(session, self.cheap_model)
                log.write("cheaper_model", model=session.model, switched_to=cheaper, reason="the PC's screen")
                session.model = cheaper
            if simple and step == 0 and not said and len(results) == 1 and not results[0]["is_error"] \
                    and getattr(session.tools.get(tool_uses[0].name), "final", False):
                # The tool's result says it all ("Paused."): no second model call to word it.
                said, last = [first_line(results[0]["content"])], 0
                on_text(said[0])
                session.messages.append({"role": "assistant", "content": said[0]})
                break
        else:
            said.append("I stopped there: that was taking too many steps.")
        session.last_used = time.monotonic()
        reply.text = self._final_words(said, last)
        reply.model = session.model
        return reply

    def _final_words(self, said, last):
        """Her reply: what she wrote after her last tool call.

        Her earlier words were the question she asked before a risky tool (he has heard it), or what she was
        doing between steps; they showed while she wrote them, and the reply takes their place. If she wrote
        nothing after her last tool call, her latest earlier words that weren't a question are the reply.
        """
        if said[last:]:
            return " ".join(said[last:])
        earlier = [words for words in said if words not in self._asked] or said
        return earlier[-1] if earlier else ""

    @staticmethod
    def _uses_pc_screen(session, tool_uses):
        for block in tool_uses:
            tool = session.tools.get(block.name)
            if tool is not None and tool.source == "pc" and block.name.removeprefix("pc_") in PC_SCREEN_TOOLS:
                return True
        return False

    def _take_over(self, session, why):
        """The stronger model answers the rest of this request (the conversation is unchanged)."""
        stronger = self._usable(session, STRONGER[session.model])
        log.write("stronger_model", model=session.model, switched_to=stronger, reason=why)
        session.model = stronger

    @staticmethod
    def _usable(session, model):
        """model, unless its API already failed in this conversation: then its fallback."""
        return FALLBACK_MODEL.get(model, model) if model in session.failed else model

    def _header(self, device):
        """The first line of each request: when (with the UTC offset, for other time zones), from where, and
        which devices are online."""
        now = datetime.now(timezone())
        online = [d.label for name, d in self.devices.items() if name in identity.KNOWN_DEVICES]
        notes, self.notes = "".join(f" · {note}" for note in self.notes), []
        return (f"[{now:%A %d %B %Y, %H:%M} {utc_offset(now)} · from the {device.label} · "
                f"online: {', '.join(online) or 'no devices'}{notes}]")

    def _with_private_notes(self, request):
        """The request with the personal notes added to its first line, for Claude only."""
        if not self.private_notes:
            return request
        notes, self.private_notes = "".join(f" · {note}" for note in self.private_notes), []
        header, _, rest = request.partition("\n")
        return f"{header.removesuffix(']')}{notes}]\n{rest}"

    @staticmethod
    def _spaced(on_text, after_earlier_text):
        """on_text, with a space before the first piece if an earlier step already wrote something."""
        pending = [after_earlier_text]

        def show(piece):
            if pending[0]:
                on_text(" ")
                pending[0] = False
            on_text(piece)
        return show

    async def _call(self, session, on_text):
        """One model call. If the model's API fails, the rest of the session moves to its fallback model."""
        try:
            return await self._stream(session, session.model, on_text), session.model
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            fallback = FALLBACK_MODEL.get(session.model)
            retryable = isinstance(e, anthropic.APIConnectionError) or e.status_code == 429 or e.status_code >= 500
            if not (fallback and retryable):
                raise
            log.write("model_error", model=session.model, error=repr(e), switched_to=fallback)
            session.failed.add(session.model)
            session.model = fallback
            return await self._stream(session, fallback, on_text), fallback

    async def _stream(self, session, model, on_text):
        request = dict(model=model, max_tokens=MAX_TOKENS, system=session.system, messages=session.messages,
                       # Also cache the conversation so far, for the next step and follow-ups.
                       cache_control={"type": "ephemeral"}, **options(model, CHECK_HISTORY))
        if session.tool_definitions:
            request["tools"] = session.tool_definitions
        started, first_token = time.monotonic(), None
        self.warmth.used[model] = started
        async with self.client.beta.messages.stream(**request) as stream:
            async for event in stream:
                if first_token is None and event.type == "content_block_start":
                    first_token = time.monotonic() - started  # its thinking or words start: the prompt is read
                if event.type == "text":
                    on_text(event.text)
            response = await stream.get_final_message()
        self._timing.call(model, response.usage, first_token, time.monotonic() - started)
        return response

    def warm_due(self, now=None, local=None):
        """The models whose cached instructions should be read now, so they don't run out while he's about (see
        WARM_FOR_HOURS): none when he hasn't asked anything for that long, at night, or over the budget."""
        now = time.monotonic() if now is None else now
        local = local or datetime.now(timezone())
        asked = self.warmth.asked
        if asked is None or now - asked > WARM_FOR_HOURS * 3600 or self.ledger.over_budget:
            return []
        if WARM_UNTIL <= local.hour < WARM_FROM:  # the hours run past midnight
            return []
        return [model for model in dict.fromkeys((self.model, self.cheap_model))
                if now - self.warmth.used.get(model, float("-inf")) >= WARM_EVERY_MINUTES * 60]

    async def warm(self, model):
        """Read the instructions and tools a new conversation would start with on model (writing them if they
        weren't cached), so they stay cached another hour. max_tokens 0: nothing is written back, only the prompt
        read; thinking and effort as a real request's, as they're part of what's cached."""
        session = self._claude_session()
        request = dict(model=model, max_tokens=0, system=session.system,
                       messages=[{"role": "user", "content": "warmup"}], **options(model, CHECK_HISTORY))
        if session.tool_definitions:
            request["tools"] = session.tool_definitions
        self.warmth.used[model] = time.monotonic()
        response = await self.client.beta.messages.create(**request)
        spent = cost(model, response.usage)
        self.ledger.add(spent)
        log.write("warm", model=model, cost=round(spent, 6),
                  tokens={kind: getattr(response.usage, kind) or 0 for kind in TOKEN_KINDS})

    async def keep_warm(self):
        """Until cancelled: each minute, warms the models warm_due names."""
        while True:
            await asyncio.sleep(60)
            for model in self.warm_due():
                try:
                    await self.warm(model)
                except anthropic.APIError as e:
                    log.write("model_error", model=model, error=repr(e), warming=True)

    async def _many_changes(self, changes, tools, tool_uses, device, question):
        """Ask once before a request's changes go past MANY_CHANGES. Returns the ids of the calls he declined."""
        changing = [block for block in tool_uses if getattr(tools.get(block.name), "changes", False)]
        if not changing:
            return set()
        if not changes.allowed and changes.made + len(changing) > MANY_CHANGES:
            counts = {}
            for block in changing:
                counts[block.name] = counts.get(block.name, 0) + 1
            details = ", ".join(f"{name} x{count}" for name, count in counts.items())
            changes.allowed = await self._ask(device, question or f"Should I make {len(changing)} changes?",
                                              f"{len(changing)} changes: {details}")
            if not changes.allowed:
                return {block.id for block in changing}
        changes.made += len(changing)
        return set()

    async def _use_tool(self, session, block, device, question, reply, declined=False):
        """Run one of Claude's tool calls and return its tool_result."""
        args = block.input if isinstance(block.input, dict) else {}
        content, is_error, shown = await self._call_tool(session.tools, block.name, args, device, question, reply,
                                                         declined)
        seen = [image for image in (pictures.for_model(mime, data) for mime, data in shown) if image]
        if seen:  # a screenshot: she sees it with the tool's words
            content = [{"type": "text", "text": content}, *seen]
        return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}

    async def _call_tool(self, tools, name, args, device, question, reply, declined=False):
        """Run one tool call, asking first if it needs a yes. Returns (text for the model, whether it failed, the
        pictures it gave as (mime, bytes) pairs).

        declined: the owner already said no to it (as one of many changes).
        """
        tool = tools.get(name)
        if tool is None:
            # Models sometimes get the letter case of a name wrong.
            tool = next((t for known, t in tools.items() if known.lower() == name.lower()), None)
        call = {"tool": name, "args": args}
        shown = []
        if tool is None:
            content, is_error = f"There is no tool named {name!r}.", True
        elif declined or tool.confirm and not tool.own_question and not await self._ask(
                device, question or plain_question(tool.name, args),
                f"{tool.name} {json.dumps(args, ensure_ascii=False)}"):
            content, is_error = "Your owner said no, so this was not done.", False
            call["declined"] = True
        else:
            self._show_step(tool.name, args)
            ASK.set(lambda question, details: self._ask(device, question, details))
            began, asked = time.monotonic(), self._timing.asking
            content, is_error, shown = await self._run(tool, args)
            # Without waiting for his yes, when it asked in its own words.
            self._timing.tool(tool.name, tool.source, time.monotonic() - began - (self._timing.asking - asked),
                              not is_error)
            if not is_error:
                self._done.append(f"{tool.name} {json.dumps(args, ensure_ascii=False)[:160]}: "
                                  f"{first_line(content)[:160]}")
        call.update(ok=not is_error, result=content[:500])
        log.write("tool", device=device.name, **call)
        reply.tool_calls.append(call)
        if len(content) > RESULT_CHARS:
            content = f"{content[:RESULT_CHARS]}\n[cut here: the whole result was {len(content)} characters]"
        return content, is_error, shown

    def _show_step(self, name, args):
        try:
            self._on_step(steps.describe(name, args))
        except Exception as e:  # a step is only for show: never in the way of the tool
            log.write("error", step="describe", tool=name, error=repr(e))

    async def _run(self, tool, args):
        """Returns (text for the model, whether it failed, its pictures)."""
        # A tool that asks in its own words: its question's time too, and its tries on another device (send_message).
        seconds = TOOL_SECONDS + (CONFIRM_SECONDS + 2 * TOOL_SECONDS if tool.own_question else 0)
        try:
            result = await asyncio.wait_for(tool.run(args), seconds)
        except ToolError as e:
            return str(e), True, []
        except asyncio.TimeoutError:
            return f"{tool.name} took longer than {seconds} seconds and was stopped.", True, []
        except Exception as e:
            return f"{tool.name} failed: {type(e).__name__}: {e}", True, []
        return result_text(result), False, list(result.pictures) if isinstance(result, WithPictures) else []

    async def _ask(self, device, question, details):
        """Ask the owner on the device; no answer within CONFIRM_SECONDS counts as no."""
        self._asked.append(question)
        with self._timing.asked():
            async with self._asking:
                try:
                    return await asyncio.wait_for(device.confirm(question, details), CONFIRM_SECONDS)
                except asyncio.TimeoutError:
                    return False
