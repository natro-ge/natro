"""The log as the dashboard reads it: the agent's daily files (log.py), parsed as they grow, and what every page
shares: spans of time, the model families, the devices that are his, and safe numbers."""
import json
import threading
from collections import Counter
from datetime import date, datetime, timedelta

from natro_agent.config import LOG_DIR, timezone

RANGES = {"24h": 1, "7d": 7, "30d": 30, "month": None}  # days (the month: from its first)
# What spending is split into: the request's model, by family; voice; background jobs. "gemini" is the free
# tier that went on 2026-10-20, kept while Billing can show a month whose logs have it.
FAMILIES = ("opus", "sonnet", "haiku", "gemini")
# Requests from these devices are his; the rest are tests and Natro's own.
DEVICES = ("pc", "phone", "job")


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


def device_group(name):
    return name if name in DEVICES else "other"


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number(value):
    return value if is_number(value) else 0


def as_date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
