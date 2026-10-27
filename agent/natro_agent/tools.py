"""Tools and devices, as the agent sees them.

Every tool is described the same way, whether it is Natro's own, comes from an
MCP server, or runs on a connected device (PC, phone), so identity, routing and
confirmations treat them all alike.
"""
import json
import re
from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable

# Claude accepts tool names of letters, digits, "_" and "-", up to 64 characters.
_NOT_ALLOWED_IN_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def tool_name(*parts):
    return _NOT_ALLOWED_IN_NAME.sub("_", "_".join(parts))[:64]


ASKS_FIRST = ("This asks your owner first: just before calling it, write one sentence saying exactly what you're "
              "about to do (that sentence is his question).")


class ToolError(Exception):
    """A tool failed in a way Claude should hear about: the message goes back to it."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], Awaitable[Any]]  # returns text, or anything JSON can show
    source: str  # "natro", an MCP server's name, or a device's name
    # Its results hold personal data (emails, notes, files...). Nothing routes on it since the free Gemini tier,
    # which was offered only the tools without it, went (2026-10-20); the devices still send it.
    personal: bool = False
    # Ask the owner before running it (deleting, sending, calling, force-closing...).
    confirm: bool = False
    # It adds or changes one of the owner's items (an event, a task, a note): many in one request ask first.
    changes: bool = False
    # Its result's first line is a fine spoken reply ("Paused.", "Opened Spotify."): when a simple request
    # needed only this tool, that is the reply, without another model call.
    final: bool = False
    # It asks the owner itself, in its own words, once it knows what to ask (send_message: whom, where, what), with
    # agent.ASK; it may fail before, asking nothing. Only with confirm: the agent then doesn't ask before it.
    own_question: bool = False

    def definition(self):
        """The tool as Claude's API takes it."""
        description = self.description
        if self.confirm and not self.own_question:
            # Her sentence before the call is the question the owner hears; without it he only gets the tool's name.
            description += f" {ASKS_FIRST}"
        return {"name": self.name, "description": description, "input_schema": self.input_schema}


@dataclass
class WithPictures:
    """A tool's result with pictures Claude sees too (a screenshot): its text, and (mime, bytes) pairs."""
    text: str
    pictures: list


def result_text(result):
    """A tool's return value as the text Claude gets."""
    if isinstance(result, str):
        return result
    if isinstance(result, WithPictures):
        return result.text
    return json.dumps(result, ensure_ascii=False, default=str)


class Device:
    """A connected device: the PC app, the phone app, or the typed chat.

    A device offers tools (run on the device), shows replies, and asks the owner
    to confirm risky actions: by voice, a hotkey, or a tap/click.
    """

    name = "device"  # short id used in tool names: "pc", "phone"
    label = "device"  # how Natro calls it: "PC", "phone"
    files = False  # it can send and receive files (files.py)

    def tools(self):
        return []

    async def confirm(self, question, details):
        """Ask the owner; True only for a clear yes. details shows exactly what will run."""
        return False

    @classmethod
    def named(cls, name):
        """A stand-in with only a name (for tools a device offered before)."""
        device = cls()
        device.name = device.label = name
        return device


def device_tools(device, specs, call):
    """Tools from a device's own descriptions of them.

    specs: [{"name", "description", "input_schema", "personal", "confirm", "changes", "final", "hidden"}, ...] as
    the device sends them. call(name, args) runs one on the device. A hidden one isn't offered to the model: only
    the server calls it (the phone's send_message, which the server's own send_message calls after his yes).
    """
    return [Tool(name=tool_name(device.name, spec["name"]),
                 description=spec["description"],
                 input_schema=spec.get("input_schema") or {"type": "object", "properties": {}},
                 run=partial(call, spec["name"]),
                 source=device.name,
                 personal=spec.get("personal", False),
                 confirm=spec.get("confirm", False),
                 changes=spec.get("changes", False),
                 final=spec.get("final", False))
            for spec in specs if not spec.get("hidden")]


class KnownTools:
    """The tools each of his devices offered when it was last connected, kept in a file (devices.json).

    They stay on offer while the device is offline (calling one says it's offline), so Claude's tool list, and the
    instructions that list them, come out the same whichever devices are online: its cached copy of them still
    applies (2026-10-26: devices coming and going wrote them again 3 times in 3 days, at ~$0.11 each on Sonnet).
    """

    def __init__(self, path=None):
        self.path = path  # None: kept in memory only (tests)
        self.specs = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}

    def update(self, device):
        """A device connected: remember its tools. None (not a device with specs) or none yet (an app still
        starting up) leave what was remembered."""
        specs = getattr(device, "specs", None)
        if not specs or self.specs.get(device.name) == specs:
            return
        self.specs[device.name] = specs
        if self.path:
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(self.specs, ensure_ascii=False, indent=1), encoding="utf-8")
            temporary.replace(self.path)

    def tools(self, name):
        """The device's tools as it last offered them (they can't run here: the agent runs them on the device)."""
        async def offline(tool, args):
            raise ToolError("That device is offline right now.")
        return device_tools(Device.named(name), self.specs.get(name, []), offline)
