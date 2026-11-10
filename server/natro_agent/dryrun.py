"""Devices that only record what they would do: for tests, and for trying the agent without the device apps."""
from natro_agent.tools import Device, device_tools


def _spec(name, description, properties, confirm=False, final=False):
    return {"name": name, "description": description, "confirm": confirm, "final": final,
            "input_schema": {"type": "object", "properties": {key: {"type": kind} for key, kind in properties.items()},
                             "required": list(properties)[:1]}}


# The PC app's tools (desktop/src-tauri/src/tools/: apps.rs, files.rs, commands.rs, screen.rs and this.rs), with the same names
# and flags.
PC_TOOLS = [
    _spec("open_app", "Open an app on the PC by name, as from the Start menu.", {"app": "string"}, final=True),
    _spec("close_app", "Close an app normally, as if clicking X on its windows.", {"app": "string"}, final=True),
    _spec("force_close_app", "Kill an app that won't close. Unsaved work in it is lost.", {"app": "string"},
          confirm=True),
    _spec("list_open_apps", "Which apps have windows open on the PC.", {}),
    _spec("search_files", "Find files and folders on the PC by name, newest first.",
          {"query": "string", "everywhere": "boolean"}),
    _spec("search_file_contents", "Find files whose text contains all these words.", {"text": "string"}),
    _spec("read_file", "Read a text file, Word document or PDF on the PC.", {"path": "string"}),
    _spec("open_file", "Open a file or folder on the PC with its usual program.", {"path": "string"}),
    _spec("open_link", "Open a web page in the PC's browser.", {"url": "string"}, final=True),
    _spec("run_command", "Run a harmless PowerShell command on the PC and get its output.", {"command": "string"}),
    _spec("run_command_risky", "Run any other PowerShell command on the PC.", {"command": "string"}, confirm=True),
    _spec("screen", "Read what's on the PC's screen now, numbered.", {"everything": "boolean"}),
    _spec("find", "Find elements on the PC's screen by their text.", {"text": "string"}),
    _spec("act", "Click, type, press keys or scroll on the PC's screen by element number.", {"actions": "array"}),
    _spec("act_risky", "The same as act, for what sends, buys, pays or deletes.", {"actions": "array"}, confirm=True),
    _spec("switch_window", "Bring an open window to the front.", {"name": "string"}),
    _spec("wait_for", "Wait until some text shows on the PC's screen.", {"text": "string"}),
    _spec("screenshot", "A picture of the PC's screen: you see it, and it goes into the chat for him.", {}),
    _spec("this", "What \"this\" or \"that\" means on the PC now, by name.", {}),
]


class DryRunDevice(Device):
    """calls lists every (tool, args) it was asked to run."""

    def __init__(self, name, label, specs):
        self.name, self.label, self.specs = name, label, specs
        self.calls = []

    def tools(self):
        return device_tools(self, self.specs, self._run)

    async def _run(self, tool, args):
        self.calls.append((tool, args))
        return f"Done (dry run: {tool} only pretended to run)."
