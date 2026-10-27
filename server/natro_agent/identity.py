"""Natro's instructions: who she is (identity/NATRO.md), her devices and tools, and what she remembers.

The tools part is generated from the tools on offer, so "what can you do?" stays
true. It must come out the same for the same tools, whichever devices are online
(each request's first line says which are): any change to the instructions means
Claude can't reuse its cached copy. What Natro remembers is a block of its own
after them, since it changes most often.
"""
from collections import defaultdict

from natro_agent.config import IDENTITY_FILE

# The devices Natro knows about, so she can tell when one is offline.
KNOWN_DEVICES = {"pc": "PC", "phone": "phone"}


def system_prompt(tools):
    """tools: every tool on offer, the devices' included (an offline one's too)."""
    identity = IDENTITY_FILE.read_text(encoding="utf-8").strip()
    return f"{identity}\n\n## Your devices and tools\n\n{capabilities(tools)}"


def memory_prompt(memory):
    """memory: what Natro remembers."""
    return f"## What you remember about your owner\n\n{memory}"


def capabilities(tools):
    lines = [f"- Your owner's devices: {', '.join(KNOWN_DEVICES.values())}. Each message's first line says which "
             "are online. One that isn't can't run its tools (they say it's offline); queue_for_device keeps a "
             "request for it."]
    by_source = defaultdict(list)
    for tool in tools:
        by_source[tool.source].append(tool.name)
    for source, names in sorted(by_source.items()):
        if source in KNOWN_DEVICES:
            where = f"the {KNOWN_DEVICES[source]}"
        elif source == "natro":
            where = "you (memory, notes, reminders, web, weather)"
        elif source == "google":
            where = "his Google account (Calendar, Tasks, Gmail, Drive, Docs)"
        elif source == "spotify":
            where = "his Spotify (Premium; plays on any device where Spotify is open)"
        else:
            where = source
        lines.append(f"- Tools from {where}: {', '.join(sorted(names))}.")
    if not tools:
        lines.append("- You have no tools yet. You can talk and answer from your own knowledge, "
                     "and you say so when a request needs a tool you don't have.")
    return "\n".join(lines)
