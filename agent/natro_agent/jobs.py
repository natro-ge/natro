"""Background jobs: long requests Natro works on while he goes on talking to her ("find three flats under $600
and put them in a note").

start_job gives a task, written out complete, to a job: a conversation of its own with Claude
(Agent.worker), up to MAX_STEPS model steps, running beside his requests; at most MOST_AT_ONCE at once.
Natro starts one for long work on her own judgment, and whenever he asks for something "in the background".
The server runs it (Server._job): it shows in the shared chat as it works (its steps, any question), a step
that needs his yes asks on the device he used last, and when it's done the result goes to his devices
(job_done: shown on every device that said "jobs" in its hello, its first paragraph spoken on one), and the
next request is answered by Claude, who is told it. jobs_list ("what are you working on?") and jobs_cancel ("cancel that")
work on them. Kept in memory only: a restart of the server ends the jobs running then.

Natro to device:
  {"type": "job_done", "id": "jb3", "task": "Find three flats...", "text": "I found three...",
   "say": "I found three...", "ok": true, "speak": true}
                                        text: all of it, to show; say: what to say out loud (its first
                                        paragraph); ok: false when it failed (text says so); speak: on this device
"""
import itertools
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

MOST_AT_ONCE = 3
MAX_STEPS = 30
MOST_TASK = 1000
# Finished jobs are listed this long, for "what did you find?" and "how did it go?".
FINISHED_HOURS = 24
MOST_KEPT = 30
RESULT_CHARS = 600
# The most of a result said out loud (its first paragraph).
SAY_CHARS = 400

# The job the current task works on, if it's a job's (set by the server): a job doesn't start jobs.
RUNNING = ContextVar("job", default=None)


@dataclass
class Job:
    id: str
    task: str
    asked_on: str  # the device he asked on ("pc", "phone"), or ""
    started: float = field(default_factory=time.time)
    state: str = "running"  # running, done, failed, cancelled
    step: str = ""  # what it's doing now
    result: str = ""
    finished: float = 0.0
    task_handle: object = None  # the asyncio task running it

    def minutes(self, now=None):
        return max(0, round(((self.finished or now or time.time()) - self.started) / 60))


def short(text, length=RESULT_CHARS):
    text = " ".join(str(text or "").split())
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def lead(text):
    """What's said out loud of a job's result: its first paragraph (the answer); the rest is for reading."""
    first = next((part for part in str(text or "").split("\n\n") if part.strip()), "")
    return short(first, SAY_CHARS)


class Jobs:
    """The server sets run(job) -> the asyncio task running it, and asking() -> the device the request being
    handled came from."""

    def __init__(self):
        self.items = []  # oldest first
        self.run = lambda job: None
        self.asking = lambda: None
        self._ids = itertools.count(1)

    def running(self):
        return [job for job in self.items if job.state == "running"]

    def tools(self):
        return [
            Tool("start_job",
                 "Work on a long request in the background, so he can go on talking to you: research that takes "
                 "several searches or reading many pages, comparing options, writing a note or doc from what you "
                 "find. Also whenever he asks for something in the background or says to take your time. The job "
                 "doesn't see this conversation: task is the whole request, complete, with every detail he gave "
                 "(where, how many, the price, and where he wants the result if he said), and nothing he didn't "
                 "ask for. He hears the result when it's done.",
                 {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
                 self.start, "natro", personal=True, final=True),
            Tool("jobs_list",
                 "What you're working on in the background (with ids and what each is doing now), and the jobs "
                 "that finished in the last day with what they found.",
                 {"type": "object", "properties": {}}, self.list, "natro", personal=True),
            Tool("jobs_cancel",
                 "Stop a background job by its id (from jobs_list).",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                 self.cancel, "natro", personal=True),
        ]

    # Tools

    async def start(self, args):
        if RUNNING.get() is not None:
            raise ToolError("You're a background job yourself: do this part here.")
        task = " ".join(str(args.get("task") or "").split())
        if not task:
            raise ToolError("Say what the job is (task).")
        if len(task) > MOST_TASK:
            raise ToolError(f"Keep the task under {MOST_TASK} characters.")
        if len(busy := self.running()) >= MOST_AT_ONCE:
            names = "; ".join(f"{job.id}: {short(job.task, 60)}" for job in busy)
            raise ToolError(f"You're already working on {MOST_AT_ONCE} jobs ({names}). Tell him; he can wait or "
                            "cancel one.")
        asked_on = getattr(self.asking(), "name", "")
        job = Job(f"jb{next(self._ids)}", task, asked_on if asked_on in KNOWN_DEVICES else "")
        self.items.append(job)
        self._forget_old()
        job.task_handle = self.run(job)
        return f"OK, I'm on it. I'll tell you when it's done.\nStarted job {job.id}: \"{task}\""

    async def list(self, args):
        now = time.time()
        self._forget_old()
        lines = []
        for job in self.running():
            doing = f", now: {job.step}" if job.step else ""
            lines.append(f"- {job.id} (started {job.minutes(now)} min ago{doing}): \"{job.task}\"")
        lines = ["Working on:", *lines] if lines else ["Nothing in the background right now."]
        finished = [job for job in self.items if job.state != "running"]
        if finished:
            lines.append("Finished in the last day:")
            for job in reversed(finished):
                ago = max(0, round((now - job.finished) / 60))
                lines.append(f"- {job.id}, {job.state} {ago} min ago, took {job.minutes()} min: \"{job.task}\"\n"
                             f"  {short(job.result) or '(nothing said)'}")
        return "\n".join(lines)

    async def cancel(self, args):
        wanted = str(args.get("id") or "").strip()
        job = next((job for job in self.items if job.id == wanted), None)
        if job is None:
            raise ToolError(f"There's no job {wanted!r}: jobs_list has their ids.")
        if job.state != "running":
            return f"Job {job.id} has already {'been cancelled' if job.state == 'cancelled' else job.state}."
        if job.task_handle is not None:
            job.task_handle.cancel()
        self.ended(job, "cancelled", "")
        return f"Cancelled: \"{job.task}\""

    # From the server

    def ended(self, job, state, result):
        if job.state == "running":
            job.state, job.result, job.finished, job.step = state, result, time.time(), ""

    def _forget_old(self):
        oldest = time.time() - FINISHED_HOURS * 3600
        kept = [job for job in self.items if job.state == "running" or job.finished >= oldest]
        self.items = kept[-MOST_KEPT:] if len(kept) > MOST_KEPT else kept
