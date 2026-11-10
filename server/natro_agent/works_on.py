"""Where Natro works: each of the owner's devices has a switch, itself or the other device, for the requests made on
it that don't name a device. Set to the phone, "open YouTube" asked on the PC opens it on the phone; "open YouTube on my PC"
still opens it on the PC. Where the owner is stays the device asked on: her reply, saying it, her questions.

The owner changes a device's switch in its app (each app only its own), or by asking on either device, for that device
or the other ("work on the phone", "make the PC work on the phone"): work_on, or the same words as a quick command
(quick.py). Each request's first line says where it works when that's the other device (the agent's header), and
when the device it's set to is offline, it works where it was asked, and says so.

A device that has the switch keeps it itself, with its other switches, and says how it's set in its hello
("works_on": "phone"; one naming neither of the owner's devices is itself). An app without it has no "works_on" in its hello.
It's told which of the owner's devices are online, so it can show when the one it's set to isn't.

Device to Natro:
  {"type": "works_on", "device": "phone"}     its switch was changed there
Natro to device:
  {"type": "works_on", "device": "phone"}     its switch is set to this now (changed by asking, on it or the other
                                              device, or its own change taken): show it and keep it
  {"type": "online", "devices": ["pc"]}       the owner's devices online now: once it's connected, and whenever one comes
                                              or goes
"""
from natro_agent import log
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError


def switched_to(device):
    """The other device that device's switch points at, or None when it works where it's asked (or has no switch)."""
    set_to = getattr(device, "works_on", None)
    return set_to if set_to and set_to != device.name else None


def works_where(device, online):
    """The device a request made on device works on: the one its switch points at, unless that's offline (online:
    the connected devices' names), else device itself."""
    set_to = switched_to(device)
    return set_to if set_to in online else device.name


def header_note(device, online):
    """Where a request made on device works, for its first line, or "" when it works where it was asked."""
    set_to = switched_to(device)
    if set_to is None:
        return ""
    if set_to in online:
        return f"works on the {KNOWN_DEVICES[set_to]}"
    if device.name in KNOWN_DEVICES:
        return f"works on the {KNOWN_DEVICES[device.name]} (set to the {KNOWN_DEVICES[set_to]}, which is offline)"
    return f"set to work on the {KNOWN_DEVICES[set_to]}, which is offline"


class WorksOn:
    """devices: name -> device, while connected (the agent's). asking: returns the device the request being handled
    came from (the server sets it)."""

    def __init__(self, devices):
        self.devices = devices
        self.asking = lambda: None

    def tools(self):
        return [Tool(
            "work_on",
            "Set where you work, from now on, for the requests made on one of your owner's devices without "
            "naming a device: each device has its own switch, itself or the other device. Use it only when your "
            "owner asks to change it: \"work on the phone\", \"switch to the PC\", \"use my phone from now on\", "
            "\"work here again\", \"make the PC work on the phone\". To do one thing on a device, use that device's "
            "tools instead.",
            {"type": "object", "properties": {
                "device": {"type": "string", "enum": list(KNOWN_DEVICES),
                           "description": "The device to work on."},
                "for": {"type": "string", "enum": list(KNOWN_DEVICES),
                        "description": "Whose switch, only when the request names the other device's (\"make the "
                                       "PC work on the phone\", asked on the phone); else the device asked on."},
            }, "required": ["device"]},
            self.work_on, "natro", final=True)]

    async def work_on(self, args):
        where = args.get("device")
        if where not in KNOWN_DEVICES:
            raise ToolError(f"Say which device to work on: {', '.join(KNOWN_DEVICES)}.")
        asked_on = getattr(self.asking(), "name", None)
        whose = args.get("for") or asked_on
        if whose not in KNOWN_DEVICES:
            raise ToolError(f"Say whose switch to change: {', '.join(KNOWN_DEVICES)}.")
        label, there = KNOWN_DEVICES[whose], KNOWN_DEVICES[where]
        device = self.devices.get(whose)
        # Not errors: the first line is her reply as it is (final), for a quick command too.
        if device is None:
            return f"Your {label} is offline, so I can't change where it works."
        if getattr(device, "works_on", None) is None:
            return f"Natro's app on your {label} can't switch where it works yet: it needs updating."
        before = device.works_on
        if before != where:
            self.set(device, where, by=asked_on or "")
        mine = whose == asked_on
        if before == where:
            reply = (f"I'm already working on the {there}." if mine else
                     f"What you ask on the {label} already happens on the {there}.")
        elif mine:
            reply = f"Working on the {there} again." if where == whose else f"Working on the {there} now."
        elif where == whose:
            reply = f"Done: what you ask on the {label} happens there again."
        else:
            reply = f"Done: what you ask on the {label} now happens on the {there}."
        return f"{reply}\n(Requests made on the {label} that don't name a device work on the {there}.)"

    def messages(self):
        return {"works_on": self.switched}

    def connected(self, device):
        """A device connected (it's among the agent's devices now), or (disconnected) went: those with the switch
        hear who's online."""
        online = [name for name in KNOWN_DEVICES if name in self.devices]
        for other in list(self.devices.values()):
            if getattr(other, "works_on", None) is not None:
                other.post(type="online", devices=online)

    disconnected = connected

    def switched(self, device, data):
        """The device's own switch was changed there: taken, and said back, so the device and Natro end up the same
        even when it was changed there as Natro changed it by asking."""
        where = data.get("device")
        if getattr(device, "works_on", None) is None or where not in KNOWN_DEVICES:
            return
        self.set(device, where, by=device.name)

    @staticmethod
    def set(device, where, by):
        device.works_on = where
        device.post(type="works_on", device=where)
        log.write("works_on", device=device.name, works_on=where, by=by)
