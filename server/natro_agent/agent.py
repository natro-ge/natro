"""Natro's brain: the conversation loop with Claude, the choice of model, and costs.

Running her tools and asking first are in tool_calls.py, and keeping the cache
warm in warm.py; Agent takes both as parts of itself. The words that route a
request are in routing.py.

There is one conversation at a time for all devices: a follow-up within
SESSION_MINUTES continues it, from any device; after that a new one starts.

Claude's model is picked per request. The much cheaper Haiku answers plain
requests and those about the PC or a screen (see pc_task). Sonnet answers
requests that plainly involve personal data or a risky action (see
personal_or_risky), what he types in Georgian, about the PC or not (see georgian),
a picture he sent, the request after a background job or an alert, and, once one
of those came up, the rest of the conversation except PC requests. Sonnet takes
over the rest of a request when Haiku gets stuck. A request that turns out to use
the PC's screen ("join the voice channel in Discord") moves to Haiku once it does,
unless Sonnet has already taken over or it's in Georgian.
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
import time
from dataclasses import dataclass, field, replace
from datetime import datetime

import anthropic

from natro_agent import costs, identity, log, pictures, quick, this
from natro_agent.config import monthly_budget, timezone
from natro_agent.models import (CHEAP_MODEL, CHECK_HISTORY, DEFAULT_MODEL, FALLBACK_MODEL, STRONGER, TOKEN_KINDS, cost,
                                options)
from natro_agent.routing import georgian, noise, pc_task, personal_or_risky, says_this
from natro_agent.timing import Timing
from natro_agent.tool_calls import Changes, ToolCalls, call_key, first_line, repeated
from natro_agent.tools import KnownTools, ToolError
from natro_agent.warm import Warming, Warmth
from natro_agent.works_on import header_note, switched_to, works_where

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
# The PC's screen and browser tools (desktop/src-tauri/src/tools/screen.rs and browser.rs): clicking through an app or
# a page takes many steps, each with a whole screen in it, so a request that uses them goes on with the cheaper model.
PC_SCREEN_TOOLS = {"screen", "find", "act", "act_risky", "switch_window", "wait_for", "browser_open", "browser_page",
                   "browser_act", "browser_act_risky", "browser_tabs"}
MAX_TOKENS = 16000
# A picture he sends goes with his next request if it comes within this time (he sent it to ask about it).
PICTURE_MINUTES = 10
MOST_PICTURES = 4


def utc_offset(moment):
    """A time's offset from UTC as people write it: "UTC+4", "UTC+5:30", "UTC-3" or "UTC"."""
    minutes = round(moment.utcoffset().total_seconds() / 60)
    if not minutes:
        return "UTC"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{'+' if minutes > 0 else '-'}{hours}" + (f":{rest:02d}" if rest else "")


@dataclass
class Reply:
    text: str
    model: str
    cost: float = 0.0
    seconds: float = 0.0
    tool_calls: list = field(default_factory=list)
    # Tokens over all Claude calls, to see what caching saves.
    tokens: dict = field(default_factory=lambda: dict.fromkeys(TOKEN_KINDS, 0))


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


def text_of(content):
    return "".join(block.text for block in content if block.type == "text").strip()


class Agent(ToolCalls, Warming):
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
        turns to it (she handles one request at a time; others wait). context: a line from the system put before
        the request (a background job's, the files he attached), not used to route it. timing: a Timing (timing.py) to fill with
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
            # Made on a device whose switch points at the other one (works_on.py): for the dashboard.
            where = {"works_on": switched_to(device)} if switched_to(device) else {}
            try:
                reply = await self._respond(text, device, on_text, context)
            except asyncio.CancelledError:
                self._rewind(before)
                if self._done:
                    # The conversation forgets the request, but what it did is done: Claude hears of it, so a
                    # "go on" or "try again" doesn't do it twice.
                    self.private_notes.append(f"he stopped his request \"{text[:200]}\" partway, so it isn't in "
                                              f"the conversation; before that it had already done: "
                                              f"{'; '.join(self._done)}")
                self.ledger.add(voice_cost)
                log.write("request", device=device.name, text=text, cancelled=True, voice_cost=round(voice_cost, 6),
                          seconds=round(time.monotonic() - started, 2), **where)
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
                      seconds=round(reply.seconds, 2), tools=[call["tool"] for call in reply.tool_calls], **where)
            return reply

    async def _respond(self, text, device, on_text, context=""):
        if noise(text, in_conversation=self.session is not None and not self.session.expired):
            reply = Reply("I didn't catch that.", "none (noise)")
            on_text(reply.text)
            return reply
        header = self._header(device)
        request = "\n".join(line for line in (header, context, text) if line)
        if (reply := await self._quick(text, request, device, on_text)) is not None:
            return reply
        sent = self._take_pictures()
        # "This" is what he attached or sent, or what a job's task says, when it has any of those.
        if says_this(text) and not context and not sent:
            with self._timing.step():
                seen = await this.in_front(device, self.devices, self._timing)
            request = "\n".join(line for line in (header, seen, text) if line)
        if self.session is None or self.session.expired:
            self.session = self._claude_session()
        in_georgian = georgian(text)
        if personal_or_risky(text) or in_georgian or self.personal_next or sent:
            self.session.personal = True  # Sonnet keeps the rest of the conversation, as it knows it
        self.personal_next = False
        model = self.cheap_model if (pc_task(text) and not in_georgian) or not self.session.personal else self.model
        return await self._claude_turn(self.session, with_pictures(self._with_private_notes(request), sent), device,
                                       on_text, quick.simple(text), model, keep_model=in_georgian)

    async def _quick(self, text, request, device, on_text):
        """A common short command run without a model (see quick.py), or None if it isn't one or didn't work."""
        in_conversation = self.session is not None and not self.session.expired
        found = quick.command(text, in_conversation, on_pc=works_where(device, self.devices) == "pc",
                              here=device.name)
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
            self.session.messages += quick.turn(request, name, args, content, reply.text, name in self.session.tools)
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

    async def _claude_turn(self, session, request, device, on_text, simple=False, model=None, keep_model=False):
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
        taken_over = keep_model  # a stronger model took over this request: it keeps it
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
            if len(results) == 1 and not results[0]["is_error"] and getattr(session.tools.get(tool_uses[0].name), "whole", False):
                # Its answer is the reply as it stands (the financist's), with no second model call to reword it.
                said, last = [results[0]["content"]], 0
                on_text(said[0])
                session.messages.append({"role": "assistant", "content": said[0]})
                break
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
        """The first line of each request: when (with the UTC offset, for other time zones), from where, where it
        works when that's another device (works_on.py), and which devices are online."""
        now = datetime.now(timezone())
        online = [d.label for name, d in self.devices.items() if name in identity.KNOWN_DEVICES]
        notes, self.notes = "".join(f" · {note}" for note in self.notes), []
        where = header_note(device, self.devices)
        where = f" · {where}" if where else ""
        return (f"[{now:%A %d %B %Y, %H:%M} {utc_offset(now)} · from the {device.label}{where} · "
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
