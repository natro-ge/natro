"""Longer memory: what he asked Natro before and what she answered, found in her log (log.py).

history_search looks through his requests (from his PC and phone, and background jobs) and her replies,
newest first: those with all the words he gives, in what he asked or what she said, within the days he
gives ("that restaurant I asked about on Tuesday"), or all of those days without words ("what did I ask
you yesterday?"). The log is kept for good on the server; requests from test devices are left out.
Personal: only Claude reads it.
"""
import json
from datetime import date, datetime, timedelta

from natro_agent.config import LOG_DIR, timezone
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

# Whose requests count: his devices', and background jobs' (their task and what they found).
DEVICES = {**KNOWN_DEVICES, "job": "background job"}
MOST = 15
ASKED_CHARS = 200
REPLY_CHARS = 400
# How far back a search goes.
MOST_DAYS = 400


def short(text, length):
    text = " ".join(str(text or "").split())
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def stem(word):
    """A word to look for: "restaurants" finds "restaurant" too."""
    word = word.casefold().strip(".,;:!?\"'()")
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


class History:
    """folder: where the log is (logs/)."""

    def __init__(self, folder=None):
        self.folder = folder or LOG_DIR
        self.now = lambda: datetime.now(timezone())

    def tools(self):
        day = {"type": "string", "description": "A day, his local time: 2026-10-14."}
        return [Tool(
            "history_search",
            "Search what he asked you before and what you answered, in earlier conversations too (requests from "
            "his PC and phone, and background jobs), newest first. words: all must be in what he asked or what "
            "you said (\"restaurant\", \"Nino flight\"); start and end: only those days. Without words, everything "
            "in those days (\"what did I ask you yesterday?\").",
            {"type": "object", "properties": {"words": {"type": "string"}, "start": day, "end": day}},
            self.search, "natro", personal=True)]

    async def search(self, args):
        words = [stem(word) for word in str(args.get("words") or "").split()]
        words = [word for word in words if word]
        today = self.now().date()
        first, last = self._day(args.get("start")), self._day(args.get("end"))
        if not words and not first and not last:
            raise ToolError("Give words to look for, or the days (start, end).")
        first = first or today - timedelta(days=MOST_DAYS)
        last = last or (first if args.get("start") and not args.get("end") and not words else today)
        if last < first:
            first, last = last, first
        found = []
        for entry in self._requests(first, last):
            said = f"{entry['text']} {entry.get('reply', '')}".casefold()
            if all(word in said for word in words):
                found.append(entry)
        if not found:
            what = f" with \"{' '.join(words)}\"" if words else ""
            return f"Nothing{what} from {first:%a %d %b %Y} to {last:%a %d %b %Y}."
        found.sort(key=lambda entry: entry["at"], reverse=True)
        lines = [f"- {entry['at']:%a %d %b %Y, %H:%M}, {self._where(entry)}: he asked \"{short(entry['text'], ASKED_CHARS)}\""
                 f"; you said \"{short(entry.get('reply'), REPLY_CHARS)}\"" for entry in found[:MOST]]
        if len(found) > MOST:
            lines.append(f"(and {len(found) - MOST} more: narrow it with more words or fewer days)")
        return "\n".join(lines)

    @staticmethod
    def _day(value):
        if not value:
            return None
        try:
            return date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise ToolError(f"Can't read the day {value!r}: use 2026-10-14.")

    @staticmethod
    def _where(entry):
        if entry["device"] == "job":
            return "a background job"
        return f"on his {DEVICES[entry['device']]}"

    def _requests(self, first, last):
        """His requests from first to last (his days), with "at" in his time. The log's files are named by the
        server's days, so one day more on each side is read."""
        day = first - timedelta(days=1)
        while day <= last + timedelta(days=1):
            path = self.folder / f"agent-{day:%Y-%m-%d}.jsonl"
            day += timedelta(days=1)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if '"kind": "request"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                    at = datetime.fromisoformat(entry["time"]).astimezone(timezone())  # the server's clock
                except (ValueError, KeyError, TypeError):
                    continue
                if (entry.get("kind") == "request" and entry.get("device") in DEVICES and entry.get("text")
                        and not entry.get("cancelled") and first <= at.date() <= last):
                    yield {**entry, "at": at}
