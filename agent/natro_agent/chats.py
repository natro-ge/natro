"""The one chat the PC and the phone both show: every request, from either, with Natro's reply and pictures.

The server keeps it in memory only, never on disk. New chat on either device clears it on both (and Natro
starts a new conversation); a restart of the server forgets it. A device that connects is sent the chat so
far: its pictures first, then every entry (chat_all). After that, each change to an entry goes to every
device as the whole entry (chat_entry), a reply being written at most every PIECE_SECONDS.

An entry is one request and Natro's answer to it, a picture he sent, a reminder that went off, a
background job, or an alert:
  {"id": "phone:r1", "device": "phone", "label": "phone", "request": "r1", "time": 1760000000000,
   "asked": "...", "heard": "...", "spoken": false, "reply": "...", "step": "...", "done": false,
   "problem": "", "pictures": ["pic3"],
   "question": {"id": "c4", "text": "...", "answer": "", "device": "phone"} or null,
   "queued": "PC" or null, "sent": false, "reminder": false, "job": "", "alert": false}
request is the id the device gave it, so the device can tell its own requests (it shows those as they
happen) from the other's. queued: the device it was asked from while this one was offline (pending.py).
sent: a picture he sent, not a request. reminder: a reminder (reminders.py), as Natro's words alone, from
the device "natro" (reply is what she said; asked is empty). job: a background job's task (jobs.py), from
the device "natro": its steps, question and reply as a request's, with asked empty. A question's device is
the one it was asked on: for a job's, a device answering it elsewhere names "natro" and the server finds it.
alert: something a watcher saw (watchers.py), as Natro's words alone, from the device "natro", like a reminder.

Pictures (screenshots, or ones he sends) go to devices as chat_picture with their bytes after it; entries
name them. Natro never sees them: they are for him.
"""
import asyncio
import itertools
import time
from collections import OrderedDict
from dataclasses import dataclass

MAX_ENTRIES = 200
MAX_PICTURES = 24
MAX_PICTURE_BYTES = 12 << 20
PICTURE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
PIECE_SECONDS = 0.25


@dataclass
class Picture:
    id: str
    mime: str
    data: bytes
    name: str


class SharedChat:
    """devices: name -> device, while connected (the agent's). Devices that said "chat" in their hello see it."""

    def __init__(self, devices):
        self.devices = devices
        self.entries = OrderedDict()  # entry id -> entry
        self.pictures = OrderedDict()  # picture id -> Picture
        self._ids = itertools.count(1)
        self._late = {}  # entry id -> the timer that sends it, while pieces of a reply come in

    # What devices see

    def _viewers(self):
        return [device for device in list(self.devices.values()) if getattr(device, "chat", False)]

    def _send(self, entry_id):
        self._late.pop(entry_id, None)
        entry = self.entries.get(entry_id)
        if entry is not None:
            for device in self._viewers():
                device.post(type="chat_entry", entry=entry)

    def _changed(self, entry_id, soon=False):
        """Send the entry to every device: now, or (soon) within PIECE_SECONDS, once for many changes."""
        if not soon:
            if timer := self._late.pop(entry_id, None):
                timer.cancel()
            self._send(entry_id)
        elif entry_id not in self._late:
            self._late[entry_id] = asyncio.get_running_loop().call_later(PIECE_SECONDS, self._send, entry_id)

    def catch_up(self, device):
        """The chat so far, for a device that just connected."""
        if not getattr(device, "chat", False):
            return
        for picture in self.pictures.values():
            self._post_picture(device, picture)
        device.post(type="chat_all", entries=list(self.entries.values()))

    @staticmethod
    def _post_picture(device, picture):
        device.post(type="chat_picture", id=picture.id, mime=picture.mime, name=picture.name)
        device.post_bytes(picture.data)

    # Requests

    def start(self, device, request, asked="", heard="", spoken=False, queued=None, sent=False):
        """A request from a device; returns its entry's id."""
        entry_id = f"{device.name}:{request}"
        self.entries.pop(entry_id, None)  # an id used again (an old app's): the new one goes at the end
        self.entries[entry_id] = {
            "id": entry_id, "device": device.name, "label": device.label, "request": request,
            "time": int(time.time() * 1000), "asked": asked, "heard": heard, "spoken": spoken, "reply": "",
            "step": "", "done": False, "problem": "", "pictures": [], "question": None, "queued": queued,
            "sent": sent, "reminder": False, "job": "", "alert": False}
        self._trim()
        self._changed(entry_id)
        return entry_id

    def piece(self, entry_id, text):
        if entry := self.entries.get(entry_id):
            entry["reply"] += text
            self._changed(entry_id, soon=True)

    def step(self, entry_id, text):
        if (entry := self.entries.get(entry_id)) and not entry["done"]:
            entry["step"] = text
            self._changed(entry_id, soon=True)

    def replied(self, entry_id, text, problem=""):
        if entry := self.entries.get(entry_id):
            entry.update(reply=text or entry["reply"], done=True, step="", problem=problem)
            self._changed(entry_id)

    def reminder(self, reminder_id, text, alert=False):
        """A reminder that went off (or an alert: what a watcher saw): Natro's words, with no request before them."""
        entry_id = f"natro:{reminder_id}"
        self.entries.pop(entry_id, None)
        self.entries[entry_id] = {
            "id": entry_id, "device": "natro", "label": "Natro", "request": reminder_id,
            "time": int(time.time() * 1000), "asked": "", "heard": "", "spoken": False, "reply": text, "step": "",
            "done": True, "problem": "", "pictures": [], "question": None, "queued": None, "sent": False,
            "reminder": not alert, "job": "", "alert": alert}
        self._trim()
        self._changed(entry_id)
        return entry_id

    def job(self, job_id, task):
        """A background job starting: Natro's work on task, as it goes, then what she found."""
        entry_id = f"natro:{job_id}"
        self.entries.pop(entry_id, None)
        self.entries[entry_id] = {
            "id": entry_id, "device": "natro", "label": "Natro", "request": job_id,
            "time": int(time.time() * 1000), "asked": "", "heard": "", "spoken": False, "reply": "", "step": "",
            "done": False, "problem": "", "pictures": [], "question": None, "queued": None, "sent": False,
            "reminder": False, "job": task, "alert": False}
        self._trim()
        self._changed(entry_id)
        return entry_id

    # Natro's questions

    def asked(self, entry_id, question_id, text, device_name=""):
        """Natro asks, on device_name (the request's own, or for a job the one he used last)."""
        if entry := self.entries.get(entry_id):
            entry["question"] = {"id": question_id, "text": text, "answer": "",
                                 "device": device_name or entry["device"]}
            self._changed(entry_id)

    def answered(self, device_name, question_id, answer):
        """The answer to a question asked on that device, however it came: shown on both devices."""
        for entry in reversed(self.entries.values()):
            question = entry["question"]
            if question and question["id"] == question_id and question.get("device", entry["device"]) == device_name:
                if not question["answer"]:
                    question["answer"] = answer
                    self._changed(entry["id"])
                return

    def asker(self, question_id):
        """The device a background job's question was asked on, or None."""
        for entry in reversed(self.entries.values()):
            question = entry["question"]
            if entry["job"] and question and question["id"] == question_id and not question["answer"]:
                return question.get("device")
        return None

    # Pictures

    def picture(self, device, mime, data, name="picture", entry_id=None):
        """A picture: one Natro got for a request (entry_id: a screenshot), or one he sent (a new entry).
        Returns the entry's id, or None if it can't be shown."""
        if mime not in PICTURE_TYPES or not data or len(data) > MAX_PICTURE_BYTES:
            return None
        picture = Picture(f"pic{next(self._ids)}", mime, bytes(data), name or "picture")
        if entry_id is None or entry_id not in self.entries:
            entry_id = self.start(device, picture.id, sent=True)
            self.entries[entry_id]["done"] = True
        self.pictures[picture.id] = picture
        while len(self.pictures) > MAX_PICTURES:
            self.pictures.popitem(last=False)  # the oldest: its entry says it's gone
        for viewer in self._viewers():
            self._post_picture(viewer, picture)
        self.entries[entry_id]["pictures"].append(picture.id)
        self._changed(entry_id)
        return entry_id

    # New chat

    def clear(self):
        for timer in self._late.values():
            timer.cancel()
        self._late.clear()
        self.entries.clear()
        self.pictures.clear()
        for device in self._viewers():
            device.post(type="chat_cleared")

    def _trim(self):
        while len(self.entries) > MAX_ENTRIES:
            _, entry = self.entries.popitem(last=False)
            for picture in entry["pictures"]:
                self.pictures.pop(picture, None)
