"""The agent as a service: devices (PC, phone) connect over a WebSocket and talk to Natro.

Listens on NATRO_LISTEN (host:port). On the VPS that is its Tailscale address,
never a public one; the default 127.0.0.1:8700 is for trying it on one machine.
If NATRO_DEVICE_TOKEN is set in .env, devices must send it in their hello.
GET /health answers "ok", so a device can show "Natro is online". /dashboard is the dashboard (dashboard/).

Usage: python -m natro_agent.server

Protocol: JSON messages over the WebSocket, and binary ones after some of them (recordings, pictures,
file parts). The messages both ways are in shared/PROTOCOL.md, the contract the desktop app and the
phone app implement.
"""
import asyncio
import collections
import hmac
import json
import os
import re
import signal
import sys
import threading
import time
from contextvars import ContextVar

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from natro_agent import log
from natro_agent.agent import PICTURE_MINUTES
from natro_agent.aloud import SAID, Aloud
from natro_agent.app_updates import AppUpdates
from natro_agent.background import Background
from natro_agent.cards import Cards
from natro_agent.chats import SharedChat
from natro_agent.convert import Converter, available as converters
from natro_agent.pdf import PdfTools
from natro_agent.config import ROOT, load_env
from natro_agent.dashboard import Dashboard
from natro_agent.delivery import Delivery
from natro_agent.devices import RemoteDevice
from natro_agent.files import Transfers, safe_name
from natro_agent.follow_ups import FollowUps
from natro_agent.jobs import Jobs
from natro_agent.keys import RESTART_CODE, Keys
from natro_agent.mcp_client import MCPServers
from natro_agent.pending import PendingRequests
from natro_agent.people import People
from natro_agent.phone_messages import PhoneMessages
from natro_agent.place_actions import PlaceActions
from natro_agent.places import Places
from natro_agent.places_page import PlacesPage
from natro_agent.reminders import Reminders
from natro_agent.routing import noise
from natro_agent.routines import Routines
from natro_agent.sending import Sending
from natro_agent.setup import build_agent, google_account
from natro_agent.timing import Timing
from natro_agent.tools import KnownTools
from natro_agent.travel import Travel
from natro_agent.watchers import Watchers
from natro_agent.works_on import WorksOn

DEFAULT_LISTEN = "127.0.0.1:8700"
# Recordings are sent whole: 16 MB is over 8 minutes of audio.
MAX_MESSAGE_BYTES = 16 * 2**20
HELLO_SECONDS = 10
# After saving new keys, Natro ends this long after her answer is sent, and systemd starts her again.
RESTART_SECONDS = 1.5
# The messages a binary message follows.
WITH_BYTES = {"file_part", "picture", "audio"}
# The most files he can attach to one request.
MOST_ATTACHED = 20

# The request Natro is handling in this task (his, or a background job's): its chat entry, and the device it
# came from. Each request and job is a task of its own, so several can be under way at once.
_entry = ContextVar("entry", default=None)
_from = ContextVar("from", default=None)


def _handling(entry, device):
    """From here on, this task handles the request with this chat entry, from this device (a background job's)."""
    _entry.set(entry)
    _from.set(device)

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


class Server:
    """The WebSocket, requests and replies, questions and pictures. The rest is features, objects that each offer what
    they have of:

      tools()               Natro's tools, added to the agent's
      connected(device)     a device came online
      disconnected(device)  it went
      messages()            {type: handler(device, data)} for what the devices send, with its bytes after data for
                            those in WITH_BYTES. A handler that returns a coroutine has started a request: it runs as
                            a task of its own, which the device can cancel by the message's id.
      asking, entry, handling, respond, offered, sending, tell, picture_sent
                            attributes the server sets. asking(): the device the request being handled came from.
                            entry(): its chat entry. handling(entry, device): from here on, this task handles that
                            request. respond(...): the server's _respond, Natro answering one. offered(): every tool
                            on offer now. sending: its sending.Sending, if there's one. tell(text): tells him.
                            picture_sent(): (mime, bytes) of the picture he just sent, or None.

    transfers: a files.Transfers, for files between devices (None: no files). pending: a
    pending.PendingRequests, for requests kept for an offline device (None: none kept). reminders: a
    reminders.Reminders (None: no reminders), whose clock (its run) the caller runs. jobs: a jobs.Jobs, for
    background jobs, run by background.Background (None: none). watchers: a watchers.Watchers (None: none), whose checks (its run) the caller
    runs. converter: a convert.Converter, for converting his files and working on his PDFs (None: none). keys: a keys.Keys, for the
    Settings window's keys (None: none). routines: a routines.Routines (None: none; they need jobs), whose clock
    (its run) the caller runs. people: a people.People, the address book for messaging anyone on Instagram and
    Messenger (None: no send_message). features: any others."""

    def __init__(self, agent, voice=None, token=None, transfers=None, pending=None, reminders=None, jobs=None,
                 watchers=None, converter=None, keys=None, routines=None, people=None, features=()):
        self.agent = agent
        self.voice = voice
        self.token = token
        self.chat = SharedChat(agent.devices)
        self.delivery = Delivery(agent, self.chat, reminders, watchers)
        self.pending = pending
        self.reminders = reminders
        self.jobs = jobs
        self.routines = routines if jobs else None
        self.background = Background(agent, self.chat, self.delivery, jobs, self.routines) if jobs else None
        self.watchers = watchers
        self.keys = keys
        self.phone_messages = PhoneMessages(agent.devices, self.chat, self.delivery.used, self._month)
        self.follow_ups = FollowUps()
        self._questions = {}  # (device name, question id) -> its question_elsewhere, while Natro waits for an answer
        self._last_picture = None  # (when, mime, bytes): the last picture he sent into the chat
        self._tasks = set()
        # The server's own messages. file_offer is here for when there's no Transfers to take it.
        self._messages = {"file_offer": self._no_files, "answer": self._answer, "tool_result": self._tool_result,
                          "picture": self._sent_picture, "chat_clear": self._chat_clear, "text": self.text_request,
                          "audio": self.audio_request, "spoken": self._spoken}
        sending = people and Sending(people, agent.devices)
        given = {"asking": _from.get, "entry": _entry.get, "handling": _handling, "respond": self._respond,
                 "offered": agent.offered_tools, "sending": sending, "tell": self.delivery.tell,
                 "picture_sent": self._picture_sent}
        # This is the order of their tools in her prompt, and of what a device hears when it connects.
        features = [feature for feature in (
            transfers, Aloud(agent.devices), WorksOn(agent.devices), pending, reminders, jobs, self.routines, watchers,
            converter, converter and PdfTools(converter), people, sending, keys,
            self.delivery, self.phone_messages, self.background, *features) if feature]
        for feature in features:
            for name, value in given.items():
                if hasattr(feature, name):
                    setattr(feature, name, value)
            if hasattr(feature, "tools"):
                agent.own_tools += feature.tools()
            if hasattr(feature, "messages"):
                self._messages.update(feature.messages())
        self._connected = [feature.connected for feature in features if hasattr(feature, "connected")]
        self._disconnected = [feature.disconnected for feature in features if hasattr(feature, "disconnected")]

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
        for connected in self._connected:
            connected(device)
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
                if kind == "cancel":
                    if task := running.pop(data.get("id"), None):
                        task.cancel()
                    # Posted before the cancelled task can run again, so nothing of it follows this.
                    device.post(type="cancelled", id=data.get("id"))
                    continue
                # Its bytes are read even when nothing here handles it, so they aren't taken for the next message.
                follows = [await connection.recv()] if kind in WITH_BYTES else []
                handler = self._messages.get(kind)
                request = handler(device, data, *follows) if handler else None
                if asyncio.iscoroutine(request):
                    self._start(request, running, data.get("id"))
        except ConnectionClosed:
            pass
        finally:
            self.agent.disconnect(device)
            device.disconnected()
            for disconnected in self._disconnected:
                disconnected(device)
            sender.cancel()
            log.write("device", device=device.name, event="disconnected")

    @staticmethod
    def _no_files(device, data):
        device.post(type="file_failed", id=data.get("id"), message="Natro can't pass files on.")

    @staticmethod
    def _tool_result(device, data):
        device.answered(data.get("id"), data)

    def _chat_clear(self, device, data):
        self.chat.clear()
        self.agent.new_conversation()
        log.write("chat", device=device.name, event="cleared")

    async def text_request(self, device, data):
        device.locked = bool(data.get("locked"))
        await self._respond(device, data.get("id"), data.get("text", ""), voice_cost=0.0, files=attached(data))

    async def audio_request(self, device, data, pcm):
        device.locked = bool(data.get("locked"))
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
        follow_up = bool(data.get("follow_up"))  # from its listening after a reply (follow_ups.py)
        if answer_to := data.get("answer_to"):
            self._count_voice(device, voice_cost, answer=english)
            device.answered(answer_to, {"yes": is_yes(english)})
            self.chat.answered(device.name, answer_to, english.strip() or "No")
        elif follow_up and (not english or noise(english, in_conversation=True)):
            # He didn't ask for this one: no "I didn't catch that", and nothing in the chat.
            self._count_voice(device, voice_cost, follow_up=True)
            device.post(type="nothing_heard", id=id, **self._month())
        elif not english:
            self._count_voice(device, voice_cost)
            device.post(type="reply", id=id, text="I didn't catch that.", model="none", cost=round(voice_cost, 5),
                        **self._month())
        else:
            await self._respond(device, id, english, voice_cost, heard=georgian, spoken=True, timing=timing,
                                files=attached(data), follow_up=follow_up)

    def _count_voice(self, device, voice_cost, **fields):
        """Voice spending that didn't become a request still counts toward the month."""
        self.agent.ledger.add(voice_cost)
        log.write("voice", device=device.name, voice_cost=round(voice_cost, 6), **fields)

    async def _respond(self, device, id, english, voice_cost, heard="", spoken=False, shown=None, queued=None,
                       timing=None, files=(), follow_up=False):
        """Natro answers a request; the chat on every device shows it (as shown: the request as he made it).
        timing: the Timing (timing.py) started when it came in (one starts now if not), logged once the reply is
        out. files: the paths of the files he attached to it, on this device: the chat shows their names, and she
        gets the paths in a line before his words. follow_up: he said it in the seconds after her last reply."""
        timing = timing or Timing()
        asked = shown or english
        if files:
            asked = f"{asked} ({', '.join(safe_name(file) for file in files)})"
        entry = self.chat.start(device, id, asked=asked, heard=heard, spoken=spoken, queued=queued)
        self.delivery.used[device.name] = time.monotonic()
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
                                             on_start=started, timing=timing, context=files_line(device, files))
        except asyncio.CancelledError:
            self.chat.replied(entry, "", problem="Cancelled")
            raise
        finally:
            _entry.set(None)
            _from.set(None)
            SAID.set(None)
        self.chat.replied(entry, reply.text)
        # Going on by just talking costs a recognition and a translation: not with the month's budget gone.
        listen = spoken and not self.agent.ledger.over_budget and self.follow_ups.replied(device.name, follow_up, reply)
        # Said out loud already, at his asking: not again.
        device.post(type="reply", id=id, text=reply.text, model=reply.model, cost=round(reply.cost + voice_cost, 5),
                    seconds=round(reply.seconds, 2), tools=tools_used(reply), **self._month(),
                    **({"said": True} if said else {}), **({"listen": listen} if listen else {}))
        log.write("timing", device=device.name, id=id, text=english[:200], spoken=spoken, model=reply.model,
                  **timing.record(), **({"follow_up": True} if follow_up else {}))

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
        self._last_picture = (time.monotonic(), mime, picture)
        if not self.agent.picture_sent(mime, picture, device.label):
            self.agent.notes.append(f"he sent a picture into the chat from his {device.label}; it shows on his "
                                    "devices, but it can't be opened, so you can't see it")
        log.write("chat", device=device.name, event="picture", bytes=len(picture))

    def _picture_sent(self):
        """(mime, bytes) of the picture he just sent (as long as it would go with his next request), or None."""
        if self._last_picture and time.monotonic() - self._last_picture[0] <= PICTURE_MINUTES * 60:
            return self._last_picture[1:]
        return None

    def _month(self):
        """This month's spending, for devices to show."""
        return {"spent": round(self.agent.ledger.spent, 4), "budget": self.agent.ledger.budget}


def attached(data):
    """The files he attached to a request: their full paths on the device it came from."""
    files = data.get("files")
    if not isinstance(files, list):
        return []
    return [file.strip() for file in files if isinstance(file, str) and file.strip()][:MOST_ATTACHED]


def files_line(device, files):
    """The line before his words that tells Natro about the files he attached ("" with none)."""
    if not files:
        return ""
    return f"[attached to this request, on his {device.label}: {'; '.join(files)}]"


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
    places = Places(ROOT / "places.json", devices)
    reminders = Reminders(ROOT / "reminders.json", devices, places)
    google = google_account()
    travel = Travel(places=places)
    watchers = Watchers(ROOT / "watchers.json", devices, google, travel)
    agent = await build_agent(servers, reminders=reminders, google=google, travel=travel, places=places,
                              watchers=watchers)
    agent.devices = devices
    agent.known_tools = KnownTools(ROOT / "devices.json")  # the service's alone: the typed chat's dry-run PC isn't his
    token = os.environ.get("NATRO_DEVICE_TOKEN")
    if not token:
        print("NATRO_DEVICE_TOKEN is not set: any device that can reach this address may connect.")
    transfers = Transfers(ROOT / "transfers", agent.devices)
    updates = AppUpdates(ROOT / "app", agent.devices, token)
    server = Server(agent, make_voice(), token, transfers, PendingRequests(ROOT / "pending.json", agent.devices),
                    reminders, Jobs(), watchers, Converter(transfers),
                    Keys(ROOT / ".env", ROOT / "tokens" / "settings-pin.json"), Routines(ROOT / "routines.json", places),
                    People(ROOT / "memory" / "people.md"),
                    features=[places, PlaceActions(ROOT / "place_actions.json", places),
                              Cards(ROOT / "cards", devices, places), updates])
    print(f"Converters: {', '.join(name for name, there in converters().items() if there) or 'none'}")
    host, port = os.environ.get("NATRO_LISTEN", DEFAULT_LISTEN).rsplit(":", 1)
    dashboard = Dashboard(server, host, places=PlacesPage(places))
    restarting = False
    try:
        async with serve(dashboard.websocket, host, int(port),
                         process_request=lambda connection, request: health(connection, request)
                         or updates.http(connection, request) or dashboard.http(connection, request),
                         max_size=MAX_MESSAGE_BYTES) as listener:
            if sys.platform != "win32":  # systemd stops the service with SIGTERM
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, listener.close)

            def restart():
                nonlocal restarting
                restarting = True
                asyncio.get_running_loop().call_later(RESTART_SECONDS, listener.close)

            server.keys.restart = restart
            print(f"Natro is listening on {host}:{port}; the dashboard is http://{host}:{port}/dashboard")
            clocks = [asyncio.create_task(server.reminders.run()), asyncio.create_task(server.watchers.run()),
                      asyncio.create_task(server.routines.run()), asyncio.create_task(agent.keep_warm()),
                      asyncio.create_task(updates.run())]
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
