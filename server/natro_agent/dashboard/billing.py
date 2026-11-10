"""The Billing page: a month's bill, by day, model and command, against the budget, and its requests as a CSV."""
import csv
import io
from collections import defaultdict
from datetime import datetime

from natro_agent.config import timezone
from natro_agent.dashboard.home import summarize
from natro_agent.dashboard.logs import family, month_span, number

# What a request did, for the bill: its first tool's service. A tool not listed goes by its name's first word.
COMMANDS = {"calendar": "Calendar", "gmail": "Gmail", "tasks": "Tasks", "drive": "Drive", "docs": "Docs",
            "spotify": "Spotify", "notes": "Notes", "web": "Web", "weather": "Weather", "travel": "Travel",
            "remind": "Reminders", "reminders": "Reminders", "remember": "Memory", "forget": "Memory",
            "history": "Memory", "send_file": "Files", "convert": "Files", "pdf": "Files", "picture": "Pictures",
            "send_message": "Messages", "reply_message": "Messages", "say_out_loud": "Speaking", "pc": "PC",
            "phone": "Phone", "job": "Background jobs", "jobs": "Background jobs", "routine": "Routines",
            "routines": "Routines", "watch": "Watchers", "watchers": "Watchers", "work_on": "Devices"}


def month(logs, budget, value):
    """A month's bill: summarize's numbers by day, what it went on by command, and the budget."""
    now = datetime.now(timezone())
    starts, end = month_span(value, now)
    rows = logs.between(starts[0], end)
    result = summarize(rows, starts, end, "day")
    current = starts[0] <= now < end
    result.update({
        "month": f"{starts[0]:%Y-%m}",
        "current": current,
        "today": (now.date() - starts[0].date()).days if current else None,
        "budget": budget,
        "commands": commands(rows),
        "quick": sum(entry.get("kind") == "request" and family(entry.get("model")) == "quick"
                     for _, _, _, entry in rows),
        "months": logs.months(),
    })
    return result


def month_csv(logs, value):
    """A month's requests, one row each with what it cost: (file name, CSV)."""
    starts, end = month_span(value, datetime.now(timezone()))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["time", "device", "request", "model", "tools", "input_tokens", "cache_read_tokens",
                     "output_tokens", "cost", "voice_cost", "total"])
    for _, _, at, entry in logs.between(starts[0], end):
        if entry.get("kind") != "request":
            continue
        tokens = entry.get("tokens") if isinstance(entry.get("tokens"), dict) else {}
        cost, voice = number(entry.get("cost")), number(entry.get("voice_cost"))
        writer.writerow([at.isoformat(timespec="seconds"), csv_cell(entry.get("device") or ""),
                         csv_cell(entry.get("text") or ""), csv_cell(entry.get("model") or ""),
                         csv_cell(" ".join(str(tool) for tool in entry.get("tools") or [])),
                         number(tokens.get("input_tokens")) + number(tokens.get("cache_creation_input_tokens")),
                         number(tokens.get("cache_read_input_tokens")), number(tokens.get("output_tokens")),
                         cost, voice, round(cost + voice, 6)])
    return f"natro-{starts[0]:%Y-%m}.csv", out.getvalue()


def command(tools, model):
    """A request's line on the bill: its first tool's service; without tools, what answered it."""
    names = [str(tool) for tool in tools or [] if tool]
    if not names:
        return "Quick, no tools" if family(model) == "quick" else "Conversation"
    first = names[0].split("_", 1)[0]
    return COMMANDS.get(names[0]) or COMMANDS.get(first) or first.capitalize()


def commands(rows):
    """What the span's spending went on, by command, costliest first. Every cost is in one line, so they add up
    to what summarize spent."""
    found = defaultdict(lambda: {"times": 0, "cost": 0.0})
    for _, _, _, entry in rows:
        cost = number(entry.get("cost")) + number(entry.get("voice_cost"))
        kind = entry.get("kind")
        if kind == "request":
            name = "Cancelled" if entry.get("cancelled") else command(entry.get("tools"), entry.get("model"))
        elif not cost:
            continue
        else:
            name = {"job": "Background jobs", "voice": "Heard, not answered",
                    "warm": "Keeping the cache warm"}.get(kind, str(kind).capitalize())
        found[name]["times"] += 1
        found[name]["cost"] += cost
    return sorted(({"name": name, "times": item["times"], "cost": round(item["cost"], 6)} for name, item in found.items()),
                  key=lambda item: (-item["cost"], -item["times"], item["name"]))


def csv_cell(value):
    """A spreadsheet cell that can't run as a formula."""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text
