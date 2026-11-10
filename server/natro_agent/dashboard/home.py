"""The Home page: a span's spending, requests, models, tokens, tools and issues, per bucket and in all, beside the
span just before it; and his requests on each day of the past year."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from natro_agent.config import timezone
from natro_agent.dashboard.logs import RANGES, device_group, family, number, percentile, span
from natro_agent.dashboard.speed import timings

TOP_TOOLS = 12
RECENT_ISSUES = 15
# Model switches and problems, for the issues list.
SWITCHES = {"hand_over", "cheaper_model", "stronger_model"}
PROBLEMS = {"error", "model_error", "model_rest"}


def stats(logs, name):
    """A range's numbers (RANGES; anything else: 7d), and the totals of the span just before, to compare."""
    if name not in RANGES:
        name = "7d"
    starts, end, unit = span(name, datetime.now(timezone()))
    result = summarize(logs.between(starts[0], end), starts, end, unit)
    length = end - starts[0]
    result["range"] = name
    result["before"] = totals(logs.between(starts[0] - length, min(end, datetime.now(timezone())) - length))
    return result


def year(logs):
    """His requests per day for the past year, as GitHub draws it: from the Sunday a year back to today."""
    today = datetime.now(timezone()).date()
    first = today - timedelta(days=365)
    first -= timedelta(days=(first.weekday() + 1) % 7)
    counts = logs.requests_per_day(first, today)
    return {"start": first.isoformat(), "today": today.isoformat(),
            "counts": [counts.get(first + timedelta(days=i), 0) for i in range((today - first).days + 1)]}


def spending_source(entry):
    """Where an entry's cost went: a model family, or "jobs"."""
    return "jobs" if entry.get("kind") == "job" else family(entry.get("model"))


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
