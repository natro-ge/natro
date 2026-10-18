"""Files sent between the owner's devices (PC and phone), passed along by the server.

A device sends a file up in parts (file_offer, then file_part + its bytes again and again, then
file_end; see server.py). The server writes it to transfers/ and sends it down to the other device
the same way as soon as that device is connected, and deletes it once the device says it saved it.
A device that's offline gets it when it next connects; a file still undelivered after KEEP_DAYS is
deleted. An upload that stops partway (the sender disconnected or gave up) is deleted at once.
Nothing is kept once it's delivered.

Each side sends on only while less than WINDOW_BYTES of a file is unanswered (file_got), so a big
file never fills memory, nor holds a voice request up for long on the same connection.

Asking Natro works too ("send the invoice to my phone"): copy_to_device asks the device that has
the file to send it (file_request).

The server itself can be one end, for a file it works on (convert.py): fetch asks a device for a file
with to "Natro" (SERVER), and keeps it in transfers/fetched/ for the caller, never passing it on; send
puts a file made here in line for a device, from "Natro". The apps show that name as is ("Sending
report.pdf to your Natro", "report.docx, from your Natro").
"""
import asyncio
import itertools
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from natro_agent import log
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

MAX_BYTES = 1 << 30  # 1 GB
PART_BYTES = 512 << 10
WINDOW_BYTES = 2 << 20
KEEP_DAYS = 7
# Room the server's disk keeps free besides a new file.
SPARE_BYTES = 1 << 30
# A transfer that doesn't move on for this long has stalled.
STALL_SECONDS = 60
# How long a device has to start sending a file Natro asked it for.
REQUEST_SECONDS = 30
# The server itself, as a file's to or from (the apps show it as is).
SERVER = "Natro"

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
UNSAFE_IN_NAME = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
LONGEST_NAME = 150


def safe_name(name):
    """A file name that's safe to save on Windows and Android: no folders, no reserved names."""
    name = re.split(r"[/\\]", str(name or ""))[-1]
    name = UNSAFE_IN_NAME.sub("_", name).strip(" .")
    if name.split(".")[0].lower() in RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > LONGEST_NAME:
        stem, dot, extension = name.rpartition(".")
        if dot and len(extension) <= 10:
            name = f"{stem[:LONGEST_NAME - len(extension) - 1]}.{extension}"
        else:
            name = name[:LONGEST_NAME]
    return name or "file"


def human_size(size):
    for unit in ("bytes", "KB", "MB"):
        if size < 1024:
            return f"{size} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


@dataclass
class Upload:
    """A file on its way up from a device."""
    id: str
    device: object
    to: str
    name: str
    size: int
    file: object
    received: int = 0


@dataclass
class Delivery:
    """A file on its way down to a device: how much it has, and its answer at the end."""
    id: str
    device: object
    acked: int = 0
    moved: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class Transfers:
    """folder: where files wait (made if missing). devices: name -> connected Device, the agent's own dict."""

    def __init__(self, folder, devices):
        self.folder = Path(folder)
        self.devices = devices
        self._uploads = {}  # id -> Upload
        self._deliveries = {}  # id -> Delivery
        self._delivering = {}  # device name -> (device, the task sending it its files)
        self._requests = {}  # id -> (device, future for its file_offer), for copy_to_device and fetch
        self._fetches = {}  # id -> (device, future for (path, name) once all of it is here), for fetch
        self._ids = itertools.count(1)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.fetched = self.folder / "fetched"
        self.fetched.mkdir(exist_ok=True)
        for part in [*self.folder.glob("*.part"), *self.fetched.iterdir()]:
            part.unlink(missing_ok=True)  # an upload cut off by a restart, a file a restart left behind
        self.expire()

    def tools(self):
        devices = list(KNOWN_DEVICES)
        device = {"type": "string", "enum": devices}
        return [Tool(
            "copy_to_device",
            "Send a copy of a file from one of his devices to the other (PC to phone, or phone to PC). It lands in "
            "the other device's Downloads folder. Find the file first (pc_search_files, phone_search_files) and give "
            f"its full path. A device that's offline gets it when it's back (within {KEEP_DAYS} days). At most 1 GB.",
            {"type": "object", "properties": {
                "from_device": device,
                "path": {"type": "string", "description": "The file's full path on that device."},
                "to_device": device,
            }, "required": ["from_device", "path", "to_device"]},
            self.copy, source="natro", personal=True)]

    # Natro, asked to send a file

    async def copy(self, args):
        source, target = args.get("from_device"), args.get("to_device")
        if source not in KNOWN_DEVICES or target not in KNOWN_DEVICES or source == target:
            raise ToolError(f"from_device and to_device must be two different devices: {', '.join(KNOWN_DEVICES)}.")
        device = self.devices.get(source)
        if device is None:
            raise ToolError(f"Your owner's {KNOWN_DEVICES[source]} is offline right now.")
        if not getattr(device, "files", False):
            raise ToolError(f"The Natro app on his {KNOWN_DEVICES[source]} is too old to send files.")
        receiver = self.devices.get(target)
        if receiver is not None and not getattr(receiver, "files", False):
            raise ToolError(f"The Natro app on his {KNOWN_DEVICES[target]} is too old to receive files.")
        id = f"natro-{time.time_ns() // 1_000_000:x}-{next(self._ids)}"
        future = asyncio.get_running_loop().create_future()
        self._requests[id] = (device, future)
        try:
            device.post(type="file_request", id=id, path=str(args.get("path") or ""), to=target)
            upload = await asyncio.wait_for(future, REQUEST_SECONDS)
        except asyncio.TimeoutError:
            raise ToolError(f"The {KNOWN_DEVICES[source]} didn't start sending it.")
        finally:
            self._requests.pop(id, None)
        there = KNOWN_DEVICES[target]
        later = "" if self._can_receive(target) else f" His {there} is offline, so it will arrive when it's back."
        return f"Sending {upload.name} ({human_size(upload.size)}) to the {there}'s Downloads folder.{later}"

    # The server's own files (convert.py)

    async def fetch(self, source, path, most=MAX_BYTES):
        """A file from one of his devices, here: (where it is, its name). The caller deletes it when done."""
        device = self.devices.get(source)
        if device is None:
            raise ToolError(f"Your owner's {KNOWN_DEVICES.get(source, source)} is offline right now.")
        if not getattr(device, "files", False):
            raise ToolError(f"The Natro app on his {device.label} is too old to send files.")
        id = f"natro-{time.time_ns() // 1_000_000:x}-{next(self._ids)}"
        loop = asyncio.get_running_loop()
        offered, arrived = loop.create_future(), loop.create_future()
        self._requests[id] = (device, offered)
        self._fetches[id] = (device, arrived)
        try:
            device.post(type="file_request", id=id, path=str(path or ""), to=SERVER)
            try:
                upload = await asyncio.wait_for(offered, REQUEST_SECONDS)
            except asyncio.TimeoutError:
                raise ToolError(f"The {device.label} didn't start sending it.")
            if upload.size > most:
                self._abort(upload, f"{upload.name} is {human_size(upload.size)}: at most {human_size(most)} here.")
                raise ToolError(f"{upload.name} is {human_size(upload.size)}; Natro takes at most {human_size(most)}.")
            received = -1
            while not arrived.done():  # wait while it keeps coming
                if upload.received == received:
                    self._abort(upload, "It stopped coming.")
                    raise ToolError(f"{upload.name} stopped coming from the {device.label}.")
                received = upload.received
                await asyncio.wait([arrived], timeout=STALL_SECONDS)
            return arrived.result()
        finally:
            self._requests.pop(id, None)
            self._fetches.pop(id, None)
            if arrived.done() and not arrived.cancelled():
                arrived.exception()  # seen, though this raised its own (refused, stalled)

    def send(self, path, name, to):
        """A file made here (moved from path) goes to one of his devices, from "Natro", as soon as it's there."""
        id = f"natro-{time.time_ns() // 1_000_000:x}-{next(self._ids)}"
        shutil.move(str(path), self._data(id))
        meta = {"id": id, "from": SERVER, "to": to, "name": safe_name(name), "size": self._data(id).stat().st_size,
                "at": time.time()}
        written = self._meta(id).with_suffix(".tmp")
        written.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        written.replace(self._meta(id))
        log.write("file", event="made", **self._fields(meta))
        self._deliver_to(to)
        return meta

    # Devices coming and going

    def connected(self, device):
        """A device connected: files waiting for it start coming down."""
        self.expire()
        self._deliver_to(device.name)

    def disconnected(self, device):
        for upload in [upload for upload in self._uploads.values() if upload.device is device]:
            self._abort(upload, "The sender disconnected.", tell=False)
        for id, (asked, future) in [*self._requests.items(), *self._fetches.items()]:
            if asked is device and not future.done():
                future.set_exception(ToolError(f"The {device.label} disconnected."))
        running = self._delivering.get(device.name)
        if running and running[0] is device:
            running[1].cancel()  # what it didn't get yet stays, for next time
            del self._delivering[device.name]

    def expire(self):
        """Deletes files no one picked up within KEEP_DAYS, and anything half there."""
        cutoff = time.time() - KEEP_DAYS * 86400
        for meta in self._waiting():
            if meta.get("at", 0) < cutoff and meta["id"] not in self._deliveries:
                self._remove(meta["id"])
                log.write("file", event="expired", **self._fields(meta))
        for data in self.folder.glob("*.file"):
            if not data.with_suffix(".json").exists():
                data.unlink(missing_ok=True)

    # Messages from devices

    def received(self, device, data):
        """A file_* message other than file_part (see server.py)."""
        kind, id = data.get("type"), str(data.get("id", ""))
        if kind == "file_offer":
            self._offer(device, id, data)
        elif kind == "file_end":
            self._end(device, id)
        elif kind == "file_got":
            self._got(device, id, data.get("bytes"))
        elif kind == "file_saved":
            delivery = self._deliveries.get(id)
            if delivery and delivery.device is device and not delivery.done.done():
                delivery.done.set_result(safe_name(data.get("name")))
        elif kind == "file_failed":
            self._failed(device, id, str(data.get("message") or "It didn't work."))

    def part(self, device, id, chunk):
        """The next piece of a file the device is sending."""
        upload = self._uploads.get(id)
        if upload is None or upload.device is not device:
            return  # refused, or given up
        if not isinstance(chunk, bytes) or upload.received + len(chunk) > upload.size:
            return self._abort(upload, "The file was bigger than it said.")
        try:
            upload.file.write(chunk)
        except OSError as e:
            return self._abort(upload, f"The server couldn't keep it: {e.strerror or e}")
        upload.received += len(chunk)
        device.post(type="file_got", id=id, bytes=upload.received)

    def _offer(self, device, id, data):
        asked = self._requests.get(id)

        def refuse(message):
            device.post(type="file_failed", id=id, message=message)
            if asked and not asked[1].done():
                asked[1].set_exception(ToolError(message))
            log.write("file", event="refused", id=id, error=message, **{"from": device.name})

        if not SAFE_ID.fullmatch(id):
            return refuse("A file needs an id of letters, digits, - and _.")
        if self._meta(id).exists():  # sent before, and the answer got lost: it's here already
            meta = self._read_meta(self._meta(id)) or {}
            device.post(type="file_sent", id=id, waiting=not self._can_receive(meta.get("to")))
            return
        if id in self._uploads:
            return refuse("That file is already on its way.")
        to, size, name = data.get("to"), data.get("size"), safe_name(data.get("name"))
        if to == SERVER and id in self._fetches:
            pass  # a file Natro asked for, to work on here
        elif to not in KNOWN_DEVICES or to == device.name:
            others = [label for key, label in KNOWN_DEVICES.items() if key != device.name]
            return refuse(f"Files can go to the {' or the '.join(others)}.")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return refuse("The file's size is missing.")
        if size > MAX_BYTES:
            return refuse(f"{name} is {human_size(size)}; files can be at most 1 GB.")
        if shutil.disk_usage(self.folder).free < size + SPARE_BYTES:
            return refuse("The server doesn't have room for it right now.")
        upload = Upload(id, device, to, name, size, open(self._part(id), "wb"))
        self._uploads[id] = upload
        log.write("file", event="offered", **self._fields(upload.__dict__ | {"from": device.name}))
        if asked and not asked[1].done():
            asked[1].set_result(upload)

    def _end(self, device, id):
        upload = self._uploads.get(id)
        if upload is None or upload.device is not device:
            return
        if upload.received != upload.size:
            return self._abort(upload, f"Only {human_size(upload.received)} of {human_size(upload.size)} arrived.")
        del self._uploads[id]
        upload.file.close()
        if upload.to == SERVER:  # for Natro herself: kept for whoever fetched it, never passed on
            kept = self.fetched / id
            self._part(id).replace(kept)
            device.post(type="file_sent", id=id, waiting=False)
            log.write("file", event="fetched", **self._fields(upload.__dict__ | {"from": device.name}))
            fetching = self._fetches.get(id)
            if fetching and not fetching[1].done():
                fetching[1].set_result((kept, upload.name))
            else:
                kept.unlink(missing_ok=True)
            return
        self._part(id).replace(self._data(id))
        meta = {"id": id, "from": device.name, "to": upload.to, "name": upload.name, "size": upload.size,
                "at": time.time()}
        written = self._meta(id).with_suffix(".tmp")
        written.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        written.replace(self._meta(id))
        device.post(type="file_sent", id=id, waiting=not self._can_receive(upload.to))
        log.write("file", event="uploaded", **self._fields(meta))
        self._deliver_to(upload.to)

    def _got(self, device, id, count):
        delivery = self._deliveries.get(id)
        if delivery and delivery.device is device and isinstance(count, int):
            delivery.acked = max(delivery.acked, count)
            delivery.moved.set()

    def _failed(self, device, id, message):
        """The device gave up on a file: one it was sending, was asked for, or was getting."""
        upload = self._uploads.get(id)
        if upload and upload.device is device:
            self._abort(upload, message, tell=False)
        for asked in (self._requests.get(id), self._fetches.get(id)):
            if asked and asked[0] is device and not asked[1].done():
                asked[1].set_exception(ToolError(message))
        delivery = self._deliveries.get(id)
        if delivery and delivery.device is device and not delivery.done.done():
            delivery.done.set_exception(ToolError(message))
            delivery.moved.set()

    def _abort(self, upload, message, tell=True):
        self._uploads.pop(upload.id, None)
        upload.file.close()
        self._part(upload.id).unlink(missing_ok=True)
        fetching = self._fetches.get(upload.id)
        if fetching and not fetching[1].done():
            fetching[1].set_exception(ToolError(message))
        if tell:
            upload.device.post(type="file_failed", id=upload.id, message=message)
        log.write("file", event="upload_failed", id=upload.id, error=message)

    # Sending files down

    def _can_receive(self, name):
        device = self.devices.get(name)
        return device is not None and getattr(device, "files", False)

    def _deliver_to(self, name):
        """Start sending a device the files waiting for it, unless that's already going on."""
        if not self._can_receive(name):
            return
        device = self.devices[name]
        running = self._delivering.get(name)
        if running and running[0] is device and not running[1].done():
            return  # it looks for more files when it's done with one
        if running:
            running[1].cancel()  # an older connection's
        self._delivering[name] = (device, asyncio.create_task(self._deliver_all(device)))

    async def _deliver_all(self, device):
        tried = set()
        while True:
            waiting = [meta for meta in self._waiting(device.name)
                       if meta["id"] not in tried and self._data(meta["id"]).exists()]
            if not waiting:
                return
            tried.add(waiting[0]["id"])
            if not await self._deliver(device, waiting[0]):
                return  # stalled: the connection is likely gone; the rest wait for the next one

    async def _deliver(self, device, meta):
        """Send one file down. False if it stalled (it stays, for next time)."""
        id = meta["id"]
        delivery = Delivery(id, device)
        self._deliveries[id] = delivery
        try:
            device.post(type="file_incoming", id=id, name=meta["name"], size=meta["size"], **{"from": meta["from"]})
            sent = 0
            with open(self._data(id), "rb") as file:
                while not delivery.done.done() and (piece := file.read(PART_BYTES)):
                    while sent - delivery.acked >= WINDOW_BYTES and not delivery.done.done():
                        delivery.moved.clear()
                        await asyncio.wait_for(delivery.moved.wait(), STALL_SECONDS)
                    if delivery.done.done():
                        break
                    device.post(type="file_part", id=id)
                    device.post_bytes(piece)
                    sent += len(piece)
            if not delivery.done.done():
                device.post(type="file_end", id=id)
            saved = await asyncio.wait_for(delivery.done, STALL_SECONDS)
        except asyncio.TimeoutError:
            log.write("file", event="stalled", **self._fields(meta))
            return False
        except (ToolError, OSError) as e:  # the device couldn't save it, or the server couldn't read it
            self._remove(id)
            self._tell_sender(meta, type="file_failed", id=id,
                              message=f"The {device.label} didn't get {meta['name']}: {e}")
            log.write("file", event="delivery_failed", error=str(e), **self._fields(meta))
            return True
        finally:
            self._deliveries.pop(id, None)
            if delivery.done.done() and not delivery.done.cancelled():
                delivery.done.exception()  # seen, even if this was cancelled before looking
        self._remove(id)
        self._tell_sender(meta, type="file_delivered", id=id, to=device.name, name=saved)
        log.write("file", event="delivered", saved_as=saved, **self._fields(meta))
        return True

    def _tell_sender(self, meta, **message):
        if self._can_receive(meta["from"]):
            self.devices[meta["from"]].post(**message)

    # The folder

    def _part(self, id):
        return self.folder / f"{id}.part"

    def _data(self, id):
        return self.folder / f"{id}.file"

    def _meta(self, id):
        return self.folder / f"{id}.json"

    def _remove(self, id):
        self._data(id).unlink(missing_ok=True)
        self._meta(id).unlink(missing_ok=True)

    @staticmethod
    def _read_meta(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _waiting(self, to=None):
        """Descriptions of the files here, oldest first (only those for `to`, if given)."""
        found = [meta for path in self.folder.glob("*.json") if (meta := self._read_meta(path))]
        return sorted((meta for meta in found if to is None or meta.get("to") == to), key=lambda m: m.get("at", 0))

    @staticmethod
    def _fields(meta):
        return {key: meta.get(key) for key in ("id", "from", "to", "name", "size")}
