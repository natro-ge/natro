"""Saying something out loud when he asks ("say out loud good morning", "read that out loud", "say on the PC that
dinner's ready"), even on a device whose "Speak replies" is off, and for what he types in the PC's chat, whose
requests are otherwise answered in writing only.

Natro calls say_out_loud with the words; the device (the one he asked on, unless he names the other) gets them at
once and says them. Said on the device he asked on, they are her reply, which then comes with "said": true, so it
isn't said a second time. A device that can do this says "say": true in its hello.

Natro to device:
  {"type": "say", "id": "sy1", "text": "Dinner's ready.", "from": "phone"}
                                        say it out loud now, whatever "Speak replies" says; from: the device he
                                        asked on ("" when none did, as for a background job): when it's another
                                        one, show what was said too
"""
import itertools
import time
from contextvars import ContextVar

from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

# The words said on the device the request being handled came from, so its reply isn't said again: a list the
# server sets for each of his requests (None in a background job, whose reply is no device's).
SAID = ContextVar("said", default=None)


class Aloud:
    """devices: name -> device, while connected (the agent's). asking: returns the device the request being handled
    came from (the server sets it)."""

    def __init__(self, devices):
        self.devices = devices
        self.asking = lambda: None
        self._ids = itertools.count(1)

    def tools(self):
        return [Tool(
            "say_out_loud",
            "Say something out loud on one of his devices right away, even when its \"Speak replies\" is off (your "
            "replies are otherwise said only where it's on, and never for what he types in the PC's chat). Use it "
            "when he asks you to say or read something out loud or aloud: \"say out loud good morning\" (the words: "
            "\"Good morning.\"), \"say out loud what time it is\" (the answer), \"read that out loud\" (what you "
            "said last). Said on the device he asked on, it is your reply: reply with the same words (they aren't "
            "said a second time).",
            {"type": "object", "properties": {
                "text": {"type": "string", "description": "Exactly what to say, in English, written to be spoken."},
                "device": {"type": "string", "enum": list(KNOWN_DEVICES),
                           "description": "Only when he names where (\"say on the PC that dinner's ready\"); else "
                                          "it's said on the device he asked on."},
            }, "required": ["text"]},
            self.say, "natro", final=True)]

    async def say(self, args):
        text = " ".join(str(args.get("text") or "").split())
        if not text:
            raise ToolError("Give the words to say.")
        asker = self.asking()
        name = args.get("device") or getattr(asker, "name", None)
        if name not in KNOWN_DEVICES:
            raise ToolError(f"Say which device: {', '.join(KNOWN_DEVICES)}.")
        label = KNOWN_DEVICES[name]
        device = self.devices.get(name)
        if device is None:
            raise ToolError(f"His {label} is offline, so it can't say it.")
        if not getattr(device, "say", False):
            raise ToolError(f"Natro's app on his {label} can't say things out loud on request yet: it needs updating.")
        asked_on = getattr(asker, "name", "")
        here = asked_on == name
        device.post(**{"type": "say", "id": f"sy{int(time.time() * 1000):x}{next(self._ids)}", "text": text,
                       "from": asked_on})
        if not here:
            return f"I said it out loud on the {label}."
        if (said := SAID.get()) is not None:
            said.append(text)
        # The first line is the reply when nothing else is needed (final).
        return f"{text}\n(Said out loud on his {label} just now.)"
