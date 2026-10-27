"""The keys in the PC's Settings window (desktop/src-tauri/src/keys.rs): Natro's API keys and a few settings in
this server's .env, changed behind a PIN.

What can be changed is a fixed list (FIELDS): the paid keys and plain settings, never what the devices connect
with (NATRO_LISTEN, NATRO_DEVICE_TOKEN), so the app can't cut itself off. A key is never sent back whole, only
its last four characters. A new key is tried first with a free call to its provider (listing models, Tavily's
usage, a short walk for the Maps key); one the provider refuses isn't saved, and neither is anything else asked
for with it. Saving
keeps the old file as .env.bak, changes only those lines (comments and order stay) and restarts Natro: she
exits with RESTART_CODE, and systemd starts her again (Restart=on-failure) with the new .env.

The PIN: 4 to 12 digits, chosen by the first unlock (a device that has the device token), kept only as a salted
scrypt hash in tokens/settings-pin.json. After FREE_TRIES wrong tries each try has to wait, twice as long each
time. Unlocking gives a session for that device, which ends SESSION_SECONDS after it was last used, or when the
device locks it (the window closed). Forgotten: delete tokens/settings-pin.json (here and on the PC).

Device to Natro, each with an id that comes back in the answer ({"type": "keys", "id": ..., "ok": ...}):
  {"type": "keys", "id": "k1", "do": "status"}                      → "pin": whether there is one yet
  {"type": "keys", "id": "k2", "do": "unlock", "pin": "1234"}       → "session", "fields", "new_pin"; or
                                                                      "message" and "wait" (seconds)
  {"type": "keys", "id": "k3", "do": "save", "session": "...", "values": {"TAVILY_API_KEY": "..."}}
                                                                    → "restarting"; or "message", "errors"
                                                                      (key -> why)
  {"type": "keys", "id": "k4", "do": "pin", "session": "...", "pin": "5678"}   a new PIN
  {"type": "keys", "id": "k5", "do": "lock", "session": "..."}
Fields: {"key", "label", "secret", "set", "value" (plain ones), "hint" (a key's "••••ab12"), "note"}.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from natro_agent import log

# systemd's Restart=on-failure starts Natro again after an exit with any code but 0 (EX_TEMPFAIL).
RESTART_CODE = 75
FREE_TRIES = 5
FIRST_WAIT_SECONDS = 30
LONGEST_WAIT_SECONDS = 3600
SESSION_SECONDS = 300
CHECK_SECONDS = 15
PIN = re.compile(r"\d{4,12}")
# scrypt's cost: 16 MB and about 50 ms a try.
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class Refused(Exception):
    """A value that can't be saved; its message says why."""


def _status(url, headers):
    """GET url: its HTTP status, or 0 if it couldn't be reached. Blocks."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=CHECK_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def _verdict(provider, status):
    # 429: too many requests, but the key was known.
    if status in (200, 429):
        return
    if status in (400, 401, 403):
        raise Refused(f"{provider} doesn't accept this key.")
    if status == 0:
        raise Refused(f"Couldn't reach {provider} to check the key.")
    raise Refused(f"{provider} answered {status} to the check: try again.")


def check_anthropic(key):
    _verdict("Anthropic", _status("https://api.anthropic.com/v1/models?limit=1",
                                  {"x-api-key": key, "anthropic-version": "2023-06-01"}))
    return key


def check_tavily(key):
    _verdict("Tavily", _status("https://api.tavily.com/usage", {"Authorization": f"Bearer {key}"}))
    return key


def check_maps(key):
    """One short walk in Tbilisi, as travel.py asks (over IPv4: the key may be limited to this server's address).
    The Routes API's free monthly allowance covers it."""
    from natro_agent import travel

    body = {"origin": {"location": {"latLng": {"latitude": 41.6938, "longitude": 44.8015}}},
            "destination": {"location": {"latLng": {"latitude": 41.6966, "longitude": 44.8009}}},
            "travelMode": "WALK"}
    try:
        travel.post(travel.URL, body, key)
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
    except OSError:
        status = 0
    _verdict("Google Maps", status)
    return key


def check_budget(value):
    try:
        budget = float(value)
    except ValueError:
        raise Refused("Not a number of dollars.") from None
    if not 0 < budget <= 1000:
        raise Refused("Between 0 and 1000 dollars.")
    return f"{budget:g}"


def check_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise Refused("Not a time zone (like Asia/Tbilisi).") from None
    return value


def check_project(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value):
        raise Refused("Not a Google Cloud project ID (6 to 30 lowercase letters, digits and hyphens).")
    return value


@dataclass
class Field:
    key: str
    label: str
    # Blocks (it may ask the provider); returns the value to save, or raises Refused.
    check: Callable[[str], str]
    secret: bool = False
    note: str = ""


FIELDS = [
    Field("ANTHROPIC_API_KEY", "Anthropic API key", check_anthropic, secret=True,
          note="Claude: Haiku and Sonnet answer every request"),
    Field("TAVILY_API_KEY", "Tavily API key", check_tavily, secret=True, note="Web search"),
    Field("GOOGLE_MAPS_API_KEY", "Google Maps API key", check_maps, secret=True,
          note="Travel times and routes (the Routes API)"),
    Field("NATRO_MONTHLY_BUDGET", "Monthly budget (USD)", check_budget,
          note="Natro warns at 80% and asks before using Claude past it"),
    Field("NATRO_TIMEZONE", "Time zone", check_timezone),
    Field("GOOGLE_CLOUD_PROJECT", "Google Cloud project", check_project, note="Speech-to-Text"),
]


def parse_env(text):
    """KEY=value lines, as config.load_env reads them."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values


def rewrite_env(text, values):
    """The .env text with these keys set: each line changed in place (or a commented-out "# KEY=" line, if
    that's all there is); a key that isn't there at all is added at the end. Everything else stays."""
    lines = text.splitlines()
    found = set()
    for pattern in (r"([A-Za-z_]\w*)\s*=.*", r"#\s*([A-Za-z_]\w*)\s*=.*"):
        for i, line in enumerate(lines):
            match = re.fullmatch(pattern, line.strip())
            # Every line that sets it; a commented-out one only if there's none (and only the first).
            if match and match[1] in values and (pattern[0] != "#" or match[1] not in found):
                lines[i] = f"{match[1]}={values[match[1]]}"
                found.add(match[1])
    lines += [f"{key}={value}" for key, value in values.items() if key not in found]
    return "\n".join(lines) + "\n"


def _private(path):
    """Only its owner may read it (on Linux)."""
    if os.name == "posix":
        os.chmod(path, 0o600)


def save_env(path, values):
    """Set these keys in the .env file at path; the old file stays as .env.bak beside it."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old:
        backup = path.with_name(path.name + ".bak")
        backup.write_text(old, encoding="utf-8")
        _private(backup)
    new = path.with_name(path.name + ".new")
    new.write_text(rewrite_env(old, values), encoding="utf-8")
    _private(new)
    os.replace(new, path)


def hint(value):
    """A key as the window shows it: its last four characters, if it's long enough for that to say little."""
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) >= 12 else "••••"


def clean(value):
    """A pasted value: no spaces or quotes around it."""
    return str(value).strip().strip("\"'").strip()


def hash_pin(pin, salt):
    return hashlib.scrypt(pin.encode(), salt=salt, **SCRYPT)


class Keys:
    """env: the .env file; pin_file: where the PIN's hash is kept. restart is set by the server: it ends Natro,
    for systemd to start her again with the new .env."""

    def __init__(self, env, pin_file, fields=FIELDS, clock=time.monotonic):
        self.env = env
        self.pin_file = pin_file
        self.fields = {field.key: field for field in fields}
        self.clock = clock
        self.restart = lambda: None
        self._failures = 0
        self._not_before = 0.0
        self._session = None  # (token, device, last used)

    async def handle(self, device, data):
        """A device's keys message: the answer (without its type and id)."""
        action = data.get("do")
        if action == "status":
            return {"ok": True, "pin": self.pin_file.exists()}
        if action == "unlock":
            return self._unlock(device, str(data.get("pin", "")))
        if action == "lock":
            if self._session and hmac.compare_digest(self._session[0], str(data.get("session", ""))):
                self._session = None
            return {"ok": True}
        if not self._in_session(device, str(data.get("session", ""))):
            return {"ok": False, "locked": True, "message": "Locked: enter the PIN again."}
        if action == "pin":
            pin = str(data.get("pin", ""))
            if not PIN.fullmatch(pin):
                return {"ok": False, "message": "A PIN is 4 to 12 digits."}
            self._set_pin(pin)
            log.write("keys", device=device, event="new pin")
            return {"ok": True}
        if action == "save":
            values = data.get("values")
            return await self._save(device, values if isinstance(values, dict) else {})
        return {"ok": False, "message": f"Natro doesn't know {action!r}."}

    def _unlock(self, device, pin):
        wait = self._not_before - self.clock()
        if wait > 0:
            return {"ok": False, "message": f"Too many wrong tries: wait {_duration(wait)}.", "wait": round(wait)}
        if not PIN.fullmatch(pin):
            return {"ok": False, "message": "A PIN is 4 to 12 digits."}
        new = not self.pin_file.exists()
        if new:
            self._set_pin(pin)
        elif not self._pin_is(pin):
            self._failures += 1
            log.write("keys", device=device, event="wrong pin", tries=self._failures)
            if self._failures >= FREE_TRIES:
                wait = min(FIRST_WAIT_SECONDS * 2 ** (self._failures - FREE_TRIES), LONGEST_WAIT_SECONDS)
                self._not_before = self.clock() + wait
                return {"ok": False, "message": f"Wrong PIN. Wait {_duration(wait)} before the next try.",
                        "wait": wait}
            left = FREE_TRIES - self._failures
            return {"ok": False, "message": f"Wrong PIN ({left} {'try' if left == 1 else 'tries'} before a wait)."}
        self._failures = 0
        token = secrets.token_urlsafe(24)
        self._session = (token, device, self.clock())
        log.write("keys", device=device, event="unlocked", new_pin=new)
        return {"ok": True, "session": token, "fields": self.shown(), "new_pin": new}

    def _in_session(self, device, token):
        if not self._session:
            return False
        known, owner, used = self._session
        if owner != device or not hmac.compare_digest(known, token):
            return False
        if self.clock() - used > SESSION_SECONDS:
            self._session = None
            return False
        self._session = (known, owner, self.clock())
        return True

    def _set_pin(self, pin):
        salt = secrets.token_bytes(16)
        self.pin_file.parent.mkdir(parents=True, exist_ok=True)
        self.pin_file.write_text(json.dumps({"salt": salt.hex(), "hash": hash_pin(pin, salt).hex(), **SCRYPT}))
        _private(self.pin_file)

    def _pin_is(self, pin):
        try:
            kept = json.loads(self.pin_file.read_text())
            salt, expected = bytes.fromhex(kept["salt"]), bytes.fromhex(kept["hash"])
        except (OSError, ValueError, KeyError):
            return False
        return hmac.compare_digest(hash_pin(pin, salt), expected)

    def current(self):
        """The fields' values now: the .env file's (what Natro will start with), else what she started with."""
        saved = parse_env(self.env.read_text(encoding="utf-8")) if self.env.exists() else {}
        return {key: saved.get(key) or os.environ.get(key, "") for key in self.fields}

    def shown(self):
        values = self.current()
        return [{"key": field.key, "label": field.label, "secret": field.secret, "set": bool(values[field.key]),
                 "value": "" if field.secret else values[field.key],
                 "hint": hint(values[field.key]) if field.secret else "", "note": field.note}
                for field in self.fields.values()]

    async def _save(self, device, asked):
        current = self.current()
        changes, errors = {}, {}
        for key, value in asked.items():
            value = clean(value)
            if key not in self.fields:
                errors[key] = "This can't be changed from the app."
            elif not value:
                errors[key] = "Empty."
            elif re.search(r"\s", value):
                errors[key] = "No spaces."
            elif value != current[key]:
                changes[key] = value
        checked = await asyncio.gather(*(asyncio.to_thread(self._check, key, value)
                                         for key, value in changes.items()))
        for key, (value, problem) in zip(list(changes), checked):
            if problem:
                errors[key] = problem
            else:
                changes[key] = value
        if errors:
            log.write("keys", device=device, event="refused", keys=sorted(errors))
            names = ", ".join(self.fields[key].label if key in self.fields else key for key in errors)
            return {"ok": False, "message": f"Nothing was saved: check {names}.", "errors": errors}
        if not changes:
            return {"ok": True, "restarting": False, "fields": self.shown()}
        save_env(self.env, changes)
        log.write("keys", device=device, event="saved", keys=sorted(changes))
        return {"ok": True, "restarting": True, "fields": self.shown()}

    def _check(self, key, value):
        """(the value to save, None), or (None, why it can't be)."""
        try:
            return self.fields[key].check(value), None
        except Refused as e:
            return None, str(e)


def _duration(seconds):
    seconds = max(1, round(seconds))
    if seconds < 90:
        return f"{seconds} seconds"
    return f"{round(seconds / 60)} minutes"
