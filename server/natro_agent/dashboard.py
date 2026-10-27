"""The dashboard: a page on Natro's own address (/dashboard) that shows how she's used and how she's doing:
spending against the month's budget, requests, models, tokens, tools, the devices, the VPS, her reminders,
routines, watchers and jobs, and the log. Read-only: nothing on it changes Natro.

It's served beside the WebSocket (serve's process_request), so it's reachable only where Natro listens: on the
VPS her Tailscale address, never a public one. A web page from anywhere else can't read it either: a request
must name that address (or localhost, or a name in NATRO_DASHBOARD_HOSTS) as its Host, so a site that points a
name of its own at the address (DNS rebinding) gets nothing. The page loads nothing from anywhere else.

GET /dashboard                         the page (web/)
GET /dashboard/api/stats?range=7d      24h, 7d, 30d or month: spending, tokens, requests, models, tools, issues,
                                       where his requests' time went (timing.py), and the same totals for the
                                       span just before, to compare
GET /dashboard/api/live                now: the service, the devices, the VPS, this month's spending, what's
                                       scheduled and running
GET /dashboard/api/logs?range=7d&kinds=request,tool&device=pc&q=flight&before=2026-10-19:120
                                       log entries, newest first, at most LOGS_PAGE (limit= for fewer); after=
                                       instead of before= for those newer than it (the page's live tail); day=
                                       or month= instead of range=. A request comes with its tool calls and
                                       where its time went.
GET /dashboard/api/year                his requests on each day of the past year, from a Sunday (as GitHub
                                       draws them)
GET /dashboard/api/month?month=2026-10 a month's bill: spending by day, model and command, against the budget
GET /dashboard/api/month.csv?month=…   that month's requests and what each cost, to download
"""
import asyncio
import csv
import io
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

from natro_agent import identity
from natro_agent.config import LOG_DIR, ROOT, timezone
from natro_agent.reminders import repeat_words
from natro_agent.watchers import describe

WEB = Path(__file__).resolve().parent / "web"
FILES = {"": "index.html", "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/icon.svg": "icon.svg",
         # Noto Sans, Latin and Georgian (SIL Open Font License, web/fonts/OFL.txt).
         "/fonts/noto-sans.woff2": "fonts/noto-sans.woff2",
         "/fonts/noto-sans-georgian.woff2": "fonts/noto-sans-georgian.woff2"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2"}
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
RANGES = {"24h": 1, "7d": 7, "30d": 30, "month": None}  # days (the month: from its first)
LOGS_PAGE = 150
TOP_TOOLS = 12
RECENT_ISSUES = 15
# What spending is split into: the request's model, by family; voice; background jobs.
FAMILIES = ("opus", "sonnet", "haiku", "gemini")
# Requests from these devices are his; the rest are tests and Natro's own.
DEVICES = ("pc", "phone", "job")
# Model switches and problems, for the issues list.
SWITCHES = {"hand_over", "cheaper_model", "stronger_model"}
PROBLEMS = {"error", "model_error", "model_rest"}
BACKUPS = Path.home() / "backups"
# The parts of a request's time (timing.py) as the breakdown stacks them, in order: recognizing his speech (with the
# Georgian-only retry), translating it, the model until its first token and while it writes, the tools, and the
# device until it starts saying the reply. other is the rest: waiting for the request before it, and the server's
# own work. Waiting for his yes is his time, not Natro's, and is left out.
STAGES = ("speech", "translate", "first_token", "writing", "tools", "speaking", "other")
# Finer parts, for the breakdown's table: each one's median and slowest 5% over the requests it was part of.
PARTS = ("recognize", "georgian", "translate_first", "translate", "wait", "first_token", "writing", "tools",
         "speaking", "other", "waited")
# The kinds of request the breakdown compares: spoken or typed (with a model), and those no model answered.
TIMING_GROUPS = ("spoken", "typed", "quick")
# A model call that wrote this many tokens to the cache read much of its prompt fresh: a cold start.
COLD_WRITE = 2000
SLOWEST = 8
# What a request did, for the bill: its first tool's service. A tool not listed goes by its name's first word.
COMMANDS = {"calendar": "Calendar", "gmail": "Gmail", "tasks": "Tasks", "drive": "Drive", "docs": "Docs",
            "spotify": "Spotify", "notes": "Notes", "web": "Web", "weather": "Weather", "travel": "Travel",
            "remind": "Reminders", "reminders": "Reminders", "remember": "Memory", "forget": "Memory",
            "history": "Memory", "send_file": "Files", "convert": "Files", "pdf": "Files", "picture": "Pictures",
            "send_message": "Messages", "reply_message": "Messages", "say_out_loud": "Speaking", "pc": "PC",
            "phone": "Phone", "job": "Background jobs", "jobs": "Background jobs", "routine": "Routines",
            "routines": "Routines", "watch": "Watchers", "watchers": "Watchers"}


class Logs:
    """The agent's log files (log.py), read as they grow: each day's file is parsed once, then only what's
    been added to it. Safe to use from several threads."""

    def __init__(self, folder=LOG_DIR):
        self.folder = folder
        self._files = {}  # path -> [bytes read, [(line number, his time, entry)]]
        self._counts = {}  # path -> (its size when counted, his requests by day)
        self._lock = threading.Lock()

    def day(self, day):
        """A day's entries (the VPS clock's day, as the files are named), oldest first."""
        path = self.folder / f"agent-{day:%Y-%m-%d}.jsonl"
        try:
            size = path.stat().st_size
        except OSError:
            return []
        with self._lock:
            read, rows = self._files.get(path) or [0, []]
            if size < read:  # a new file in its place
                read, rows = 0, []
            if size > read:
                with open(path, "rb") as f:
                    f.seek(read)
                    data = f.read(size - read)
                end = data.rfind(b"\n") + 1  # a line still being written waits for its end
                for line in data[:end].splitlines():
                    entry = parse(line)
                    if entry is not None:
                        rows.append((len(rows), moment(entry), entry))
                read += end
            self._files[path] = [read, rows]
            if len(self._files) > 100:
                self._files = {key: value for key, value in self._files.items() if key == path}
            return rows

    def between(self, start, end):
        """Entries from start until end (aware), oldest first, each as (day, line number, his time, entry)."""
        first, last = start.astimezone().date(), end.astimezone().date()
        found = []
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            found += [(day, n, at, entry) for n, at, entry in self.day(day) if at and start <= at < end]
        return found

    def requests_per_day(self, first, last):
        """His requests on each day from first to last (dates, his time zone), as a Counter. Each file is counted
        again only when it has grown, so a year of them is cheap after the first look."""
        found = Counter()
        for offset in range((last - first).days + 3):  # a day either side: the files are the VPS clock's days
            path = self.folder / f"agent-{first + timedelta(days=offset - 1):%Y-%m-%d}.jsonl"
            try:
                size = path.stat().st_size
            except OSError:
                continue
            with self._lock:
                known = self._counts.get(path)
                if known is None or known[0] != size:
                    known = self._counts[path] = (size, count_requests(path.read_bytes()))
            for day, count in known[1].items():
                if first <= day <= last:
                    found[day] += count
        return found

    def months(self):
        """The months the log has, oldest first ("2026-10")."""
        return sorted({path.name[6:13] for path in self.folder.glob("agent-*.jsonl")})


def count_requests(data):
    """His requests in a log file's bytes, by day in his time zone."""
    days = Counter()
    for line in data.splitlines():
        if b'"kind": "request"' not in line:
            continue
        entry = parse(line)
        if entry and entry.get("kind") == "request" and entry.get("device") in DEVICES and (at := moment(entry)):
            days[at.date()] += 1
    return days


def parse(line):
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def moment(entry):
    """An entry's time, in his time zone (the log's are the VPS clock's)."""
    try:
        return datetime.fromisoformat(str(entry["time"])).astimezone(timezone())
    except (KeyError, ValueError):
        return None


def span(name, now):
    """A range's buckets' starts, its end, and the bucket ("hour" or "day"), in his time zone."""
    if name == "24h":
        end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return [end - timedelta(hours=24 - i) for i in range(24)], end, "hour"
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = today.day if name == "month" else RANGES[name]
    return [today - timedelta(days=days - 1 - i) for i in range(days)], today + timedelta(days=1), "day"


def month_span(value, now):
    """A month ("2026-10"; anything else: this one): its days' starts and its end, in his time zone."""
    try:
        first = datetime.strptime(str(value), "%Y-%m").replace(tzinfo=now.tzinfo)
    except ValueError:
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    after = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return [first + timedelta(days=i) for i in range((after - first).days)], after


def family(model):
    name = str(model or "").lower()
    if name.startswith("none"):
        return "quick"
    return next((word for word in FAMILIES if word in name), "other")


def spending_source(entry):
    """Where an entry's cost went: a model family, or "jobs"."""
    return "jobs" if entry.get("kind") == "job" else family(entry.get("model"))


def device_group(name):
    return name if name in DEVICES else "other"


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def summarize(rows, starts, end, unit):
    """What happened in rows ([(day, n, his time, entry)]), per bucket of the span and in all."""
    count = len(starts)

    def bucket(at):
        if unit == "hour":
            return int((at - starts[0]).total_seconds() // 3600)
        return (at.date() - starts[0].date()).days

    spend = defaultdict(lambda: [0.0] * count)  # source -> per bucket
    requests = defaultdict(lambda: [0] * count)  # device group -> per bucket
    tokens = defaultdict(lambda: [0] * count)
    seconds = [[] for _ in range(count)]
    models = defaultdict(lambda: {"requests": 0, "cost": 0.0, "seconds": [], "tokens": 0})
    tools = defaultdict(lambda: {"calls": 0, "failed": 0, "declined": 0})
    tool_calls = [0] * count
    week = [[0] * 24 for _ in range(7)]  # Monday first, by hour
    issues = Counter()
    recent = []
    cancelled = 0
    for _, _, at, entry in rows:
        i = bucket(at)
        if not 0 <= i < count:
            continue
        kind = entry.get("kind")
        cost, voice = number(entry.get("cost")), number(entry.get("voice_cost"))
        if cost:
            spend[spending_source(entry)][i] += cost
        if voice:
            spend["voice"][i] += voice
        if kind == "request":
            requests[device_group(entry.get("device"))][i] += 1
            week[at.weekday()][at.hour] += 1
            if entry.get("cancelled"):
                cancelled += 1
                continue
            took = number(entry.get("seconds"))
            if took:
                seconds[i].append(took)
            model = models[str(entry.get("model") or "unknown")]
            model["requests"] += 1
            model["cost"] += cost
            if took:
                model["seconds"].append(took)
            for key, value in (entry.get("tokens") or {}).items():
                if isinstance(value, (int, float)):
                    tokens[key][i] += value
                    model["tokens"] += value
        elif kind == "tool":
            tool = tools[str(entry.get("tool"))]
            tool["calls"] += 1
            tool_calls[i] += 1
            tool["failed"] += not entry.get("ok", True) and not entry.get("declined")
            tool["declined"] += bool(entry.get("declined"))
        if kind in SWITCHES or kind in PROBLEMS:
            issues[kind] += 1
            recent.append(issue(at, entry))
    took_all = [took for bucket_seconds in seconds for took in bucket_seconds]
    total_tokens = {key: sum(values) for key, values in tokens.items()}
    fresh = sum(total_tokens.get(key, 0) for key in ("input_tokens", "cache_creation_input_tokens"))
    cached = total_tokens.get("cache_read_input_tokens", 0)
    tool_list = sorted(({"tool": name, **counts} for name, counts in tools.items()), key=lambda t: -t["calls"])
    return {
        "buckets": [start.isoformat() for start in starts],
        "unit": unit,
        "end": end.isoformat(),
        "spend": {source: [round(value, 6) for value in values] for source, values in spend.items()},
        "spent": round(sum(sum(values) for values in spend.values()), 6),
        "requests": dict(requests),
        "request_count": sum(sum(values) for values in requests.values()),
        "cancelled": cancelled,
        "seconds": {"median": [percentile(values, 0.5) for values in seconds],
                    "p95": [percentile(values, 0.95) for values in seconds],
                    "all_median": percentile(took_all, 0.5), "all_p95": percentile(took_all, 0.95)},
        "tokens": dict(tokens),
        "token_totals": total_tokens,
        "cache_share": cached / (fresh + cached) if fresh + cached else None,
        "models": sorted(({"model": name, "family": family(name), "requests": info["requests"],
                           "cost": round(info["cost"], 6), "tokens": info["tokens"],
                           "median_seconds": percentile(info["seconds"], 0.5)}
                          for name, info in models.items()), key=lambda m: (-m["requests"], m["model"])),
        "tools": tool_list[:TOP_TOOLS],
        "tool_count": len(tool_list),
        "tool_calls": sum(tool_calls),
        "tool_buckets": tool_calls,
        "tool_failed": sum(tool["failed"] for tool in tool_list),
        "tool_declined": sum(tool["declined"] for tool in tool_list),
        "week": week,
        "issues": dict(issues),
        "recent_issues": recent[::-1][:RECENT_ISSUES],
        "timing": timings(rows),
    }


def stages(entry):
    """A timing entry's seconds by part (STAGES; speaking is 0 until the device says it started)."""
    voice = entry.get("voice") if isinstance(entry.get("voice"), dict) else {}
    calls = [call for call in entry.get("calls") or [] if isinstance(call, dict)]
    model = sum(number(call.get("seconds")) for call in calls)
    # A call that wrote nothing (no first token) waited all its time.
    first = sum(number(call.get("first_token")) if call.get("first_token") is not None
                else number(call.get("seconds")) for call in calls)
    parts = {"speech": number(voice.get("recognize")) + number(voice.get("georgian")),
             "translate": number(voice.get("translate")), "first_token": first,
             "writing": max(0.0, model - first), "tools": sum(number(step) for step in entry.get("steps") or []),
             "speaking": 0.0}
    parts["other"] = max(0.0, number(entry.get("total")) - number(entry.get("asking")) - sum(parts.values()))
    return parts


def timings(rows):
    """Where his requests' time went, from the timing entries and the devices' spoken ones: per kind of request
    (TIMING_GROUPS) the average of each part, which add up to the average wait; each finer part's median and
    slowest 5%; the model's first token with the cache warm and cold; how often the Georgian retry ran; and the
    slowest requests."""
    records = []
    newest = {}  # (device, request id) -> its record, for the spoken entry that follows it
    warm, cold = [], []
    for _, _, at, entry in rows:
        kind = entry.get("kind")
        if kind == "timing":
            calls = [call for call in entry.get("calls") or [] if isinstance(call, dict)]
            voice = entry.get("voice") if isinstance(entry.get("voice"), dict) else {}
            record = {"time": at.isoformat(), "device": str(entry.get("device") or ""),
                      "text": str(entry.get("text") or "")[:120], "model": str(entry.get("model") or ""),
                      "group": "quick" if not calls else "spoken" if entry.get("spoken") else "typed",
                      "stages": stages(entry), "voice": voice, "wait": number(entry.get("wait")),
                      "has_tools": bool(entry.get("steps")), "said": False}
            records.append(record)
            newest[(record["device"], str(entry.get("id") or ""))] = record
            for call in calls:
                if is_number(call.get("first_token")):
                    (cold if number(call.get("cache_write")) >= COLD_WRITE else warm).append(call["first_token"])
        elif kind == "spoken":
            record = newest.pop((str(entry.get("device") or ""), str(entry.get("id") or "")), None)
            if record is not None:
                record["stages"]["speaking"] = number(entry.get("seconds"))
                record["said"] = True
    for record in records:
        record["waited"] = round(sum(record["stages"].values()), 3)

    groups = []
    for key in TIMING_GROUPS:
        mine = [record for record in records if record["group"] == key]
        if mine:
            waited = [record["waited"] for record in mine]
            groups.append({"key": key, "count": len(mine),
                           "stages": {stage: round(statistics.fmean(r["stages"][stage] for r in mine), 3)
                                      for stage in STAGES},
                           "median": percentile(waited, 0.5), "p95": percentile(waited, 0.95)})

    def part(record, name):
        """A record's seconds for a finer part, or None if it wasn't part of that request."""
        if name in ("recognize", "georgian", "translate_first", "translate"):
            value = record["voice"].get(name)
            return value if is_number(value) else None
        if name == "wait":
            return record["wait"]
        if name in ("first_token", "writing"):
            return record["stages"][name] if record["group"] != "quick" else None
        if name == "tools":
            return record["stages"]["tools"] if record["has_tools"] else None
        if name == "speaking":
            return record["stages"]["speaking"] if record["said"] else None
        return record["waited"] if name == "waited" else record["stages"][name]

    parts = []
    for name in PARTS:
        values = [value for value in (part(record, name) for record in records) if value is not None]
        parts.append({"key": name, "count": len(values), "median": percentile(values, 0.5),
                      "p95": percentile(values, 0.95)})
    spoken = [record for record in records if "recognize" in record["voice"]]
    said = defaultdict(list)
    for record in records:
        if record["said"]:
            said[record["device"]].append(record["stages"]["speaking"])
    slowest = sorted(records, key=lambda record: -record["waited"])[:SLOWEST]
    return {
        "count": len(records),
        "groups": groups,
        "parts": parts,
        "first_token": {"warm": percentile(warm, 0.5), "warm_count": len(warm),
                        "cold": percentile(cold, 0.5), "cold_count": len(cold)},
        "georgian_share": sum("georgian" in record["voice"] for record in spoken) / len(spoken) if spoken else None,
        "speaking": {device: percentile(said[device], 0.5) for device in ("pc", "phone")},
        "slowest": [{key: record[key] for key in ("time", "device", "text", "model", "group", "waited", "stages")}
                    for record in slowest],
    }


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


def attachments(rows):
    """What belongs to each request, by its (day, line): the tool calls its device logged since its last request,
    and where its time went, from the timing entry logged after it (with the device's word on when it started
    speaking)."""
    found = {}
    tools = defaultdict(list)  # device -> its tool calls since its last request
    last = {}  # device -> (its last request's key, the request's words)
    by_id = {}  # (device, timing id) -> request key
    for day, n, _, entry in rows:
        kind, device = entry.get("kind"), str(entry.get("device") or "")
        if kind == "tool":
            tools[device].append({key: entry[key] for key in ("tool", "args", "ok", "declined") if key in entry})
        elif kind == "request":
            found[(day, n)] = {"tools": tools.pop(device, [])}
            last[device] = ((day, n), str(entry.get("text") or "")[:200])
        elif kind == "timing" and device in last:
            key, text = last.pop(device)
            if str(entry.get("text") or "") == text:
                found[key]["stages"] = stages(entry)
                by_id[(device, str(entry.get("id") or ""))] = key
        elif kind == "spoken":
            key = by_id.pop((device, str(entry.get("id") or "")), None)
            if key is not None:
                found[key]["stages"]["speaking"] = number(entry.get("seconds"))
    return found


def csv_cell(value):
    """A spreadsheet cell that can't run as a formula."""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def totals(rows):
    """The headline numbers of a span, to compare with the one after it."""
    spent = requests = tool_calls = tool_failed = 0
    took = []
    for _, _, _, entry in rows:
        spent += number(entry.get("cost")) + number(entry.get("voice_cost"))
        if entry.get("kind") == "request":
            requests += 1
            if not entry.get("cancelled") and number(entry.get("seconds")):
                took.append(number(entry.get("seconds")))
        elif entry.get("kind") == "tool":
            tool_calls += 1
            tool_failed += not entry.get("ok", True) and not entry.get("declined")
    return {"spent": round(spent, 6), "request_count": requests, "median_seconds": percentile(took, 0.5),
            "tool_calls": tool_calls, "tool_failed": tool_failed}


def issue(at, entry):
    kind = entry.get("kind")
    if kind == "hand_over":
        text = entry.get("reason") or ""
    elif kind in ("cheaper_model", "stronger_model"):
        text = f"{entry.get('model')} → {entry.get('switched_to')}: {entry.get('reason') or ''}"
    elif kind == "model_rest":
        failures = entry.get("failures")
        text = f"{entry.get('model')} rests {entry.get('seconds')} s after {failures} failure{'' if failures == 1 else 's'}"
    elif kind == "model_error":
        text = f"{entry.get('model')}: {entry.get('error')}" + (
            f" (switched to {entry['switched_to']})" if entry.get("switched_to") else "")
    else:
        text = f"{entry.get('step') or entry.get('device') or ''}: {entry.get('error') or ''}"
    return {"time": at.isoformat(), "kind": kind, "text": str(text)[:300]}


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number(value):
    return value if is_number(value) else 0


def matches(entry, kinds, device, words):
    """kinds: the kinds to show, or ones to leave out written "-tool" (the rest are shown)."""
    shown = {kind for kind in kinds if not kind.startswith("-")}
    if shown and entry.get("kind") not in shown or f"-{entry.get('kind')}" in kinds:
        return False
    if device and entry.get("device") != device:
        return False
    if not words:
        return True
    text = json.dumps({key: value for key, value in entry.items() if key != "time"}, ensure_ascii=False).casefold()
    return all(word in text for word in words)


def as_date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def cursor(value):
    """"2026-10-19:120" -> (the day, the line number), or None."""
    try:
        day, n = str(value).split(":")
        return date.fromisoformat(day), int(n)
    except ValueError:
        return None


class Host:
    """The VPS (Linux; elsewhere, what Python can tell): CPU, memory, disk, load; and Natro's own process."""

    def __init__(self):
        self._cpu = None  # (busy, total) at the last look
        self._own = None  # (her CPU seconds, when)

    def now(self):
        memory = meminfo()
        disk = shutil.disk_usage(ROOT)
        return {
            "name": socket.gethostname(),
            "system": f"{platform.system()} {platform.release()}",
            "cores": os.cpu_count(),
            "cpu": self.cpu(),
            "load": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "memory": memory,
            "disk": {"total": disk.total, "used": disk.used},
            "uptime": read_float("/proc/uptime"),
        }

    def cpu(self):
        """The share of CPU time busy since the last look (the first: over a moment)."""
        try:
            first = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        except OSError:
            return None
        values = [int(value) for value in first]
        idle, total = values[3] + (values[4] if len(values) > 4 else 0), sum(values)
        if self._cpu is None:
            self._cpu = (total - idle, total)
            time.sleep(0.2)
            return self.cpu()
        busy_before, total_before = self._cpu
        self._cpu = (total - idle, total)
        return (total - idle - busy_before) / (total - total_before) if total > total_before else 0.0

    def own(self):
        """Natro's process: its memory (and its service's limit, under systemd) and CPU since the last look."""
        result = {"pid": os.getpid(), "memory": None, "memory_max": None, "cpu": None}
        try:
            group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
            folder = Path("/sys/fs/cgroup") / group.lstrip("/")
            result["memory"] = int((folder / "memory.current").read_text())
            limit = (folder / "memory.max").read_text().strip()
            result["memory_max"] = int(limit) if limit.isdigit() else None
        except (OSError, IndexError, ValueError):
            for line in read_lines("/proc/self/status"):
                if line.startswith("VmRSS:"):
                    result["memory"] = int(line.split()[1]) * 1024
        try:
            fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
            seconds = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
            now = time.monotonic()
            if self._own:
                result["cpu"] = (seconds - self._own[0]) / max(now - self._own[1], 1e-6)
            self._own = (seconds, now)
        except (OSError, IndexError, ValueError, AttributeError):
            pass
        return result


def meminfo():
    values = {}
    for line in read_lines("/proc/meminfo"):
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0]) * 1024
    if "MemTotal" not in values:
        return None
    return {"total": values["MemTotal"], "available": values.get("MemAvailable")}


def read_lines(path):
    try:
        return Path(path).read_text().splitlines()
    except OSError:
        return []


def read_float(path):
    try:
        return float(Path(path).read_text().split()[0])
    except (OSError, IndexError, ValueError):
        return None


def version():
    """The commit Natro runs: short hash, date, subject."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h%x00%cI%x00%s"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", timeout=5).stdout.strip()
        commit, when, subject = out.split("\x00", 2)
        return {"commit": commit, "date": when, "subject": subject}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def backups(folder=BACKUPS):
    """The nightly copies of memory and notes (server/deploy/backup.sh)."""
    try:
        found = sorted(folder.glob("natro-*.tar.gz"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return None
    if not found:
        return None
    newest = found[-1].stat()
    return {"count": len(found), "last": datetime.fromtimestamp(newest.st_mtime, timezone()).isoformat(),
            "size": newest.st_size}


class Dashboard:
    """server: the server.Server whose state the page shows. listen: the host Natro listens on."""

    def __init__(self, server, listen="127.0.0.1", logs=None):
        self.server = server
        self.hosts = LOCAL_HOSTS | {listen.strip("[]")} | {
            name.strip().lower() for name in os.environ.get("NATRO_DASHBOARD_HOSTS", "").split(",") if name.strip()}
        self.logs = logs or Logs()
        self.host = Host()
        self.started = time.time()
        self.version = version()

    async def http(self, connection, request):
        """serve()'s process_request for the dashboard's addresses (None for any other: a WebSocket)."""
        path, _, query = request.path.partition("?")
        if path == "/" and request.headers.get("Upgrade", "").lower() != "websocket":  # a browser at her address
            response = connection.respond(302, "")
            response.headers["Location"] = "/dashboard"
            return response
        if path != "/dashboard" and not path.startswith("/dashboard/"):
            return None
        if host_name(request.headers.get("Host")) not in self.hosts:
            return connection.respond(403, "Open the dashboard at the address Natro listens on.\n")
        path = path.removeprefix("/dashboard")
        args = {key: values[-1] for key, values in parse_qs(query).items()}
        try:
            if path in FILES:
                return page(connection, WEB / FILES[path])
            if path == "/api/stats":
                return reply(connection, await asyncio.to_thread(self.stats, args.get("range", "7d")))
            if path == "/api/live":
                return reply(connection, await self.live())
            if path == "/api/logs":
                return reply(connection, await asyncio.to_thread(self.entries, args))
            if path == "/api/year":
                return reply(connection, await asyncio.to_thread(self.year))
            if path == "/api/month":
                return reply(connection, await asyncio.to_thread(self.month, args.get("month")))
            if path == "/api/month.csv":
                return download(connection, *await asyncio.to_thread(self.month_csv, args.get("month")))
        except Exception as e:  # the page says so; Natro goes on
            return reply(connection, {"error": repr(e)}, 500)
        return connection.respond(404, "Not found.\n")

    def stats(self, name):
        if name not in RANGES:
            name = "7d"
        starts, end, unit = span(name, datetime.now(timezone()))
        result = summarize(self.logs.between(starts[0], end), starts, end, unit)
        length = end - starts[0]
        result["range"] = name
        result["before"] = totals(self.logs.between(starts[0] - length, min(end, datetime.now(timezone())) - length))
        return result

    async def live(self):
        server = self.server
        agent = server.agent
        connected = {name: device for name, device in list(agent.devices.items()) if hasattr(device, "connection")}
        devices = []
        for name in dict.fromkeys([*identity.KNOWN_DEVICES, *connected]):
            device = connected.get(name)
            used = server._used.get(name)
            devices.append({
                "name": name,
                "label": device.label if device else identity.KNOWN_DEVICES.get(name, name),
                "online": device is not None,
                "since": stamp(getattr(device, "since", None)),
                "used": stamp(time.time() - (time.monotonic() - used)) if used else None,
                "tools": len(device.specs) if device else None,
                "abilities": [ability for ability in ("files", "chat", "reminders", "jobs", "alerts", "questions",
                                                      "phone_messages")
                              if device and getattr(device, ability, False)],
            })
        last_seen = await asyncio.to_thread(self.last_seen)
        for device in devices:
            device["seen"] = last_seen.get(device["name"])
        spent, budget = agent.ledger.spent, agent.ledger.budget
        now = datetime.now(timezone())
        month_days = ((now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
        elapsed = (now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
        return {
            "now": now.isoformat(),
            "service": {
                "started": stamp(self.started),
                "version": self.version,
                "python": platform.python_version(),
                "voice": server.voice is not None,
                "model": getattr(agent, "model", None),
                "process": self.host.own(),
            },
            "month": {"spent": round(spent, 4), "budget": budget, "days": month_days, "elapsed_days": elapsed,
                      "projected": round(spent / elapsed * month_days, 4) if elapsed > 0.5 else None},
            "devices": devices,
            "host": await asyncio.to_thread(self.host.now),
            "backups": backups(),
            "jobs": [job_item(job) for job in reversed(server.jobs.items)] if server.jobs else [],
            "reminders": reminder_items(server.reminders, now) if server.reminders else [],
            "routines": [routine_item(item) for item in server.routines.items] if server.routines else [],
            "watchers": [{"id": item["id"], "kind": item.get("kind"), "text": describe_safely(item),
                          "urgent": bool(item.get("urgent")), "once": bool(item.get("once"))}
                         for item in server.watchers.items] if server.watchers else [],
            "pending": len(server.pending.items) if server.pending else 0,
        }

    def last_seen(self):
        """When each device last connected or went, from the past week's log."""
        now = datetime.now(timezone())
        seen = {}
        for _, _, at, entry in self.logs.between(now - timedelta(days=7), now + timedelta(minutes=1)):
            if entry.get("kind") == "device":
                seen[entry.get("device")] = at.isoformat()
        return seen

    def year(self):
        """His requests per day for the past year, as GitHub draws it: from the Sunday a year back to today."""
        today = datetime.now(timezone()).date()
        first = today - timedelta(days=365)
        first -= timedelta(days=(first.weekday() + 1) % 7)
        counts = self.logs.requests_per_day(first, today)
        return {"start": first.isoformat(), "today": today.isoformat(),
                "counts": [counts.get(first + timedelta(days=i), 0) for i in range((today - first).days + 1)]}

    def month(self, value):
        """A month's bill: summarize's numbers by day, what it went on by command, and the budget."""
        now = datetime.now(timezone())
        starts, end = month_span(value, now)
        rows = self.logs.between(starts[0], end)
        result = summarize(rows, starts, end, "day")
        current = starts[0] <= now < end
        result.update({
            "month": f"{starts[0]:%Y-%m}",
            "current": current,
            "today": (now.date() - starts[0].date()).days if current else None,
            "budget": self.server.agent.ledger.budget,
            "commands": commands(rows),
            "quick": sum(entry.get("kind") == "request" and family(entry.get("model")) == "quick"
                         for _, _, _, entry in rows),
            "months": self.logs.months(),
        })
        return result

    def month_csv(self, value):
        """A month's requests, one row each with what it cost: (file name, CSV)."""
        starts, end = month_span(value, datetime.now(timezone()))
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["time", "device", "request", "model", "tools", "input_tokens", "cache_read_tokens",
                         "output_tokens", "cost", "voice_cost", "total"])
        for _, _, at, entry in self.logs.between(starts[0], end):
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

    def entries(self, args):
        """Log entries for the explorer: newest first, filtered, a page at a time."""
        now = datetime.now(timezone())
        if day := as_date(args.get("day")):
            start = datetime(day.year, day.month, day.day, tzinfo=timezone())
            end = start + timedelta(days=1)
        elif args.get("month"):
            starts, end = month_span(args["month"], now)
            start = starts[0]
        else:
            name = args.get("range", "7d") if args.get("range") in RANGES else "7d"
            starts, end, _ = span(name, now)
            start, end = starts[0], end + timedelta(minutes=1)
        limit = min(int(args["limit"]), LOGS_PAGE) if str(args.get("limit", "")).isdigit() else LOGS_PAGE
        kinds = {kind for kind in args.get("kinds", "").split(",") if kind}
        device = args.get("device") or ""
        words = args.get("q", "").casefold().split()
        before, after = cursor(args.get("before")), cursor(args.get("after"))
        rows = self.logs.between(start, end)
        extra = attachments(rows)
        found, kind_counts = [], Counter()
        for day, n, at, entry in reversed(rows):
            if after and (day, n) <= after:
                break
            if before and (day, n) >= before:
                continue
            if not matches(entry, kinds, device, words):
                continue
            kind_counts[entry.get("kind")] += 1
            if len(found) < limit:
                item = {"id": f"{day:%Y-%m-%d}:{n}", "time": at.isoformat(), "entry": entry, **extra.get((day, n), {})}
                if entry.get("kind") == "request":
                    item["command"] = "Cancelled" if entry.get("cancelled") else command(entry.get("tools"), entry.get("model"))
                found.append(item)
        return {"entries": found, "more": sum(kind_counts.values()) > len(found), "matched": sum(kind_counts.values()),
                "kinds": sorted(Counter(entry.get("kind") for _, _, _, entry in rows).items(), key=lambda k: -k[1])}


def host_name(header):
    """The host a request names, without its port: "100.101.107.5:8700" -> "100.101.107.5"."""
    host = str(header or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]")[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone()).isoformat() if seconds else None


def job_item(job):
    return {"id": job.id, "title": job.title, "task": job.task[:300], "state": job.state, "step": job.step,
            "started": stamp(job.started), "finished": stamp(job.finished), "minutes": job.minutes(),
            "result": job.result[:300], "routine": bool(job.routine)}


def reminder_items(reminders, now):
    items = []
    for item in reminders.items:
        repeat = item.get("repeat") or ""
        items.append({"id": item["id"], "text": item["text"], "next": item.get("next"),
                      "repeat": safely(repeat_words, repeat) if repeat else "", "on": item.get("on")})
    return sorted(items, key=lambda item: (item["next"] is None, item["next"] or ""))


def routine_item(item):
    repeat = item.get("repeat") or ""
    last = item.get("last") or None
    if last and isinstance(last.get("at"), (int, float)):
        last = {**last, "at": stamp(last["at"] / 1000)}
    return {"id": item["id"], "name": item.get("name") or "", "task": str(item.get("task") or "")[:300],
            "next": item.get("next"), "repeat": safely(repeat_words, repeat) if repeat else "once",
            "quiet": bool(item.get("quiet")), "last": last}


def describe_safely(item):
    return safely(describe, item) or item.get("kind", "")


def safely(function, value):
    try:
        return function(value)
    except Exception:  # one odd item must not take the page down
        return ""


def page(connection, path):
    response = connection.respond(200, "")
    response.body = path.read_bytes()
    set_headers(response, TYPES.get(path.suffix, "application/octet-stream"))
    return response


def download(connection, name, text):
    response = connection.respond(200, "")
    response.body = text.encode("utf-8-sig")  # with a BOM, so spreadsheets read it as UTF-8 (Georgian too)
    set_headers(response, "text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


def reply(connection, data, status=200):
    response = connection.respond(status, "")
    response.body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    set_headers(response, "application/json; charset=utf-8")
    return response


def set_headers(response, content_type):
    headers = response.headers
    for key, value in {"Content-Type": content_type, "Content-Length": str(len(response.body)), **HEADERS}.items():
        if key in headers:
            del headers[key]
        headers[key] = value
