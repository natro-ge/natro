"""Messaging anyone on Instagram or Messenger ("message Nino on Instagram: running 10 min late"): after one yes, with
no model steps on a screen, through only what Android and each app offer other apps (no unofficial APIs).

send_message finds the person in Natro's address book (people.py) and asks him in its own words before anything opens
(Send "running 10 min late" to Nino Kapanadze on Instagram?). On his yes it goes the first of these ways that can
send it, and nothing shows anywhere unless the last is needed (owner, 2026-10-23: a seamless send, his phone often
out of his reach):
  1. The phone's quiet ways (Sending.kt, screen false): her chat's notification's Reply, or one kept from her last
     message; they work with the phone locked.
  2. Natro's own hidden Edge on the PC (browser.py): headless, signed in to his accounts once.
  3. The phone's screen (Sending.kt, screen true): sharing into her chat, or her chat's link, with Send tapped; it
     shows for a few seconds, then the phone goes back to what he had open. Only when 2 can't: the PC is off, or
     Edge stopped before sending. Not with the phone locked (a secure lock can't be got past).
Several people fit, none does, or nobody can be reached: it fails before asking anything, for her to ask him which
one, or the username. One message per request, one per yes; a background job asks too, each time.

Natro to device (the phone's and the PC's send_message, hidden from the model: only this calls them):
  tool_call send_message {"app": "instagram", "name": "Nino Kapanadze", "chat": "3402...", "facebook": "",
                          "username": "nino.k", "group": false, "text": "Running 10 min late.", "screen": false}
"""
import asyncio
import collections
import json
import re

from natro_agent.agent import ASK
from natro_agent.people import APPS, Person, app_of, plain, username_from
from natro_agent.tools import Tool, ToolError

# The phone answers through a Reply at once; on its screen it opens a chat and waits for it.
PHONE_SECONDS = 45
# Natro's own Edge starts, opens the chat's web page and waits for it (two pages at most).
EDGE_SECONDS = 60
# How the phone's send_message starts its failure when it was asked for the quiet ways alone and has none
# (Sending.kt).
NO_QUIET_WAY = "No quiet way"
MOST_CHARS = 2000


def offers(device, name):
    return device is not None and any(spec.get("name") == name for spec in getattr(device, "specs", []))


class Sending:
    """people: the address book (people.People). devices: name -> device, while connected (the agent's)."""

    def __init__(self, people, devices):
        self.people = people
        self.devices = devices
        self.request = lambda: None  # the chat entry of the request being handled (the server sets it)
        self._sent = collections.deque(maxlen=100)  # the requests that have had their one message

    def tools(self):
        return [Tool(
            "send_message",
            "Send a message to someone on Instagram or Messenger from his own account (\"message Nino on Instagram: "
            "running 10 min late\", \"DM Giorgi that I'm outside\"). Instagram unless he says Messenger. It finds "
            "them in Natro's address book and asks him itself, in its own words, before anything goes: don't ask "
            "him first. If several people fit, ask him which one; if nobody can be found, ask him for their "
            "username (never guess one), save it with people_save, then send. If it fails, tell him why: never send "
            "it another way (the screen or browser tools). One message per request. To answer a message that just "
            "came, reply_message.",
            {"type": "object", "properties": {
                "to": {"type": "string", "description": "Whom, as he says it (\"Nino\", \"Nino K\", a group's "
                                                        "name), or a username he gave (nino.k)."},
                "text": {"type": "string", "description": "The message, exactly as it should arrive, in the "
                                                          "language he said it in."},
                "app": {"type": "string", "enum": ["instagram", "messenger"]},
            }, "required": ["to", "text"]},
            self.send, "natro", personal=True, confirm=True, final=True, own_question=True)]

    async def send(self, args):
        app = app_of(args.get("app"))
        label = APPS[app]
        to = " ".join(str(args.get("to") or "").split())
        text = str(args.get("text") or "").strip()
        if not to:
            raise ToolError("Say whom to message.")
        if not text:
            raise ToolError("Say what the message says.")
        if len(text) > MOST_CHARS:
            raise ToolError(f"That's too long for one message (at most {MOST_CHARS} characters).")
        request = self.request()
        if request is not None and request in self._sent:
            raise ToolError(f"One at a time: nothing more was sent. Tell him to ask you again for {to}.")
        person = self._whom(app, to)
        shown = person.shown()
        phone, pc = self.devices.get("phone"), self.devices.get("pc")
        old_phone = phone is not None and not offers(phone, "send_message")
        phone = phone if offers(phone, "send_message") else None
        pc = pc if offers(pc, "send_message") else None
        if not phone and not pc:
            if old_phone:
                raise ToolError("Natro's app on his phone can't send messages yet: it needs updating (and his PC, "
                                "which could, is offline).")
            raise ToolError("His phone is offline, and his PC can't send it (it's offline, or Natro's app there needs "
                            "updating), so the message can't go now. Offer to keep it for the phone with "
                            "queue_for_device.")
        if request is not None:
            self._sent.append(request)
        ask = ASK.get()
        details = f"send_message {json.dumps({'app': app, 'to': shown, 'text': text}, ensure_ascii=False)}"
        if ask is None or not await ask(f"Send \"{text}\" to {shown} on {label}?", details):
            # The first line is the reply when nothing else is needed (final).
            return "I didn't send it.\n(He said no, or didn't answer in time, so nothing was sent.)"
        call = {"app": app, "name": person.name, "chat": person.chat, "facebook": person.facebook,
                "username": person.username, "group": person.group, "text": text}
        if phone:
            try:
                return await self._on(phone, {**call, "screen": False}, PHONE_SECONDS, label)
            except ToolError as e:
                if not str(e).startswith(NO_QUIET_WAY):
                    raise
        if pc:
            try:
                return await self._on(pc, call, EDGE_SECONDS, label)
            except ToolError as e:
                if not phone or "nothing was sent" not in str(e).lower():
                    offline = "" if phone else " His phone is offline: offer to keep it for the phone with " \
                                               "queue_for_device."
                    raise ToolError(f"{e}{offline}")
                from_pc = str(e)
        else:
            from_pc = "His PC is offline."
        try:  # the phone's screen: the last way, which shows on it for a few seconds
            return await self._on(phone, call, PHONE_SECONDS, label)
        except ToolError as e:
            raise ToolError(f"{e} (It couldn't go quietly from the PC either: {from_pc})")

    @staticmethod
    async def _on(device, call, seconds, label):
        """send_message on one device: what it did, or a ToolError saying why it didn't."""
        try:
            said = await asyncio.wait_for(device.call_tool("send_message", call), seconds)
        except asyncio.TimeoutError:
            raise ToolError(f"His {device.label} didn't say in time whether it went: ask him to check {label}. Don't "
                            "send it again.")
        return str(said).strip() or f"Sent on {label}."

    def _whom(self, app, to):
        """The one person he means, or a ToolError for her to ask him (nothing is asked then)."""
        label = APPS[app]
        fits = self.people.find(app, to)
        if len(fits) > 1:
            names = "; ".join(f"{person.shown()}{' (a group)' if person.group else ''}"
                              f"{f', last wrote {person.last}' if person.last else ''}" for person in fits)
            raise ToolError(f"Several people on {label} fit \"{to}\": {names}. Ask him which one; nothing was sent.")
        if fits:
            person = fits[0]
            if person.chat or person.facebook or person.username:
                return person
        elif re.search(r"[._\d]", said := plain(to).removeprefix("@")) or to.startswith("@"):
            try:  # a username he gave
                return Person("", app, username=username_from(app, said))
            except ToolError:
                pass
        raise ToolError(f"Natro has no way to reach \"{to}\" on {label}: they aren't in her address book, which "
                        f"learns people as their messages come. Ask him for their {label} username (never guess "
                        "one), save it with people_save, then send it. Nothing was sent.")
