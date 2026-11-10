"""Requests kept for a device that's offline: asked on the phone for the PC (or the other way), done later.

When a request needs his PC or phone and it's offline, Natro keeps it (queue_for_device). When that
device connects, it gets them (queued) and asks him about each: Run sends queue_run and the request goes
to Natro as one made on that device; Skip sends queue_skip. Unanswered ones stay until he answers, the
next time too, and go after KEEP_HOURS. Kept in pending.json, so a restart of the server keeps them.

Natro to device:
  {"type": "queued", "items": [{"id": "q1", "text": "close Discord", "from": "phone", "time": 1760000000000}]}
Device to Natro:
  {"type": "queue_run", "id": "q1"}       run it now, as a request from this device with this id
  {"type": "queue_skip", "id": "q1"}      forget it
"""
import itertools
import json
import time

from natro_agent import log
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

KEEP_HOURS = 24
MOST_PER_DEVICE = 20


class PendingRequests:
    """path: the JSON file they're kept in. devices: name -> device, while connected (the agent's).
    The server sets asking() -> the device the request being handled came from, and respond(device, id, english,
    voice_cost, shown, queued) -> Natro's answer to one, a coroutine."""

    def __init__(self, path, devices):
        self.path = path
        self.devices = devices
        self.asking = lambda: None
        self.respond = lambda *args, **kwargs: None
        self._ids = itertools.count(1)
        self.items = self._load()

    def tools(self):
        return [Tool(
            "queue_for_device",
            "His PC or phone is offline and the request needs it: keep the request for when it's back. That device "
            "then asks him whether to run it (within 24 hours). Only for a device that is offline right now.",
            {"type": "object", "properties": {
                "device": {"type": "string", "enum": list(KNOWN_DEVICES)},
                "request": {"type": "string", "description": "The request as he'd say it to that device, complete "
                                                             "(\"close Discord\", \"send the report to my phone\")."},
            }, "required": ["device", "request"]},
            self.keep, "natro")]

    async def keep(self, args):
        device, request = args.get("device"), " ".join(str(args.get("request") or "").split())
        if device not in KNOWN_DEVICES:
            raise ToolError(f"device is one of: {', '.join(KNOWN_DEVICES)}.")
        label = KNOWN_DEVICES[device]
        if device in self.devices:
            raise ToolError(f"His {label} is online: do it now instead.")
        if not request:
            raise ToolError("Say what to do there.")
        if sum(1 for item in self.items if item["device"] == device) >= MOST_PER_DEVICE:
            raise ToolError(f"{MOST_PER_DEVICE} requests are already waiting for his {label}.")
        asker = self.asking()
        item = {"id": f"q{int(time.time() * 1000):x}{next(self._ids)}", "device": device, "text": request,
                "from": getattr(asker, "name", ""), "time": int(time.time() * 1000)}
        self.items.append(item)
        self._save()
        log.write("queued", device=device, text=request)
        return f"Kept for his {label}: it will ask him there when it's back online (within {KEEP_HOURS} hours)."

    def messages(self):
        return {"queue_run": self._run, "queue_skip": self._skip}

    def _run(self, device, data):
        """He said to go ahead with one: Natro's answer to it, a request of this device's."""
        id = str(data.get("id", ""))
        item = self.take(device, id)
        if item is None:
            device.post(type="error", id=id, message="That request isn't waiting any more.")
            return None
        asked_on = KNOWN_DEVICES.get(item.get("from"), "other device")
        english = (f"[He asked for this on his {asked_on} while this {device.label} was offline, and has just "
                   f"said to go ahead.] {item['text']}")
        return self.respond(device, id, english, 0.0, shown=item["text"], queued=asked_on)

    def _skip(self, device, data):
        self.skip(device, str(data.get("id", "")))

    def connected(self, device):
        """A device came online: the requests waiting for it, if it can ask about them."""
        self._expire()
        if not getattr(device, "queue", False):
            return
        mine = [{key: item[key] for key in ("id", "text", "from", "time")}
                for item in self.items if item["device"] == device.name]
        if mine:
            device.post(type="queued", items=mine)

    def take(self, device, item_id):
        """The request to run now (Run), or None if it's gone (done elsewhere, expired)."""
        for item in self.items:
            if item["id"] == item_id and item["device"] == device.name:
                self.items.remove(item)
                self._save()
                return item
        return None

    def skip(self, device, item_id):
        if self.take(device, item_id) is not None:
            log.write("queue_skipped", device=device.name, id=item_id)

    def _expire(self):
        oldest = (time.time() - KEEP_HOURS * 3600) * 1000
        kept = [item for item in self.items if item["time"] >= oldest]
        if len(kept) != len(self.items):
            self.items = kept
            self._save()

    def _load(self):
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
            return [item for item in items if isinstance(item, dict) and item.get("device") in KNOWN_DEVICES]
        except (OSError, ValueError):
            return []

    def _save(self):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        partial.replace(self.path)
