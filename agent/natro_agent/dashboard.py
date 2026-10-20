"""The dashboard: a page on Natro's own address (/dashboard) that shows how she's used and how she's doing:
spending against the month's budget, requests, models, tokens, tools, the devices, the VPS, her reminders,
routines, watchers and jobs, and the log. Read-only: nothing on it changes Natro.

It's served beside the WebSocket (serve's process_request), so it's reachable only where Natro listens: on the
VPS her Tailscale address, never a public one. A web page from anywhere else can't read it either: a request
must name that address (or localhost, or a name in NATRO_DASHBOARD_HOSTS) as its Host, so a site that points a
name of its own at the address (DNS rebinding) gets nothing. The page loads nothing from anywhere else.

GET /dashboard                         the page (web/)
GET /dashboard/api/stats?range=7d      24h, 7d, 30d or month: spending, tokens, requests, models, tools, issues,
                                       and the same totals for the span just before, to compare
GET /dashboard/api/live                now: the service, the devices, the VPS, this month's spending, what's
                                       scheduled and running
GET /dashboard/api/logs?range=7d&kinds=request,tool&device=pc&q=flight&before=2026-10-19:120
                                       log entries, newest first, at most LOGS_PAGE; after= instead of before=
                                       for those newer than it (the page's live tail)
"""
import asyncio
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
FILES = {"": "index.html", "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/icon.svg": "icon.svg"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
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


class Logs:
    """The agent's log files (log.py), read as they grow: each day's file is parsed once, then only what's
    been added to it. Safe to use from several threads."""

    def __init__(self, folder=LOG_DIR):
        self.folder = folder
        self._files = {}  # path -> [bytes read, [(line number, his time, entry)]]
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
    }


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


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def matches(entry, kinds, device, words):
    if kinds and entry.get("kind") not in kinds:
        return False
    if device and entry.get("device") != device:
        return False
    if not words:
        return True
    text = json.dumps({key: value for key, value in entry.items() if key != "time"}, ensure_ascii=False).casefold()
    return all(word in text for word in words)


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
    """The nightly copies of memory and notes (deploy/backup.sh)."""
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

    def entries(self, args):
        """Log entries for the explorer: newest first, filtered, a page at a time."""
        name = args.get("range", "7d") if args.get("range") in RANGES else "7d"
        starts, end, _ = span(name, datetime.now(timezone()))
        kinds = {kind for kind in args.get("kinds", "").split(",") if kind}
        device = args.get("device") or ""
        words = args.get("q", "").casefold().split()
        before, after = cursor(args.get("before")), cursor(args.get("after"))
        rows = self.logs.between(starts[0], end + timedelta(minutes=1))
        found, kind_counts = [], Counter()
        for day, n, at, entry in reversed(rows):
            if after and (day, n) <= after:
                break
            if before and (day, n) >= before:
                continue
            if not matches(entry, kinds, device, words):
                continue
            kind_counts[entry.get("kind")] += 1
            if len(found) < LOGS_PAGE:
                found.append({"id": f"{day:%Y-%m-%d}:{n}", "time": at.isoformat(), "entry": entry})
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
