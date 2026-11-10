"""The Speed page: where his requests' time went, from the timing entries (timing.py) and the devices' spoken
ones."""
import statistics
from collections import defaultdict

from natro_agent.dashboard.logs import is_number, number, percentile

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
