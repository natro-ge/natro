"""What "this" and "that" mean: what's in front of him, on the device a request works on (works_on.py).

Each device offers a tool called this (desktop/src-tauri/src/tools/this.rs, the phone's PhoneTools.kt), which says by name
what he has in front of him: the window or app, the file open in it, the page, the text he selected, what's playing. What's
in a window or a file stays on the device until she reads it with another tool.

When his words point at something (routing.says_this), the agent asks the device before the model reads the request, and
the answer goes in a line before his words, so "convert this to a PDF" takes no model call to find out what "this" is. A
request that doesn't point at anything asks the device nothing. What the words miss ("what's this on my phone?", or other
words for it) is left to her, with the tool. The line travels with the request, so the instructions and tools stay the same.
"""
import asyncio
import re
import time

from natro_agent import log
from natro_agent.tools import tool_name
from natro_agent.works_on import works_where

# A device answers in a fraction of a second: past this she goes without the line, and can ask with the tool.
LOOK_SECONDS = 3
LINE_CHARS = 6000


def line(label, seen):
    """A device's answer as one line for the request. It comes from window titles and selected text, which anyone can
    write, so it can't end its own brackets or start a line that passes for the system's or his."""
    flat = re.sub(r"\s*\n\s*", " · ", seen.strip())
    flat = " ".join(flat.replace("[", "(").replace("]", ")").split())
    if len(flat) > LINE_CHARS:
        flat = flat[:LINE_CHARS] + "… (cut)"
    return f"[in front of him on his {label} · {flat}]" if flat else ""


async def in_front(device, devices, timing):
    """The line for a request made on device, from the device it works on, or "" if it can't say in time (an app too old
    to have the tool, say)."""
    where = devices.get(works_where(device, devices))
    name = tool_name(where.name, "this") if where else ""
    tool = next((tool for tool in where.tools() if tool.name == name), None) if where else None
    if tool is None:
        return ""
    began, problem, seen = time.monotonic(), "", ""
    try:
        seen = await asyncio.wait_for(tool.run({}), LOOK_SECONDS)
    except asyncio.TimeoutError:
        problem = "too slow"
    except Exception as e:  # a look that's only there to help never stops the request
        problem = str(e) or repr(e)
    seconds = time.monotonic() - began
    timing.tool(name, where.name, seconds, not problem)
    # Only whether it answered: what's in front of him isn't for the log.
    log.write("this", device=device.name, on=where.name, seconds=round(seconds, 3), chars=len(seen), problem=problem[:200])
    return line(where.label, seen)
