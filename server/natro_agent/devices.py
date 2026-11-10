"""The devices the server works with: one connected over the WebSocket (the PC, the phone; the messages
are in shared/PROTOCOL.md), and whom a background job works for."""
import asyncio
import base64
import binascii
import itertools
import json
import time

from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Device, ToolError, WithPictures, device_tools


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
        self.places = bool(hello.get("places"))
        self.cards = bool(hello.get("cards"))
        self.version = hello.get("version")  # its app's build (app_updates.py), if it says
        if "works_on" in hello:  # an app with the switch (works_on.py); one naming neither device is itself
            works_on = hello.get("works_on")
            self.works_on = works_on if works_on in KNOWN_DEVICES else self.name
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


class JobDevice(Device):
    """Whom a background job works for: its questions go to the device he used last (in the chat, the job's).
    delivery: the server's delivery.Delivery, which knows that device. works_on: the other device the switch of the
    device it was started on points at (works_on.py), if it does."""

    name = "job"
    label = "background job"

    def __init__(self, delivery, works_on=None):
        self.delivery = delivery
        self.works_on = works_on

    async def confirm(self, question, details):
        devices = [device for device in list(self.delivery.agent.devices.values())
                   if isinstance(device, RemoteDevice)]
        if not devices:
            return False
        return await self.delivery.speaker(devices).confirm(question, details)
