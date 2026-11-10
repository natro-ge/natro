"""The Activity page: the log explorer, its entries newest first, filtered, a page at a time, each request with its
tool calls and where its time went."""
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from natro_agent.config import timezone
from natro_agent.dashboard.billing import command
from natro_agent.dashboard.logs import RANGES, as_date, month_span, number, span
from natro_agent.dashboard.speed import stages

LOGS_PAGE = 150


def entries(logs, args):
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
    rows = logs.between(start, end)
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


def cursor(value):
    """"2026-10-19:120" -> (the day, the line number), or None."""
    try:
        day, n = str(value).split(":")
        return date.fromisoformat(day), int(n)
    except ValueError:
        return None
