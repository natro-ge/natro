"""The phone's messages on the other devices: a message that comes to the phone (WhatsApp, Instagram, SMS) is shown on
the devices that show them (the PC), and his answer, typed there, goes from the phone in the message's own app (its
reply_message tool). The messages both ways are in shared/PROTOCOL.md (phone_message, phone_message_gone,
message_reply)."""
import asyncio
import collections
import itertools
import time

from natro_agent import log
from natro_agent.jobs import short
from natro_agent.tools import ToolError

# The phone's messages a device may still answer, by id.
MOST_MESSAGES = 50
MESSAGE_SECONDS = 30


class PhoneMessages:
    """devices: name -> device, while connected (the agent's). chat: the chats.SharedChat his answers go into, as
    requests. used: device name -> when (monotonic) he last made a request on it (delivery.Delivery's), which an
    answer counts in. month: returns this month's spending, for the devices to show with the answer's reply."""

    def __init__(self, devices, chat, used, month):
        self.devices = devices
        self.chat = chat
        self.used = used
        self.month = month
        # Set by the server: from here on, this task handles the request with this chat entry, from this device (the
        # answer being sent, for the phone's tool call).
        self.handling = lambda entry, device: None
        # id -> {"device", "key", "app", "title"}, the newest last; kept once their notification goes ("gone"), as
        # the phone may still answer them with a Reply it kept.
        self._messages = collections.OrderedDict()
        self._ids = itertools.count(1)

    def messages(self):
        return {"phone_message": self.came, "phone_message_gone": self.gone, "message_reply": self.answer}

    def came(self, device, data):
        """A message came to the phone: shown on the devices that show them (the PC), to answer there."""
        key = str(data.get("key") or "")
        viewers = [other for other in list(self.devices.values())
                   if other is not device and getattr(other, "phone_messages", False)]
        if not key or not viewers:
            return  # nowhere to show it: it's on the phone anyway
        id = f"pm{int(time.time()):x}{next(self._ids)}"
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

    def gone(self, device, data):
        """A message's notification went from the phone (read, answered, dismissed): the others let go of it. An
        answer he was typing meanwhile still goes, through a Reply the phone kept."""
        key = str(data.get("key") or "")
        for id, message in list(self._messages.items()):
            if message["device"] == device.name and message["key"] == key and not message.get("gone"):
                message["gone"] = True
                for other in list(self.devices.values()):
                    if getattr(other, "phone_messages", False):
                        other.post(type="phone_message_gone", id=id)

    async def answer(self, device, data):
        """His answer to a message from the phone, typed on this device: it goes from the phone, in the message's
        own app. In the chat as a request, answered as one (reply, or error)."""
        id, text = data.get("id"), str(data.get("text") or "").strip()
        message = self._messages.get(str(data.get("to") or ""))
        to = f"{message['title']} on {message['app']}" if message else "a message"
        entry = self.chat.start(device, id, asked=f"To {to}: {text}")
        self.used[device.name] = time.monotonic()
        started = time.monotonic()
        self.handling(entry, device)
        try:
            phone = self.devices.get(message["device"]) if message else None
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
            self.handling(None, None)
        words = str(said).strip()  # the phone's: "Sent to Nino on WhatsApp."
        reply = words.splitlines()[0] if words else f"Sent to {to}."
        self.chat.replied(entry, reply)
        device.post(type="reply", id=id, text=reply, model="none", cost=0.0,
                    seconds=round(time.monotonic() - started, 2), tools=[], **self.month())
        log.write("message", event="replied", device=device.name, app=message["app"])
