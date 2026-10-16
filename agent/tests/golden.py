"""The real tool catalog, as Natro offers it, with every tool's run replaced by a recorder.

Used by the golden tests (test_golden.py). The flags (personal, confirm, changes) are the real
ones, so a tool added or changed anywhere shows up here. The PC's tools come from the PC app's
own definitions when it's installed (the Windows PC), else from the dry-run copy; the phone's are
read from the Kotlin source, which is where they are defined.
"""
import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.files import Transfers
from natro_agent.google_tools import SCOPES, Google
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.spotify import Spotify
from natro_agent.websearch import WebSearch

PHONE_TOOLS_KT = Path(__file__).resolve().parents[2] / "android/app/src/main/java/dev/natro/PhoneTools.kt"


def own_tools(folder):
    """Natro's own tools, the server's (files between devices), and the cloud ones (Google, Spotify), signed in."""
    google_api = MagicMock(scopes=SCOPES)  # signed in with every permission, so Drive and Docs are offered
    search = WebSearch(key="tvly-test", post=lambda url, body, key: {"results": []})
    return (Memory(folder / "memory").tools() + Notes(folder / "notes").tools() + search.tools()
            + Transfers(folder / "transfers", {}).tools() + Google(google_api).tools() + Spotify(MagicMock()).tools())


def pc_specs():
    try:
        from natro_pc import apps, files
    except Exception:  # not on Windows, or the PC app isn't installed
        return PC_TOOLS
    return [tool.spec() for tool in apps.tools(MagicMock()) + files.tools(MagicMock())]


def phone_specs():
    """The phone's tool descriptions, read from PhoneTools.kt: name, personal, confirm, final."""
    source = PHONE_TOOLS_KT.read_text(encoding="utf-8")
    start = source.index("private val tools = listOf(")
    end = source.index("/** The tools as the hello describes them. */")
    pieces = re.split(r'\n\s*Tool\("', source[start:end])[1:]
    specs = []
    for piece in pieces:
        flags = {flag: f"{flag} = true" in piece for flag in ("personal", "confirm", "final")}
        specs.append({"name": re.match(r"[a-z_]+", piece).group(), "description": "A phone tool.",
                      "input_schema": {"type": "object", "properties": {}, "required": []}, **flags})
    return specs


def recorded(tools, calls):
    """The same tools, each appending (name, args) to calls instead of running."""
    def recorder(name):
        async def run(args):
            calls.append((name, args))
            return f"Done ({name})."
        return run
    return [replace(tool, run=recorder(tool.name)) for tool in tools]


class Recording(DryRunDevice):
    """A device whose tools are the real ones' descriptions; calls gets (full tool name, args) for each run."""

    def __init__(self, name, label, specs, calls):
        super().__init__(name, label, specs)
        self.all_calls = calls

    async def _run(self, tool, args):
        self.all_calls.append((f"{self.name}_{tool}", args))
        return f"Done ({self.name}_{tool})."


def catalog(folder, calls):
    """(Natro's own tools, the PC device, the phone device); every run is recorded in calls."""
    return (recorded(own_tools(folder), calls), Recording("pc", "PC", pc_specs(), calls),
            Recording("phone", "phone", phone_specs(), calls))


def all_tools(folder):
    own, pc, phone = catalog(folder, [])
    return own + pc.tools() + phone.tools()
