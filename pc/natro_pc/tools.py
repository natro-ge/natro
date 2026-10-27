"""The PC's tools, as Natro's brain sees them: each runs here and returns text for her.

A tool is described to the brain in the device's hello (see agent/natro_agent/server.py):
name, description, input schema, whether its results are personal (so only Claude
sees them) and whether the owner must say yes first.
"""
import base64
from dataclasses import dataclass, field
from typing import Callable


class ToolError(Exception):
    """A tool failed in a way Natro should hear about: the message goes back to her."""


@dataclass
class Picture:
    """A tool's result with a picture (a screenshot): text tells Natro, the picture goes into the chat for him."""
    text: str
    data: bytes
    mime: str = "image/jpeg"
    name: str = "picture.jpg"

    def message(self):
        """The tool_result's result and picture (agent/natro_agent/server.py)."""
        return {"result": self.text, "picture": {"mime": self.mime, "name": self.name,
                                                 "data": base64.b64encode(self.data).decode("ascii")}}


def result_fields(result):
    """A tool's return value as tool_result fields: text, or text and a picture."""
    return result.message() if isinstance(result, Picture) else {"result": result}


@dataclass
class PCTool:
    name: str
    description: str
    run: Callable[..., str]  # blocking; called with the arguments as keywords
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    personal: bool = False
    confirm: bool = False
    final: bool = False  # its result is a fine spoken reply on its own ("Opened Spotify.")
    hidden: bool = False  # not offered to the model: only the brain calls it (send_message, after his yes)

    def spec(self):
        return {"name": self.name, "description": self.description, "personal": self.personal,
                "confirm": self.confirm, "final": self.final, "hidden": self.hidden,
                "input_schema": {"type": "object", "properties": self.properties, "required": self.required}}


def all_tools():
    import threading

    from natro_pc import apps, browser, commands, control, files, uia

    pc_apps = apps.Apps()
    threading.Thread(target=pc_apps.menu.warm_up, daemon=True).start()
    pc_commands = commands.Commands()
    threading.Thread(target=pc_commands.shell.warm_up, daemon=True).start()
    tools = apps.tools(pc_apps) + files.tools() + commands.tools(pc_commands)
    if uia.available():  # screen control: Windows UI Automation, through comtypes
        screen = control.ScreenControl()
        threading.Thread(target=screen.desktop.warm_up, daemon=True).start()
        tools += control.tools(screen)
    return tools + browser.tools()  # his Edge, once the Playwright Extension's token is in .env
