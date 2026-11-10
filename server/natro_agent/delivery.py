"""Who sees and who hears what Natro does on her own: a reminder that's due, a watcher's alert, what a background job
found, and what she tells him later (a conversion that failed after she answered). Each is shown on every device that
shows that kind (its hello says so) and in the chat, and said out loud on one: the device it's for, else the one he
used last if that was lately, else the phone. The messages are in shared/PROTOCOL.md (reminder, reminder_snoozed,
alert, job_done)."""
import time
from datetime import datetime

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.jobs import lead, short

# A reminder is spoken on the device he used last if that was within this time; else on the phone.
RECENT_MINUTES = 30
# A reminder delivered later than this says when it was for.
LATE_MINUTES = 2
# What a finished background job found, as the next request Claude answers hears it.
JOB_NOTE_CHARS = 1500
# A finished job no device could show waits for one this long.
JOB_WAIT_HOURS = 12


class Delivery:
    """agent: the Agent, whose devices show them and whose next request hears of them. chat: the chats.SharedChat
    they go into. reminders: the reminders.Reminders whose reminders it shows and a device snoozes (None: none).
    watchers: the watchers.Watchers whose alerts it shows (None: none)."""

    def __init__(self, agent, chat, reminders=None, watchers=None):
        self.agent = agent
        self.chat = chat
        self.reminders = reminders
        if reminders:
            reminders.deliver = self.remind
        if watchers:
            watchers.deliver = self.alert
        # Device name -> when (monotonic) he last made a request on it; the server and PhoneMessages (his answers)
        # write it.
        self.used = {}
        self._unseen_jobs = []  # (when, job_done's fields) for a device that shows jobs, none being online

    def messages(self):
        return {"reminder_snooze": self.snooze} if self.reminders else {}

    def connected(self, device):
        """A device that shows jobs connected: the jobs that finished while none was online, if not too old."""
        if not device.jobs:
            return
        oldest = time.time() - JOB_WAIT_HOURS * 3600
        unseen, self._unseen_jobs = self._unseen_jobs, []
        for when, done in unseen:
            if when < oldest:
                log.write("job", id=done["id"], event="dropped", late_hours=JOB_WAIT_HOURS)
            elif not self._show_job(done):
                self._unseen_jobs.append((when, done))

    def remind(self, reminder):
        """A reminder is due: shown on every device that shows them and in the chat, spoken on one. Returns
        whether any device showed it."""
        viewers = [device for device in list(self.agent.devices.values()) if getattr(device, "reminders", False)]
        if not viewers:
            return False
        speaker = self.speaker(viewers, reminder.get("on"))
        now = datetime.now(timezone())
        due = datetime.fromtimestamp(reminder["due"] / 1000, timezone())
        late = (now - due).total_seconds() > LATE_MINUTES * 60
        text = reminder["text"]
        if late:
            days = (now.date() - due.date()).days
            day = "" if days == 0 else "yesterday at " if days == 1 else f"{due:%A} at "
            text = f"A reminder from {day}{due:%H:%M}: {text}"
        for device in viewers:
            device.post(type="reminder", id=reminder["id"], text=text, due=reminder["due"], late=late,
                        speak=device is speaker)
        self.chat.reminder(reminder["id"], text)
        # So "remind me again in an hour" knows what. Personal: only Claude hears of it.
        self.agent.private_notes.append(f"you reminded him at {now:%H:%M}: \"{reminder['text']}\"")
        log.write("reminder", event="shown", id=reminder["id"], devices=[device.name for device in viewers],
                  spoken_on=speaker.name)
        return True

    def snooze(self, device, data):
        id, minutes = str(data.get("id", "")), data.get("minutes")
        at = self.reminders.snooze(id, minutes if minutes is not None else 10)
        if at is None:
            device.post(type="reminder_snoozed", id=id, ok=False, text="That reminder is gone.")
        else:
            device.post(type="reminder_snoozed", id=id, ok=True, text=f"Snoozed until {at:%H:%M}.")

    def job_done(self, job, text, ok):
        """A job finished: shown on every device that shows jobs (or the next one that connects), spoken on one;
        Claude hears it next."""
        done = {"id": job.id, "task": job.task, "text": text, "say": lead(text), "ok": ok, "title": job.title}
        if not self._show_job(done):
            self._unseen_jobs.append((time.time(), done))
        # So "what did you find?" is answered by Sonnet, who is told (the next request counts as personal).
        what = f"your routine \"{job.title}\" ({job.id})" if job.routine else f"your background job {job.id}"
        self.agent.private_notes.append(f"{what} (\"{short(job.task, 200)}\") {'finished' if ok else 'failed'}; "
                                        f"you told him: \"{short(text, JOB_NOTE_CHARS)}\"")
        self.agent.personal_next = True

    def _show_job(self, done):
        """job_done to every device that shows jobs, spoken on one. Returns whether any device showed it."""
        viewers = [device for device in list(self.agent.devices.values()) if getattr(device, "jobs", False)]
        if not viewers:
            log.write("job", id=done["id"], event="waiting")
            return False
        speaker = self.speaker(viewers)
        for device in viewers:
            device.post(type="job_done", **done, speak=device is speaker)
        log.write("job", id=done["id"], event="shown", devices=[device.name for device in viewers],
                  spoken_on=speaker.name)
        return True

    def alert(self, alert):
        """Something a watcher saw: shown on every device that shows alerts and in the chat, spoken on one if
        it's urgent. Returns whether any device showed it."""
        viewers = [device for device in list(self.agent.devices.values()) if getattr(device, "alerts", False)]
        if not viewers:
            return False
        speaker = self.speaker(viewers) if alert["urgent"] else None
        for device in viewers:
            device.post(type="alert", id=alert["id"], text=alert["text"], kind=alert["kind"], urgent=alert["urgent"],
                        speak=device is speaker, **({"answers": alert["answers"]} if alert.get("answers") else {}))
        self.chat.reminder(alert["id"], alert["text"], alert=True)
        if alert["kind"] != "meetings":
            # So "read it" or "what did it say?" is answered by Sonnet, who is told (the next request counts as
            # personal).
            self.agent.private_notes.append(f"you told him: \"{alert['text']}\"")
            self.agent.personal_next = True
        log.write("watcher", event="shown", id=alert["id"], devices=[device.name for device in viewers],
                  spoken_on=speaker.name if speaker else None)
        return True

    def tell(self, text):
        """Tell him something that happened later (a conversion that failed after Natro answered), as an alert."""
        now = int(time.time() * 1000)
        self.alert({"id": f"tl{now:x}", "kind": "natro", "text": text, "urgent": False, "due": now,
                    "until": now + 12 * 3600 * 1000})

    def speaker(self, viewers, on=None):
        """Which device says it out loud: the one it's for (back online), else the one he used last if that was
        lately, else the phone."""
        by_name = {device.name: device for device in viewers}
        if on in by_name:
            return by_name[on]
        recent = [(when, name) for name, when in self.used.items()
                  if name in by_name and time.monotonic() - when < RECENT_MINUTES * 60]
        if recent:
            return by_name[max(recent)[1]]
        return by_name.get("phone") or viewers[0]
