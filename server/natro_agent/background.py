"""Background jobs as they run, and routines' runs as they start: a job (jobs.py) is a task of its own beside his
requests, in the shared chat as it works, and what it found goes to his devices (delivery.py); a routine
(routines.py) that's due, or that he asked to run now, runs as a job titled with its name."""
import asyncio
from datetime import datetime

from natro_agent import identity, log
from natro_agent.aloud import SAID
from natro_agent.config import timezone
from natro_agent.devices import JobDevice
from natro_agent.jobs import MAX_STEPS as JOB_STEPS, RUNNING, short
from natro_agent.routines import QUIET_REPLY, is_quiet
from natro_agent.works_on import switched_to


class Background:
    """agent: the Agent, whose workers do the jobs. chat: the chats.SharedChat they show in. delivery: the
    delivery.Delivery that shows what they found and knows where to ask. jobs: the jobs.Jobs it runs. routines: the
    routines.Routines whose runs it starts (None: none)."""

    def __init__(self, agent, chat, delivery, jobs, routines=None):
        self.agent = agent
        self.chat = chat
        self.delivery = delivery
        self.jobs = jobs
        jobs.run = self.run_job
        self.routines = routines
        if routines:
            routines.start = self.routine
        # Set by the server: from here on, this task handles the request with this chat entry, from this device.
        self.handling = lambda entry, device: None
        self._tasks = set()

    def run_job(self, job):
        """Start a background job (jobs.Jobs calls this); returns its task."""
        task = asyncio.create_task(self._job(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _job(self, job):
        """A background job: Natro works on it beside his requests, in the chat as it goes; then what she found
        goes to his devices, and the next request Claude answers hears it."""
        RUNNING.set(job)
        entry = self.chat.job(job.id, job.task, routine=job.title if job.routine else "")
        asker = self.agent.devices.get(job.asked_on)
        self.handling(entry, asker)
        SAID.set(None)  # started from one of his requests: its reply isn't the job's
        now = datetime.now(timezone())
        if job.routine:
            what = (f"A routine he set up, \"{job.title}\": it runs by itself now, at {now:%H:%M}, and he isn't "
                    "waiting for it")
        else:
            asked_on = identity.KNOWN_DEVICES.get(job.asked_on, "device")
            what = (f"A background job: he asked for this on his {asked_on} at {now:%H:%M} and is doing other things "
                    "meanwhile")
        quiet = (" He wants to hear only when there's something to report: if there's nothing, reply with only "
                 f"\"{QUIET_REPLY}\"") if job.quiet else ""
        context = (f"[{what}. Work through it to the end on your own. When you're done, your reply's first paragraph "
                   "is said to him out loud: the answer in a sentence or two. The details he'd want can follow, in "
                   f"short paragraphs without lists; he reads those.{quiet}]")

        def step(step):
            job.step = step.get("text", "")
            self.chat.step(entry, job.step)

        log.write("job", id=job.id, event="started", task=job.task)
        try:
            reply = await self.agent.worker(JOB_STEPS).respond(
                job.task, JobDevice(self.delivery, switched_to(asker)), on_text=lambda piece: self.chat.piece(entry, piece),
                on_step=step, context=context)
        except asyncio.CancelledError:
            self.chat.replied(entry, "", problem="Cancelled")
            self.jobs.ended(job, "cancelled", "")
            self._routine_ran(job, "cancelled")
            log.write("job", id=job.id, event="cancelled")
            raise
        except Exception as e:
            log.write("error", step="job", id=job.id, error=repr(e))
            text = (f"The routine \"{job.title}\" failed: {e}" if job.routine
                    else f"The job \"{short(job.task, 80)}\" failed: {e}")
            self.chat.replied(entry, "", problem=text)
            self.jobs.ended(job, "failed", text)
            self._routine_ran(job, "failed", text)
            self.delivery.job_done(job, text, ok=False)
            return
        quiet = job.quiet and is_quiet(reply.text)
        self.chat.replied(entry, reply.text)
        self.jobs.ended(job, "done", reply.text)
        self._routine_ran(job, "quiet" if quiet else "done", reply.text)
        log.write("job", id=job.id, event="quiet" if quiet else "done", minutes=job.minutes(),
                  cost=round(reply.cost, 5))
        if not quiet:  # a quiet routine with nothing to report: only the chat shows it ran
            self.delivery.job_done(job, reply.text, ok=True)

    def _routine_ran(self, job, state, result=""):
        if job.routine and self.routines:
            self.routines.ran(job.routine, state, result)

    def routine(self, routine, asked=False):
        """A routine is due (or he asked to run it now): it runs as a background job titled with its name.
        Returns why it didn't start, or ""."""
        if any(job.routine == routine["id"] for job in self.jobs.running()):
            return "its last run is still going"
        if not asked and self.agent.ledger.over_budget:
            # Claude would ask first, and he isn't there to answer: skipped, and he's told.
            self.delivery.tell(f"I skipped your routine \"{routine['name']}\": this month's {self.agent.ledger.budget:g} "
                               "dollar budget is used up.")
            return "this month's budget was used up"
        self.jobs.add(routine["task"], title=routine["name"], routine=routine["id"], quiet=bool(routine.get("quiet")))
        return ""
