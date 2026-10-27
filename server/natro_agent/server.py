"""The agent as a service: devices (PC, phone) connect over a WebSocket and talk to Natro.

Listens on NATRO_LISTEN (host:port). On the VPS that is its Tailscale address,
never a public one; the default 127.0.0.1:8700 is for trying it on one machine.
If NATRO_DEVICE_TOKEN is set in .env, devices must send it in their hello.
GET /health answers "ok", so a device can show "Natro is online". /dashboard is the dashboard (dashboard.py).

Usage: python -m natro_agent.server

Protocol: JSON messages over the WebSocket, and binary ones after some of them (recordings, pictures,
file parts). The messages both ways are in shared/PROTOCOL.md, the contract the desktop app and the
phone app implement.
"""
import asyncio
import base64
import binascii
import collections
import hmac
import itertools
import json
import os
import re
import signal
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from natro_agent import identity, log
from natro_agent.aloud import SAID, Aloud
from natro_agent.chats import SharedChat
from natro_agent.convert import Converter, available as converters
from natro_agent.pdf import PdfTools
from natro_agent.config import ROOT, load_env, timezone
from natro_agent.dashboard import Dashboard
from natro_agent.files import Transfers
from natro_agent.jobs import MAX_STEPS as JOB_STEPS, RUNNING, Jobs, lead, short
from natro_agent.keys import RESTART_CODE, Keys
from natro_agent.mcp_client import MCPServers
from natro_agent.pending import PendingRequests
from natro_agent.people import People
from natro_agent.reminders import Reminders
from natro_agent.routines import QUIET_REPLY, Routines, is_quiet
from natro_agent.sending import Sending
from natro_agent.setup import build_agent, google_account
from natro_agent.timing import Timing
from natro_agent.tools import Device, KnownTools, ToolError, WithPictures, device_tools
from natro_agent.travel import Travel
from natro_agent.watchers import Watchers

DEFAULT_LISTEN = "127.0.0.1:8700"
# A reminder is spoken on the device he used last if that was within this time; else on the phone.
RECENT_MINUTES = 30
# A reminder delivered later than this says when it was for.
LATE_MINUTES = 2
# What a finished background job found, as the next request Claude answers hears it.
JOB_NOTE_CHARS = 1500
# A finished job no device could show waits for one this long.
JOB_WAIT_HOURS = 12
# The phone's messages a device may still answer, by id.
MOST_MESSAGES = 50
MESSAGE_SECONDS = 30
# Recordings are sent whole: 16 MB is over 8 minutes of audio.
MAX_MESSAGE_BYTES = 16 * 2**20
HELLO_SECONDS = 10
# After saving new keys, Natro ends this long after her answer is sent, and systemd starts her again.
RESTART_SECONDS = 1.5

# The request Natro is handling in this task (his, or a background job's): its chat entry, and the device it
# came from. Each request and job is a task of its own, so several can be under way at once.
_entry = ContextVar("entry", default=None)
_from = ContextVar("from", default=None)

YES_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "correct", "right", "go", "do"}
NO_WORDS = {"no", "nope", "not", "don't", "dont", "cancel", "stop", "wait", "never"}


def is_yes(english):
    """Whether a spoken answer (translated) means yes. Anything unclear counts as no."""
    words = re.sub(r"[^\w\s']", " ", english.casefold()).split()
    # "OK Natro, no" must not count as yes because of the "OK".
    if words[:2] in (["ok", "natro"], ["okay", "natro"]):
        words = words[2:]
    return not NO_WORDS.intersection(words) and bool(YES_WORDS.intersection(words))


class Voice:
    """Georgian recordings to English, with the voice pipeline from natro_voice (Google Chirp 3 + Claude)."""

    def __init__(self):
        from natro_voice.config import load_env as load_voice_env, load_wordlist
        from natro_voice.speech import Recognizer, has_speech
        from natro_voice.translate import Translator

        load_voice_env()  # resolves the Google key file's path
        self.has_speech = has_speech
        self.recognizer = Recognizer()
        self.translator = Translator(terms=load_wordlist())
        self._lock = threading.Lock()  # one recording at a time; the translator keeps context

    def process(self, pcm, on_english=None, timing=None):
        """Returns (georgian, english, cost). Blocks: run it in a thread.

        timing, a dict, gets how long each part took, in seconds (Timing.voice: recognize, georgian when Google
        heard English and the recording was recognized again as Georgian only, translate, translate_first until
        the translation's first words).
        """
        timing = {} if timing is None else timing
        with self._lock:
            before = self.recognizer.cost + self.translator.cost
            if not self.has_speech(pcm):
                return "", "", 0.0
            heard, as_georgian = self.recognizer.transcribe(pcm, timing)
            if not heard:
                return as_georgian or heard, "", self.recognizer.cost + self.translator.cost - before
            started = time.monotonic()

            def first_words(piece):
                timing.setdefault("translate_first", time.monotonic() - started)
                if on_english:
                    on_english(piece)
            english = self.translator.translate(heard, as_georgian=as_georgian, on_text=first_words)
            timing["translate"] = time.monotonic() - started
            return as_georgian or heard, english, self.recognizer.cost + self.translator.cost - before


class RemoteDevice(Device):
    """A device connected over the WebSocket. Messages to it go out in order through an outbox."""

    def __init__(self, connection, hello):
        self.connection = connection
        self.name = hello["device"]
        self.label = hello.get("label") or self.name
        self.specs = hello.get("tools", [])
        self.files = bool(hello.get("files"))
        self.chat = bool(hello.get("chat"))
        self.queue = bool(hello.get("queue"))
        self.reminders = bool(hello.get("reminders"))
        self.jobs = bool(hello.get("jobs"))
        self.alerts = bool(hello.get("alerts"))
        self.watch_notifications = bool(hello.get("watch_notifications"))
        self.questions = bool(hello.get("questions"))
        self.phone_messages = bool(hello.get("phone_messages"))
        self.say = bool(hello.get("say"))
        self.since = time.time()  # when it connected, for the dashboard
        # Set by the server: Natro asked a question (id, text, details), and it's over (id, his yes or no, or None
        # when it went unanswered); a tool's result came with a picture; the chat entry of the request being handled.
        self.on_question = lambda id, text, details: None
        self.on_question_over = lambda id, yes: None
        self.on_picture = lambda picture: None
        self.entry = lambda: None
        self.outbox = asyncio.Queue()
        self._waiting = {}  # message id -> future for the device's answer
        self._ids = itertools.count(1)

    def tools(self):
        return device_tools(self, self.specs, self.call_tool)

    def post(self, **message):
        """Queue a message for the device (safe to call from synchronous code)."""
        self.outbox.put_nowait(json.dumps(message, ensure_ascii=False))

    def post_bytes(self, data):
        """Queue a binary message (a piece of a file, after its file_part)."""
        self.outbox.put_nowait(bytes(data))

    async def send_all(self):
        while True:
            await self.connection.send(await self.outbox.get())

    async def _ask(self, kind, on_id=None, **message):
        """Send a message that needs an answer, and wait for it."""
        id = f"{kind[0]}{next(self._ids)}"
        future = asyncio.get_running_loop().create_future()
        self._waiting[id] = future
        try:
            self.post(type=kind, id=id, **message)
            if on_id:
                on_id(id)
            return await future
        finally:
            self._waiting.pop(id, None)

    async def call_tool(self, name, args):
        result = await self._ask("tool_call", name=name, args=args, request=self.entry())
        if not result.get("ok"):
            raise ToolError(result.get("result") or f"{name} failed on the {self.label}.")
        text = result.get("result", "")
        picture = result.get("picture")
        if not isinstance(picture, dict):
            return text
        try:
            data = base64.b64decode(str(picture.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            return text
        mime = str(picture.get("mime") or "image/jpeg")
        self.on_picture(mime, data, str(picture.get("name") or ""))
        return WithPictures(str(text), [(mime, data)])

    async def confirm(self, question, details):
        asked, yes = None, None

        def on_id(id):
            nonlocal asked
            asked = id
            self.on_question(id, question, details)

        try:
            answer = await self._ask("confirm", on_id=on_id, question=question, details=details, request=self.entry())
            yes = bool(answer.get("yes"))
            return yes
        except ToolError:  # disconnected before answering
            return False
        finally:  # answered, or not: the time for it ran out, the request was cancelled, the device went
            if asked:
                self.on_question_over(asked, yes)

    def answered(self, id, answer):
        future = self._waiting.get(id)
        if future and not future.done():
            future.set_result(answer)

    def disconnected(self):
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(ToolError(f"The {self.label} disconnected."))


class Server:
    """transfers: a files.Transfers, for files between devices (None: no files). pending: a
    pending.PendingRequests, for requests kept for an offline device (None: none kept). reminders: a
    reminders.Reminders (None: no reminders), whose clock (its run) the caller runs. jobs: a jobs.Jobs, for
    background jobs (None: none). watchers: a watchers.Watchers (None: none), whose checks (its run) the caller
    runs. converter: a convert.Converter, for converting his files and working on his PDFs (None: none). keys: a keys.Keys, for the
    Settings window's keys (None: none). routines: a routines.Routines (None: none; they need jobs), whose clock
    (its run) the caller runs. people: a people.People, the address book for messaging anyone on Instagram and
    Messenger (None: no send_message)."""

    def __init__(self, agent, voice=None, token=None, transfers=None, pending=None, reminders=None, jobs=None,
                 watchers=None, converter=None, keys=None, routines=None, people=None):
        self.agent = agent
        self.voice = voice
        self.token = token
        self.transfers = transfers
        if transfers:
            agent.own_tools += transfers.tools()
        self.chat = SharedChat(agent.devices)
        self.aloud = Aloud(agent.devices)
        agent.own_tools += self.aloud.tools()
        self.aloud.asking = _from.get
        self.pending = pending
        if pending:
            agent.own_tools += pending.tools()
            pending.asking = _from.get
        self.reminders = reminders
        if reminders:
            agent.own_tools += reminders.tools()
            reminders.asking = _from.get
            reminders.deliver = self._remind
        self.jobs = jobs
        if jobs:
            agent.own_tools += jobs.tools()
            jobs.asking = _from.get
            jobs.run = self._run_job
        self.routines = routines if jobs else None
        if self.routines:
            agent.own_tools += routines.tools()
            routines.asking = _from.get
            routines.start = self._routine
        self._unseen_jobs = []  # (when, job_done's fields) for a device that shows jobs, none being online
        self.watchers = watchers
        if watchers:
            agent.own_tools += watchers.tools()
            watchers.deliver = self._alert
        if converter:
            agent.own_tools += converter.tools() + PdfTools(converter).tools()
            converter.tell = self._tell
        self.keys = keys
        self.people = people
        if people:
            sending = Sending(people, agent.devices)
            sending.request = _entry.get
            agent.own_tools += people.tools() + sending.tools()
        self._used = {}  # device name -> when (monotonic) he last made a request on it
        self._questions = {}  # (device name, question id) -> its question_elsewhere, while Natro waits for an answer
        # The phone's messages shown on other devices: id -> {"device", "key", "app", "title"}, the newest last;
        # kept once their notification goes ("gone"), as the phone may still answer them with a Reply it kept.
        self._messages = collections.OrderedDict()
        self._message_ids = itertools.count(1)
        self._tasks = set()

    def _start(self, coroutine, running, id):
        """Run a request; running (request id -> task) lets the device cancel it."""
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if id:
            running[id] = task
            task.add_done_callback(lambda done: running.pop(id) if running.get(id) is done else None)

    async def handle(self, connection):
        try:
            hello = json.loads(await asyncio.wait_for(connection.recv(), HELLO_SECONDS))
        except (asyncio.TimeoutError, ConnectionClosed, ValueError):
            return
        token_ok = not self.token or hmac.compare_digest(str(hello.get("token", "")), self.token)
        if hello.get("type") != "hello" or not hello.get("device") or not token_ok:
            await connection.close(1008, "bad hello")
            return

        device = RemoteDevice(connection, hello)
        device.on_question = lambda id, text, details: self._asked(device, id, text, details)
        device.on_question_over = lambda id, yes: self._question_over(device, id, yes)
        device.on_picture = lambda mime, data, name: self._tool_picture(device, mime, data, name)
        device.entry = _entry.get
        sender = asyncio.create_task(device.send_all())
        self.agent.connect(device)
        log.write("device", device=device.name, event="connected", tools=[spec["name"] for spec in device.specs])
        device.post(type="welcome", **self._month())
        self.chat.catch_up(device)
        if self.transfers:
            self.transfers.connected(device)
        if self.pending:
            self.pending.connected(device)
        if self.reminders:
            self.reminders.connected(device)
        if self.watchers:
            self.watchers.connected(device)
        if device.jobs:
            self._show_unseen_jobs()
        if device.questions:
            for question in list(self._questions.values()):
                if question["device"] != device.name:
                    device.post(**question)
        running = {}  # this device's requests in progress, by id
        try:
            async for message in connection:
                if isinstance(message, bytes):
                    continue  # a recording or a piece of a file without its message
                data = json.loads(message)
                kind = data.get("type") or ""
                if kind == "file_part":
                    piece = await connection.recv()
                    if self.transfers:
                        self.transfers.part(device, str(data.get("id", "")), piece)
                elif kind.startswith("file_"):
                    if self.transfers:
                        self.transfers.received(device, data)
                    elif kind == "file_offer":
                        device.post(type="file_failed", id=data.get("id"), message="Natro can't pass files on.")
                elif kind == "answer":
                    self._answer(device, data)
                elif kind == "tool_result":
                    device.answered(data.get("id"), data)
                elif kind == "picture":
                    self._sent_picture(device, data, await connection.recv())
                elif kind == "chat_clear":
                    self.chat.clear()
                    self.agent.new_conversation()
                    log.write("chat", device=device.name, event="cleared")
                elif kind == "notification_matched" and self.watchers:
                    self.watchers.matched(str(data.get("rule", "")), str(data.get("app") or ""),
                                          str(data.get("title") or ""), str(data.get("text") or ""))
                elif kind == "phone_message":
                    self._phone_message(device, data)
                elif kind == "phone_message_gone":
                    self._phone_message_gone(device, str(data.get("key") or ""))
                elif kind == "person" and self.people:
                    try:
                        self.people.seen(data.get("app"), data.get("name"), data.get("chat"), data.get("facebook"),
                                         bool(data.get("group")))
                    except OSError as e:  # the address book can't be written: the phone stays connected
                        log.write("error", step="people", error=repr(e))
                elif kind == "message_reply":
                    self._start(self._message_reply(device, data), running, data.get("id"))
                elif kind == "reminder_snooze" and self.reminders:
                    self._snooze(device, str(data.get("id", "")), data.get("minutes"))
                elif kind == "keys" and self.keys:
                    self._start(self._keys(device, data), running, None)
                elif kind in ("queue_run", "queue_skip") and self.pending:
                    self._queued(device, kind, str(data.get("id", "")), running)
                elif kind == "text":
                    self._start(self.text_request(device, data), running, data.get("id"))
                elif kind == "audio":
                    pcm = await connection.recv()
                    self._start(self.audio_request(device, data, pcm), running, data.get("id"))
                elif kind == "spoken":
                    self._spoken(device, data)
                elif kind == "cancel":
                    if task := running.pop(data.get("id"), None):
                        task.cancel()
                    # Posted before the cancelled task can run again, so nothing of it follows this.
                    device.post(type="cancelled", id=data.get("id"))
        except ConnectionClosed:
            pass
        finally:
            self.agent.disconnect(device)
            device.disconnected()
            if self.transfers:
                self.transfers.disconnected(device)
            sender.cancel()
            log.write("device", device=device.name, event="disconnected")

    async def text_request(self, device, data):
        await self._respond(device, data.get("id"), data.get("text", ""), voice_cost=0.0)

    async def audio_request(self, device, data, pcm):
        id = data.get("id")
        timing = Timing()
        if self.voice is None:
            device.post(type="error", id=id, message="Voice isn't set up on the server (see server/natro_voice/).")
            return
        loop = asyncio.get_running_loop()
        pieces = collections.deque()  # of the translation, from the voice thread, not yet posted

        def post_english():
            while pieces:
                device.post(type="english", id=id, text=pieces.popleft())

        def on_english(piece):
            pieces.append(piece)
            loop.call_soon_threadsafe(post_english)

        try:
            georgian, english, voice_cost = await asyncio.to_thread(self.voice.process, pcm, on_english, timing.voice)
        except Exception as e:
            log.write("error", device=device.name, error=repr(e), step="voice")
            device.post(type="error", id=id, message=f"The voice pipeline failed: {e}")
            return
        # A quick thread can return before the loop gets to its pieces, and the phone adds a piece
        # that comes after "heard" to the whole translation: post them now.
        post_english()
        device.post(type="heard", id=id, georgian=georgian, english=english)
        if answer_to := data.get("answer_to"):
            self._count_voice(device, voice_cost, answer=english)
            device.answered(answer_to, {"yes": is_yes(english)})
            self.chat.answered(device.name, answer_to, english.strip() or "No")
        elif not english:
            self._count_voice(device, voice_cost)
            device.post(type="reply", id=id, text="I didn't catch that.", model="none", cost=round(voice_cost, 5),
                        **self._month())
        else:
            await self._respond(device, id, english, voice_cost, heard=georgian, spoken=True, timing=timing)

    def _count_voice(self, device, voice_cost, **fields):
        """Voice spending that didn't become a request still counts toward the month."""
        self.agent.ledger.add(voice_cost)
        log.write("voice", device=device.name, voice_cost=round(voice_cost, 6), **fields)

    async def _respond(self, device, id, english, voice_cost, heard="", spoken=False, shown=None, queued=None,
                       timing=None):
        """Natro answers a request; the chat on every device shows it (as shown: the request as he made it).
        timing: the Timing (timing.py) started when it came in (one starts now if not), logged once the reply is
        out."""
        timing = timing or Timing()
        entry = self.chat.start(device, id, asked=shown or english, heard=heard, spoken=spoken, queued=queued)
        self._used[device.name] = time.monotonic()
        said = []  # what say_out_loud said on this device for it

        def started():  # Natro has turned to this request (she does his one at a time)
            _entry.set(entry)
            _from.set(device)
            SAID.set(said)

        def piece(text):
            device.post(type="reply_text", id=id, text=text)
            self.chat.piece(entry, text)

        def step(step):
            device.post(type="step", id=id, **step)
            self.chat.step(entry, step.get("text", ""))

        try:
            reply = await self.agent.respond(english, device, voice_cost=voice_cost, on_text=piece, on_step=step,
                                             on_start=started, timing=timing)
        except asyncio.CancelledError:
            self.chat.replied(entry, "", problem="Cancelled")
            raise
        finally:
            _entry.set(None)
            _from.set(None)
            SAID.set(None)
        self.chat.replied(entry, reply.text)
        # Said out loud already, at his asking: not again.
        device.post(type="reply", id=id, text=reply.text, model=reply.model, cost=round(reply.cost + voice_cost, 5),
                    seconds=round(reply.seconds, 2), tools=tools_used(reply), **self._month(),
                    **({"said": True} if said else {}))
        log.write("timing", device=device.name, id=id, text=english[:200], spoken=spoken, model=reply.model,
                  **timing.record())

    @staticmethod
    def _spoken(device, data):
        """The device started saying a reply: seconds after it came (its speech's start, for the dashboard)."""
        seconds = data.get("seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 <= seconds < 600:
            log.write("spoken", device=device.name, id=str(data.get("id") or ""), seconds=round(seconds, 3))

    def _answer(self, device, data):
        """His yes or no: to a question of this device's, or (device) of the other's, which is then told."""
        id, yes, name = data.get("id"), bool(data.get("yes")), data.get("device")
        for_job = name == "natro"  # a background job's question, answered in the chat
        if for_job:
            name = self.chat.asker(id)
        asking = self.agent.devices.get(name) if name else device
        if asking is None or (asking.name, id) not in self._questions:
            return  # a late answer (a notification still up): Natro isn't waiting for it any more
        asking.answered(id, data)
        self.chat.answered(asking.name, id, "Yes" if yes else "No")
        if asking is not device or for_job:
            asking.post(type="answered", id=id, yes=yes)

    def _asked(self, device, id, text, details):
        """Natro asks on a device: the chat shows it, and the other devices ask too, so he can answer wherever he is."""
        self.chat.asked(_entry.get(), id, text, device.name)
        question = {"type": "question_elsewhere", "id": id, "device": device.name, "label": device.label,
                    "question": text, "details": details}
        self._questions[(device.name, id)] = question
        for other in self._others(device):
            other.post(**question)

    def _question_over(self, device, id, yes):
        """Answered (anywhere), or given up on: the other devices stop asking."""
        if self._questions.pop((device.name, id), None) is None:
            return
        for other in self._others(device):
            other.post(type="question_done", id=id, device=device.name, label=device.label, yes=yes)

    def _others(self, device):
        """The devices other than this one that ask its questions too."""
        return [other for other in list(self.agent.devices.values())
                if other.name != device.name and getattr(other, "questions", False)]

    def _tool_picture(self, device, mime, data, name):
        """A screenshot or other picture a tool sent back: into the chat, with the request it's for."""
        self.chat.picture(device, mime, data, name, entry_id=_entry.get())

    def _sent_picture(self, device, data, picture):
        """A picture he sent into the chat: Claude sees it with his next request."""
        mime = str(data.get("mime") or "")
        if not isinstance(picture, bytes) or self.chat.picture(device, mime, picture,
                                                               str(data.get("name") or "")) is None:
            device.post(type="error", id=data.get("id"),
                        message="That picture can't be shown (too big, or not a picture).")
            return
        if not self.agent.picture_sent(mime, picture, device.label):
            self.agent.notes.append(f"he sent a picture into the chat from his {device.label}; it shows on his "
                                    "devices, but it can't be opened, so you can't see it")
        log.write("chat", device=device.name, event="picture", bytes=len(picture))

    def _phone_message(self, device, data):
        """A message came to the phone: shown on the devices that show them (the PC), to answer there."""
        key = str(data.get("key") or "")
        viewers = [other for other in list(self.agent.devices.values())
                   if other is not device and getattr(other, "phone_messages", False)]
        if not key or not viewers:
            return  # nowhere to show it: it's on the phone anyway
        id = f"pm{int(time.time()):x}{next(self._message_ids)}"
        message = {"device": device.name, "key": key, "app": short(str(data.get("app") or ""), 40) or "a message",
                   "title": short(str(data.get("title") or ""), 80) or "Someone"}
        self._messages[id] = message
        while len(self._messages) > MOST_MESSAGES:
            self._messages.popitem(last=False)
        for viewer in viewers:
            viewer.post(type="phone_message", id=id, app=message["app"], title=message["title"],
                        text=short(str(data.get("text") or ""), 400), reply=bool(data.get("reply")),
                        device=device.name, label=device.label)
        # What it says is his and stays out of the log.
        log.write("message", event="shown", app=message["app"], devices=[viewer.name for viewer in viewers])

    def _phone_message_gone(self, device, key):
        """A message's notification went from the phone (read, answered, dismissed): the others let go of it. An
        answer he was typing meanwhile still goes, through a Reply the phone kept."""
        for id, message in list(self._messages.items()):
            if message["device"] == device.name and message["key"] == key and not message.get("gone"):
                message["gone"] = True
                for other in list(self.agent.devices.values()):
                    if getattr(other, "phone_messages", False):
                        other.post(type="phone_message_gone", id=id)

    async def _message_reply(self, device, data):
        """His answer to a message from the phone, typed on this device: it goes from the phone, in the message's
        own app. In the chat as a request, answered as one (reply, or error)."""
        id, text = data.get("id"), str(data.get("text") or "").strip()
        message = self._messages.get(str(data.get("to") or ""))
        to = f"{message['title']} on {message['app']}" if message else "a message"
        entry = self.chat.start(device, id, asked=f"To {to}: {text}")
        self._used[device.name] = time.monotonic()
        started = time.monotonic()
        _entry.set(entry)
        try:
            phone = self.agent.devices.get(message["device"]) if message else None
            if not message:
                raise ToolError("That message isn't on your phone any more, so it can't be answered from here.")
            if not text:
                raise ToolError("There's nothing to send.")
            if phone is None:
                raise ToolError(f"Your phone is offline, so it can't answer {message['title']}.")
            if not any(spec.get("name") == "reply_message" for spec in getattr(phone, "specs", [])):
                raise ToolError("Natro's app on your phone can't answer messages yet: it needs updating.")
            said = await asyncio.wait_for(phone.call_tool("reply_message", {"key": message["key"], "text": text}),
                                          MESSAGE_SECONDS)
        except (ToolError, asyncio.TimeoutError) as e:
            problem = str(e) or "Your phone didn't answer in time."
            self.chat.replied(entry, "", problem=problem)
            device.post(type="error", id=id, message=problem)
            log.write("message", event="reply_failed", device=device.name, error=problem)
            return
        finally:
            _entry.set(None)
        words = str(said).strip()  # the phone's: "Sent to Nino on WhatsApp."
        reply = words.splitlines()[0] if words else f"Sent to {to}."
        self.chat.replied(entry, reply)
        device.post(type="reply", id=id, text=reply, model="none", cost=0.0,
                    seconds=round(time.monotonic() - started, 2), tools=[], **self._month())
        log.write("message", event="replied", device=device.name, app=message["app"])

    def _queued(self, device, kind, id, running):
        if kind == "queue_skip":
            self.pending.skip(device, id)
            return
        item = self.pending.take(device, id)
        if item is None:
            device.post(type="error", id=id, message="That request isn't waiting any more.")
            return
        asked_on = identity.KNOWN_DEVICES.get(item.get("from"), "other device")
        english = (f"[He asked for this on his {asked_on} while this {device.label} was offline, and has just "
                   f"said to go ahead.] {item['text']}")
        self._start(self._respond(device, id, english, 0.0, shown=item["text"], queued=asked_on), running, id)

    def _remind(self, reminder):
        """A reminder is due: shown on every device that shows them and in the chat, spoken on one. Returns
        whether any device showed it."""
        viewers = [device for device in list(self.agent.devices.values()) if getattr(device, "reminders", False)]
        if not viewers:
            return False
        speaker = self._speaker(viewers, reminder.get("on"))
        now = datetime.now(timezone())
        due = datetime.fromtimestamp(reminder["due"] / 1000, timezone())
        late = (now - due).total_seconds() > LATE_MINUTES * 60
        text = reminder["text"]
        if late:
            days = (now.date() - due.date()).days
            day = "" if days == 0 else "yesterday at " if days == 1 else f"{due:%A} at "
            text = f"A reminder from {day}{due:%H:%M}: {text}"
        for device in viewers:
            device.post(type="reminder", id=reminder["id"], text=text, due=reminder["due"], late=late,
                        speak=device is speaker)
        self.chat.reminder(reminder["id"], text)
        # So "remind me again in an hour" knows what. Personal: only Claude hears of it.
        self.agent.private_notes.append(f"you reminded him at {now:%H:%M}: \"{reminder['text']}\"")
        log.write("reminder", event="shown", id=reminder["id"], devices=[device.name for device in viewers],
                  spoken_on=speaker.name)
        return True

    def _run_job(self, job):
        """Start a background job (jobs.Jobs calls this); returns its task."""
        task = asyncio.create_task(self._job(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _job(self, job):
        """A background job: Natro works on it beside his requests, in the chat as it goes; then what she found
        goes to his devices, and the next request Claude answers hears it."""
        RUNNING.set(job)
        entry = self.chat.job(job.id, job.task, routine=job.title if job.routine else "")
        _entry.set(entry)
        _from.set(self.agent.devices.get(job.asked_on))
        SAID.set(None)  # started from one of his requests: its reply isn't the job's
        now = datetime.now(timezone())
        if job.routine:
            what = (f"A routine he set up, \"{job.title}\": it runs by itself now, at {now:%H:%M}, and he isn't "
                    "waiting for it")
        else:
            asked_on = identity.KNOWN_DEVICES.get(job.asked_on, "device")
            what = (f"A background job: he asked for this on his {asked_on} at {now:%H:%M} and is doing other things "
                    "meanwhile")
        quiet = (" He wants to hear only when there's something to report: if there's nothing, reply with only "
                 f"\"{QUIET_REPLY}\"") if job.quiet else ""
        context = (f"[{what}. Work through it to the end on your own. When you're done, your reply's first paragraph "
                   "is said to him out loud: the answer in a sentence or two. The details he'd want can follow, in "
                   f"short paragraphs without lists; he reads those.{quiet}]")

        def step(step):
            job.step = step.get("text", "")
            self.chat.step(entry, job.step)

        log.write("job", id=job.id, event="started", task=job.task)
        try:
            reply = await self.agent.worker(JOB_STEPS).respond(
                job.task, JobDevice(self), on_text=lambda piece: self.chat.piece(entry, piece), on_step=step,
                context=context)
        except asyncio.CancelledError:
            self.chat.replied(entry, "", problem="Cancelled")
            self.jobs.ended(job, "cancelled", "")
            self._routine_ran(job, "cancelled")
            log.write("job", id=job.id, event="cancelled")
            raise
        except Exception as e:
            log.write("error", step="job", id=job.id, error=repr(e))
            text = (f"The routine \"{job.title}\" failed: {e}" if job.routine
                    else f"The job \"{short(job.task, 80)}\" failed: {e}")
            self.chat.replied(entry, "", problem=text)
            self.jobs.ended(job, "failed", text)
            self._routine_ran(job, "failed", text)
            self._job_done(job, text, ok=False)
            return
        quiet = job.quiet and is_quiet(reply.text)
        self.chat.replied(entry, reply.text)
        self.jobs.ended(job, "done", reply.text)
        self._routine_ran(job, "quiet" if quiet else "done", reply.text)
        log.write("job", id=job.id, event="quiet" if quiet else "done", minutes=job.minutes(),
                  cost=round(reply.cost, 5))
        if not quiet:  # a quiet routine with nothing to report: only the chat shows it ran
            self._job_done(job, reply.text, ok=True)

    def _routine_ran(self, job, state, result=""):
        if job.routine and self.routines:
            self.routines.ran(job.routine, state, result)

    def _job_done(self, job, text, ok):
        """A job finished: shown on every device that shows jobs (or the next one that connects), spoken on one;
        Claude hears it next."""
        done = {"id": job.id, "task": job.task, "text": text, "say": lead(text), "ok": ok, "title": job.title}
        if not self._show_job(done):
            self._unseen_jobs.append((time.time(), done))
        # So "what did you find?" is answered by Sonnet, who is told (the next request counts as personal).
        what = f"your routine \"{job.title}\" ({job.id})" if job.routine else f"your background job {job.id}"
        self.agent.private_notes.append(f"{what} (\"{short(job.task, 200)}\") {'finished' if ok else 'failed'}; "
                                        f"you told him: \"{short(text, JOB_NOTE_CHARS)}\"")
        self.agent.personal_next = True

    def _show_job(self, done):
        """job_done to every device that shows jobs, spoken on one. Returns whether any device showed it."""
        viewers = [device for device in list(self.agent.devices.values()) if getattr(device, "jobs", False)]
        if not viewers:
            log.write("job", id=done["id"], event="waiting")
            return False
        speaker = self._speaker(viewers)
        for device in viewers:
            device.post(type="job_done", **done, speak=device is speaker)
        log.write("job", id=done["id"], event="shown", devices=[device.name for device in viewers],
                  spoken_on=speaker.name)
        return True

    def _show_unseen_jobs(self):
        """A device that shows jobs connected: the jobs that finished while none was online, if not too old."""
        oldest = time.time() - JOB_WAIT_HOURS * 3600
        unseen, self._unseen_jobs = self._unseen_jobs, []
        for when, done in unseen:
            if when < oldest:
                log.write("job", id=done["id"], event="dropped", late_hours=JOB_WAIT_HOURS)
            elif not self._show_job(done):
                self._unseen_jobs.append((when, done))

    def _routine(self, routine, asked=False):
        """A routine is due (or he asked to run it now): it runs as a background job titled with its name.
        Returns why it didn't start, or ""."""
        if any(job.routine == routine["id"] for job in self.jobs.running()):
            return "its last run is still going"
        if not asked and self.agent.ledger.over_budget:
            # Claude would ask first, and he isn't there to answer: skipped, and he's told.
            self._tell(f"I skipped your routine \"{routine['name']}\": this month's {self.agent.ledger.budget:g} "
                       "dollar budget is used up.")
            return "this month's budget was used up"
        self.jobs.add(routine["task"], title=routine["name"], routine=routine["id"], quiet=bool(routine.get("quiet")))
        return ""

    def _alert(self, alert):
        """Something a watcher saw: shown on every device that shows alerts and in the chat, spoken on one if
        it's urgent. Returns whether any device showed it."""
        viewers = [device for device in list(self.agent.devices.values()) if getattr(device, "alerts", False)]
        if not viewers:
            return False
        speaker = self._speaker(viewers) if alert["urgent"] else None
        for device in viewers:
            device.post(type="alert", id=alert["id"], text=alert["text"], kind=alert["kind"], urgent=alert["urgent"],
                        speak=device is speaker)
        self.chat.reminder(alert["id"], alert["text"], alert=True)
        if alert["kind"] != "meetings":
            # So "read it" or "what did it say?" is answered by Sonnet, who is told (the next request counts as
            # personal).
            self.agent.private_notes.append(f"you told him: \"{alert['text']}\"")
            self.agent.personal_next = True
        log.write("watcher", event="shown", id=alert["id"], devices=[device.name for device in viewers],
                  spoken_on=speaker.name if speaker else None)
        return True

    def _tell(self, text):
        """Tell him something that happened later (a conversion that failed after Natro answered), as an alert."""
        now = int(time.time() * 1000)
        self._alert({"id": f"tl{now:x}", "kind": "natro", "text": text, "urgent": False, "due": now,
                     "until": now + 12 * 3600 * 1000})

    def _speaker(self, viewers, on=None):
        """Which device says a reminder out loud: the one it's for (back online), else the one he used last if
        that was lately, else the phone."""
        by_name = {device.name: device for device in viewers}
        if on in by_name:
            return by_name[on]
        recent = [(when, name) for name, when in self._used.items()
                  if name in by_name and time.monotonic() - when < RECENT_MINUTES * 60]
        if recent:
            return by_name[max(recent)[1]]
        return by_name.get("phone") or viewers[0]

    def _snooze(self, device, id, minutes):
        at = self.reminders.snooze(id, minutes if minutes is not None else 10)
        if at is None:
            device.post(type="reminder_snoozed", id=id, ok=False, text="That reminder is gone.")
        else:
            device.post(type="reminder_snoozed", id=id, ok=True, text=f"Snoozed until {at:%H:%M}.")

    async def _keys(self, device, data):
        answer = await self.keys.handle(device.name, data)
        device.post(type="keys", id=data.get("id"), **answer)
        if answer.get("restarting"):
            self.keys.restart()

    def _month(self):
        """This month's spending, for devices to show."""
        return {"spent": round(self.agent.ledger.spent, 4), "budget": self.agent.ledger.budget}


class JobDevice(Device):
    """Whom a background job works for: its questions go to the device he used last (in the chat, the job's)."""

    name = "job"
    label = "background job"

    def __init__(self, server):
        self.server = server

    async def confirm(self, question, details):
        devices = [device for device in list(self.server.agent.devices.values()) if isinstance(device, RemoteDevice)]
        if not devices:
            return False
        return await self.server._speaker(devices).confirm(question, details)


def tools_used(reply):
    """The tools a request ran: name, arguments, whether it worked, whether the owner declined, the result's start."""
    return [{"tool": call["tool"], "args": call["args"], "ok": call["ok"], "declined": call.get("declined", False),
             "result": call["result"][:300]} for call in reply.tool_calls]


def health(connection, request):
    if request.path == "/health":
        return connection.respond(200, "ok\n")
    return None


def make_voice():
    try:
        return Voice()
    except Exception as e:  # not installed, or no Google credentials
        print(f"Voice requests are off: {e}")
        return None


async def run():
    """Natro, until she's stopped. True if she's to start again (her keys changed)."""
    load_env()
    servers = MCPServers()
    devices = {}  # the agent's, while connected
    reminders = Reminders(ROOT / "reminders.json", devices)
    google = google_account()
    travel = Travel()
    watchers = Watchers(ROOT / "watchers.json", devices, google, travel)
    agent = await build_agent(servers, reminders=reminders, google=google, travel=travel)
    agent.devices = devices
    agent.known_tools = KnownTools(ROOT / "devices.json")  # the service's alone: the typed chat's dry-run PC isn't his
    token = os.environ.get("NATRO_DEVICE_TOKEN")
    if not token:
        print("NATRO_DEVICE_TOKEN is not set: any device that can reach this address may connect.")
    transfers = Transfers(ROOT / "transfers", agent.devices)
    server = Server(agent, make_voice(), token, transfers, PendingRequests(ROOT / "pending.json", agent.devices),
                    reminders, Jobs(), watchers, Converter(transfers),
                    Keys(ROOT / ".env", ROOT / "tokens" / "settings-pin.json"), Routines(ROOT / "routines.json"),
                    People(ROOT / "memory" / "people.md"))
    print(f"Converters: {', '.join(name for name, there in converters().items() if there) or 'none'}")
    host, port = os.environ.get("NATRO_LISTEN", DEFAULT_LISTEN).rsplit(":", 1)
    dashboard = Dashboard(server, host)
    restarting = False
    try:
        async with serve(server.handle, host, int(port),
                         process_request=lambda connection, request: health(connection, request)
                         or dashboard.http(connection, request), max_size=MAX_MESSAGE_BYTES) as listener:
            if sys.platform != "win32":  # systemd stops the service with SIGTERM
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, listener.close)

            def restart():
                nonlocal restarting
                restarting = True
                asyncio.get_running_loop().call_later(RESTART_SECONDS, listener.close)

            server.keys.restart = restart
            print(f"Natro is listening on {host}:{port}; the dashboard is http://{host}:{port}/dashboard")
            clocks = [asyncio.create_task(server.reminders.run()), asyncio.create_task(server.watchers.run()),
                      asyncio.create_task(server.routines.run()), asyncio.create_task(agent.keep_warm())]
            try:
                await listener.serve_forever()
            except asyncio.CancelledError:
                print("Natro stopped.")  # systemctl stop/restart: a normal exit, not a failure
            finally:
                for clock in clocks:
                    clock.cancel()
    finally:
        await servers.close()
    return restarting


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        if asyncio.run(run()):
            print(f"Natro's keys changed: she ends with {RESTART_CODE} for systemd to start her again.")
            sys.exit(RESTART_CODE)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
