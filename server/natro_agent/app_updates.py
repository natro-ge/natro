"""The phone's app, updated over the air: scripts/publish-phone.py puts a new build in app/ on the VPS (natro.apk and
app.json, saying its version, size and hash), and a phone on an older version is told (app_update), when it connects and
as soon as there's a new one. It fetches GET /app/natro.apk with the device token, piece by piece (Range), and installs it
itself (AppUpdates.kt). He can ask too, in the phone's Settings (app_update_check): it's told as on connecting, or
that it's the newest (app_current).

The tailnet and the token keep the file to his devices, and Android keeps the rest safe: it takes an update only if it's
signed with the same key as the app it replaces, and that key isn't here.
"""
import asyncio
import hmac
import json
import os
import re

from natro_agent import log
from natro_agent.dashboard import set_headers

CHECK_SECONDS = 60
PATH = "/app/natro.apk"
TYPE = "application/vnd.android.package-archive"
RANGE = re.compile(r"bytes=(\d+)-(\d*)")
# The most a request gets. This server drops a response that takes more than about 10 seconds to go out (websockets'
# close_timeout), which a whole build does on mobile data: it comes piece by piece, each its own request.
MOST_PIECE_BYTES = 2**20


class AppUpdates:
    """folder: where a build is published (app/). devices: name -> device, while connected (the agent's). token: the
    device token (None: anyone who can reach this address may fetch it)."""

    def __init__(self, folder, devices, token=None):
        self.folder = folder
        self.devices = devices
        self.token = token
        self._told = None

    def latest(self):
        """What's published: {"version", "size", "sha256"}, or None."""
        try:
            data = json.loads((self.folder / "app.json").read_text(encoding="utf-8"))
            return {key: data[key] for key in ("version", "size", "sha256")}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def connected(self, device):
        """A device connected: if it's a phone on an older version, it's told of the new one. True if it was."""
        latest = self.latest()
        version = getattr(device, "version", None)
        if not (latest and isinstance(version, int) and not isinstance(version, bool) and version < latest["version"]):
            return False
        device.post(type="app_update", path=PATH, **latest)
        log.write("app_update", event="told", device=device.name, version=latest["version"], had=version)
        return True

    def messages(self):
        return {"app_update_check": self.check}

    def check(self, device, data):
        """He asked, in the phone's Settings, whether there's a newer build."""
        if not self.connected(device):
            device.post(type="app_current", version=getattr(device, "version", None))

    async def run(self):
        """Tell the phones as soon as a new build is published, forever (the server runs it as a task)."""
        while True:
            try:
                latest = self.latest()
                if latest != self._told:
                    self._told = latest
                    for device in list(self.devices.values()):
                        self.connected(device)
            except Exception as e:  # one bad look must not stop the next
                log.write("error", step="app_updates", error=repr(e))
            await asyncio.sleep(CHECK_SECONDS)

    def http(self, connection, request):
        """serve()'s process_request for the build (None for any other address)."""
        if request.path.partition("?")[0] != PATH:
            return None
        given = str(request.headers.get("X-Natro-Token", ""))
        if self.token and not hmac.compare_digest(given.encode(), self.token.encode()):
            return connection.respond(403, "Not without the device token.\n")
        try:
            with (self.folder / "natro.apk").open("rb") as build:
                size = build.seek(0, os.SEEK_END)
                asked = RANGE.fullmatch(str(request.headers.get("Range", "")))
                if not asked:
                    build.seek(0)
                    return self._whole(connection, build.read())
                start = int(asked.group(1))
                if start >= size:
                    return connection.respond(416, "That's past its end.\n")
                end = min(int(asked.group(2) or size - 1), start + MOST_PIECE_BYTES - 1, size - 1)
                build.seek(start)
                data = build.read(end - start + 1)
        except OSError:
            return connection.respond(404, "There's no build here.\n")
        response = connection.respond(206, "")
        response.body = data
        set_headers(response, TYPE, {"Content-Range": f"bytes {start}-{end}/{size}"})
        if start == 0:
            log.write("app_update", event="fetching", bytes=size)
        return response

    @staticmethod
    def _whole(connection, data):
        response = connection.respond(200, "")
        response.body = data
        set_headers(response, TYPE)
        log.write("app_update", event="fetched", bytes=len(data))
        return response
