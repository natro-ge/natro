"""The System page and the live view: the service, the devices, the VPS, this month's spending, and what's
scheduled and running."""
import asyncio
import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from natro_agent import identity
from natro_agent.config import ROOT, timezone
from natro_agent.reminders import repeat_words
from natro_agent.watchers import describe

BACKUPS = Path.home() / "backups"


async def live(dashboard):
    """dashboard: the Dashboard whose server, host, start and version it shows."""
    server = dashboard.server
    agent = server.agent
    connected = {name: device for name, device in list(agent.devices.items()) if hasattr(device, "connection")}
    devices = []
    for name in dict.fromkeys([*identity.KNOWN_DEVICES, *connected]):
        device = connected.get(name)
        used = server.delivery.used.get(name)
        devices.append({
            "name": name,
            "label": device.label if device else identity.KNOWN_DEVICES.get(name, name),
            "online": device is not None,
            "since": stamp(getattr(device, "since", None)),
            "used": stamp(time.time() - (time.monotonic() - used)) if used else None,
            "tools": len(device.specs) if device else None,
            "version": getattr(device, "version", None),  # its app's build, if it says (app_updates.py)
            "works_on": getattr(device, "works_on", None),  # where its switch says it works (works_on.py)
            "abilities": [ability for ability in ("files", "chat", "reminders", "jobs", "alerts", "questions",
                                                  "phone_messages")
                          if device and getattr(device, ability, False)],
        })
    seen = await asyncio.to_thread(last_seen, dashboard.logs)
    for device in devices:
        device["seen"] = seen.get(device["name"])
    spent, budget = agent.ledger.spent, agent.ledger.budget
    now = datetime.now(timezone())
    month_days = ((now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    elapsed = (now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
    return {
        "now": now.isoformat(),
        "service": {
            "started": stamp(dashboard.started),
            "version": dashboard.version,
            "python": platform.python_version(),
            "voice": server.voice is not None,
            "model": getattr(agent, "model", None),
            "process": dashboard.host.own(),
        },
        "month": {"spent": round(spent, 4), "budget": budget, "days": month_days, "elapsed_days": elapsed,
                  "projected": round(spent / elapsed * month_days, 4) if elapsed > 0.5 else None},
        "devices": devices,
        "host": await asyncio.to_thread(dashboard.host.now),
        "backups": backups(),
        "jobs": [job_item(job) for job in reversed(server.jobs.items)] if server.jobs else [],
        "reminders": reminder_items(server.reminders, now) if server.reminders else [],
        "routines": [routine_item(item) for item in server.routines.items] if server.routines else [],
        "watchers": [{"id": item["id"], "kind": item.get("kind"), "text": describe_safely(item),
                      "urgent": bool(item.get("urgent")), "once": bool(item.get("once"))}
                     for item in server.watchers.items] if server.watchers else [],
        "pending": len(server.pending.items) if server.pending else 0,
    }


def last_seen(logs):
    """When each device last connected or went, from the past week's log."""
    now = datetime.now(timezone())
    seen = {}
    for _, _, at, entry in logs.between(now - timedelta(days=7), now + timedelta(minutes=1)):
        if entry.get("kind") == "device":
            seen[entry.get("device")] = at.isoformat()
    return seen


class Host:
    """The VPS (Linux; elsewhere, what Python can tell): CPU, memory, disk, load; and Natro's own process."""

    def __init__(self):
        self._cpu = None  # (busy, total) at the last look
        self._own = None  # (her CPU seconds, when)

    def now(self):
        memory = meminfo()
        disk = shutil.disk_usage(ROOT)
        return {
            "name": socket.gethostname(),
            "system": f"{platform.system()} {platform.release()}",
            "cores": os.cpu_count(),
            "cpu": self.cpu(),
            "load": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "memory": memory,
            "disk": {"total": disk.total, "used": disk.used},
            "uptime": read_float("/proc/uptime"),
        }

    def cpu(self):
        """The share of CPU time busy since the last look (the first: over a moment)."""
        try:
            first = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        except OSError:
            return None
        values = [int(value) for value in first]
        idle, total = values[3] + (values[4] if len(values) > 4 else 0), sum(values)
        if self._cpu is None:
            self._cpu = (total - idle, total)
            time.sleep(0.2)
            return self.cpu()
        busy_before, total_before = self._cpu
        self._cpu = (total - idle, total)
        return (total - idle - busy_before) / (total - total_before) if total > total_before else 0.0

    def own(self):
        """Natro's process: its memory (and its service's limit, under systemd) and CPU since the last look."""
        result = {"pid": os.getpid(), "memory": None, "memory_max": None, "cpu": None}
        try:
            group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
            folder = Path("/sys/fs/cgroup") / group.lstrip("/")
            result["memory"] = int((folder / "memory.current").read_text())
            limit = (folder / "memory.max").read_text().strip()
            result["memory_max"] = int(limit) if limit.isdigit() else None
        except (OSError, IndexError, ValueError):
            for line in read_lines("/proc/self/status"):
                if line.startswith("VmRSS:"):
                    result["memory"] = int(line.split()[1]) * 1024
        try:
            fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
            seconds = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
            now = time.monotonic()
            if self._own:
                result["cpu"] = (seconds - self._own[0]) / max(now - self._own[1], 1e-6)
            self._own = (seconds, now)
        except (OSError, IndexError, ValueError, AttributeError):
            pass
        return result


def meminfo():
    values = {}
    for line in read_lines("/proc/meminfo"):
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0]) * 1024
    if "MemTotal" not in values:
        return None
    return {"total": values["MemTotal"], "available": values.get("MemAvailable")}


def read_lines(path):
    try:
        return Path(path).read_text().splitlines()
    except OSError:
        return []


def read_float(path):
    try:
        return float(Path(path).read_text().split()[0])
    except (OSError, IndexError, ValueError):
        return None


def version():
    """The commit Natro runs: short hash, date, subject."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h%x00%cI%x00%s"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", timeout=5).stdout.strip()
        commit, when, subject = out.split("\x00", 2)
        return {"commit": commit, "date": when, "subject": subject}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def backups(folder=BACKUPS):
    """The nightly copies of memory and notes (server/deploy/backup.sh)."""
    try:
        found = sorted(folder.glob("natro-*.tar.gz"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return None
    if not found:
        return None
    newest = found[-1].stat()
    return {"count": len(found), "last": datetime.fromtimestamp(newest.st_mtime, timezone()).isoformat(),
            "size": newest.st_size}


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone()).isoformat() if seconds else None


def job_item(job):
    return {"id": job.id, "title": job.title, "task": job.task[:300], "state": job.state, "step": job.step,
            "started": stamp(job.started), "finished": stamp(job.finished), "minutes": job.minutes(),
            "result": job.result[:300], "routine": bool(job.routine)}


def reminder_items(reminders, now):
    items = []
    for item in reminders.items:
        repeat = item.get("repeat") or ""
        items.append({"id": item["id"], "text": item["text"], "next": item.get("next"),
                      "repeat": safely(repeat_words, repeat) if repeat else "", "on": item.get("on")})
    return sorted(items, key=lambda item: (item["next"] is None, item["next"] or ""))


def routine_item(item):
    repeat = item.get("repeat") or ""
    last = item.get("last") or None
    if last and isinstance(last.get("at"), (int, float)):
        last = {**last, "at": stamp(last["at"] / 1000)}
    return {"id": item["id"], "name": item.get("name") or "", "task": str(item.get("task") or "")[:300],
            "next": item.get("next"), "repeat": safely(repeat_words, repeat) if repeat else "once",
            "quiet": bool(item.get("quiet")), "last": last}


def describe_safely(item):
    return safely(describe, item) or item.get("kind", "")


def safely(function, value):
    try:
        return function(value)
    except Exception:  # one odd item must not take the page down
        return ""
