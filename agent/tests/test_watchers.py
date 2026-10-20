"""Watchers: what to watch for, the checks (email, meetings, the phone's notifications), and alerts through the server."""
import json
import time
from datetime import datetime, timedelta

import pytest

from conftest import FakeClient, run
from natro_agent import chats
from natro_agent.agent import Agent
from natro_agent.config import timezone
from natro_agent.google_tools import CALENDAR, GMAIL
from natro_agent.server import Server
from natro_agent.tools import ToolError
from natro_agent.watchers import STALE_HOURS, Watchers, sender
from test_google import google
from test_shared_chat import devices, hello

# A Sunday afternoon where he is.
NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone())


class Phone:
    """A connected device, as the watchers see it: what was posted to it."""

    def __init__(self, name="phone", alerts=True, watch=True):
        self.name, self.alerts, self.watch_notifications = name, alerts, watch
        self.posted = []

    def post(self, **message):
        self.posted.append(message)


class Shown:
    """Stands in for the server: records each alert delivered; online=False means nobody could see it."""

    def __init__(self):
        self.online = True
        self.alerts = []

    def __call__(self, alert):
        if self.online:
            self.alerts.append(alert)
        return self.online


def make(tmp_path, answers=None, devices=None, now=NOW, signed_in=True):
    g, fake = google(answers or {}) if signed_in else (None, None)
    tmp_path.mkdir(parents=True, exist_ok=True)
    watchers = Watchers(tmp_path / "watchers.json", devices if devices is not None else {}, g)
    watchers.now = lambda: now
    watchers.deliver = Shown()
    return watchers, fake


def watch(watchers, **args):
    return run(watchers.watch(args))


def mail(*messages):
    """Gmail's answers for a search that finds these (id, from, subject)."""
    answers = {("GET", f"{GMAIL}/messages"): (200, {"messages": [{"id": id} for id, _, _ in messages]})}
    for id, frm, subject in messages:
        answers[("GET", f"{GMAIL}/messages/{id}")] = (200, {"id": id, "payload": {"headers": [
            {"name": "From", "value": frm}, {"name": "Subject", "value": subject}]}})
    return answers


def event(id, start, summary="Dentist", **more):
    begin = start.isoformat()
    return {"id": id, "summary": summary, "start": {"dateTime": begin},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()}, **more}


# --- Setting them -------------------------------------------------------------------------------

def test_meeting_heads_ups_are_there_from_the_start(tmp_path):
    watchers, _ = make(tmp_path)
    item, = watchers.items
    assert (item["kind"], item["minutes"], item["urgent"]) == ("meetings", 10, True)
    said = watch(watchers, kind="meetings", minutes=15)
    assert said.splitlines()[0] == "OK, I'll give you a heads-up 15 minutes before each meeting."
    assert [item["minutes"] for item in watchers.items] == [15]  # one is enough
    with pytest.raises(ToolError, match="from 1 to 120"):
        watch(watchers, kind="meetings", minutes=500)


def test_an_email_watcher(tmp_path):
    watchers, _ = make(tmp_path)
    said = watch(watchers, kind="email", **{"from": "Nino"})
    assert said.splitlines()[0] == "OK, I'll tell you whenever Nino emails."
    said = watch(watchers, kind="email", once=True, about="the contract", **{"from": "Giorgi"})
    assert said.splitlines()[0] == "OK, I'll tell you the next time Giorgi emails about the contract."
    with pytest.raises(ToolError, match="who the email is from"):
        watch(watchers, kind="email")
    signed_out, _ = make(tmp_path / "out", signed_in=False)
    with pytest.raises(ToolError, match="isn't signed in"):
        watch(signed_out, kind="email", **{"from": "Nino"})


def test_a_notification_watcher_goes_to_the_phone(tmp_path):
    phone, old_pc = Phone(), Phone("pc", watch=False)
    watchers, _ = make(tmp_path, devices={"phone": phone, "pc": old_pc})
    said = watch(watchers, kind="notification", app="Bank of Georgia", words="transfer", urgent=True)
    assert said.splitlines()[0] == ("OK, I'll tell you whenever your phone gets a notification from Bank of "
                                    "Georgia with \"transfer\".")
    rules, = [m["rules"] for m in phone.posted if m["type"] == "watch_rules"]
    assert rules == [{"id": watchers.items[-1]["id"], "app": "Bank of Georgia", "words": "transfer"}]
    assert old_pc.posted == []
    with pytest.raises(ToolError, match="app, the words"):
        watch(watchers, kind="notification")
    offline, _ = make(tmp_path / "offline")
    assert "starts watching when it's back" in watch(offline, kind="notification", app="WhatsApp")


def test_list_and_cancel(tmp_path):
    phone = Phone()
    watchers, _ = make(tmp_path, devices={"phone": phone})
    watch(watchers, kind="email", **{"from": "Nino"})
    watch(watchers, kind="notification", words="BOG", once=True)
    meetings, nino, bog = watchers.items
    listed = run(watchers.list({}))
    assert listed == (f"Watching for:\n- {meetings['id']}: a heads-up 10 minutes before each meeting, said out loud\n"
                      f"- {nino['id']}: an email from Nino\n"
                      f"- {bog['id']}: a notification on his phone with \"BOG\", once")
    assert run(watchers.cancel({"id": bog["id"]})) == "Stopped watching for a notification on his phone with \"BOG\"."
    assert phone.posted[-1] == {"type": "watch_rules", "rules": []}  # the phone stops looking
    with pytest.raises(ToolError, match="no watcher"):
        run(watchers.cancel({"id": "w99"}))
    # Kept across a restart.
    again, _ = make(tmp_path)
    assert [item["id"] for item in again.items] == [meetings["id"], nino["id"]]


# --- The checks ---------------------------------------------------------------------------------

def test_an_email_from_someone_watched_is_told_once(tmp_path):
    watchers, fake = make(tmp_path, mail(("m2", "Nino Beridze <nino@example.com>", "Dinner on Friday"),
                                         ("m1", "nino@example.com", "")))
    watchers.items = []  # no meetings, for this one
    watch(watchers, kind="email", about="dinner", **{"from": "Nino"})
    run(watchers.check())
    assert [alert["text"] for alert in watchers.deliver.alerts] == ["nino@example.com emailed: (no subject)",
                                                                   "Nino Beridze emailed: Dinner on Friday"]
    assert not watchers.deliver.alerts[0]["urgent"]
    query = next(params["q"] for method, url, params, _ in fake.requests if url == f"{GMAIL}/messages")
    made = int(watchers.items[0]["made"] / 1000) - 60
    assert query == f"in:inbox is:unread after:{made} from:(Nino) dinner"
    watchers._mail_at = 0  # the next check, two minutes on: nothing new
    run(watchers.check())
    assert len(watchers.deliver.alerts) == 2


def test_a_once_email_watcher_goes_after_it_tells(tmp_path):
    watchers, _ = make(tmp_path, mail(("m1", "Giorgi <g@example.com>", "The contract")))
    watch(watchers, kind="email", once=True, **{"from": "Giorgi"})
    run(watchers.check())
    assert [alert["text"] for alert in watchers.deliver.alerts] == ["Giorgi emailed: The contract"]
    assert [item["kind"] for item in watchers.items] == ["meetings"]


def test_a_heads_up_before_a_meeting(tmp_path):
    events = {"items": [
        event("e1", NOW + timedelta(minutes=9), location="City clinic"),
        event("e2", NOW + timedelta(minutes=40), summary="Padel"),  # later
        event("e3", NOW + timedelta(minutes=5), summary="Call",
              attendees=[{"self": True, "responseStatus": "declined"}]),
        event("e4", NOW - timedelta(minutes=5), summary="Under way"),
        {"id": "e5", "summary": "Nino's birthday", "start": {"date": "2026-10-18"}, "end": {"date": "2026-10-19"}},
    ]}
    watchers, fake = make(tmp_path, {("GET", f"{CALENDAR}/events"): (200, events)})
    run(watchers.check())
    alert, = watchers.deliver.alerts
    assert alert["text"] == "Dentist at City clinic in 9 minutes." and alert["urgent"] and alert["kind"] == "meetings"
    assert alert["until"] == int((NOW + timedelta(minutes=9)).timestamp() * 1000)
    run(watchers.check())  # a minute on: not again, and the events aren't looked up again yet
    assert len(watchers.deliver.alerts) == 1
    assert len([r for r in fake.requests if r[1] == f"{CALENDAR}/events"]) == 1
    # Not again after a restart either.
    again, _ = make(tmp_path, {("GET", f"{CALENDAR}/events"): (200, events)})
    run(again.check())
    assert again.deliver.alerts == []


def test_without_google_nothing_is_checked(tmp_path):
    watchers, _ = make(tmp_path, signed_in=False)
    run(watchers.check())
    assert watchers.deliver.alerts == []


def test_a_notification_the_phone_matched(tmp_path):
    phone = Phone()
    watchers, _ = make(tmp_path, devices={"phone": phone})
    watch(watchers, kind="notification", app="Bank of Georgia", once=True, urgent=True)
    rule = watchers.items[-1]["id"]
    watchers.matched(rule, "Bank of Georgia", "Transfer", "You received 500 GEL.")
    alert, = watchers.deliver.alerts
    assert alert["text"] == "Bank of Georgia: Transfer: You received 500 GEL." and alert["urgent"]
    assert phone.posted[-1] == {"type": "watch_rules", "rules": []}  # once: gone
    watchers.matched(rule, "Bank of Georgia", "Transfer", "Again.")  # already gone: nothing
    assert len(watchers.deliver.alerts) == 1


def test_alerts_wait_for_a_device_until_they_are_stale(tmp_path):
    watchers, _ = make(tmp_path, mail(("m1", "Nino", "Hi")))
    watchers.items = []
    watch(watchers, kind="email", **{"from": "Nino"})
    watchers.deliver.online = False
    run(watchers.check())
    assert [alert["text"] for alert in watchers.waiting] == ["Nino emailed: Hi"]
    watchers.deliver.online = True
    watchers.connected(Phone(alerts=False, watch=False))  # an app that doesn't show alerts
    assert watchers.waiting
    watchers.connected(Phone())
    assert [alert["text"] for alert in watchers.deliver.alerts] == ["Nino emailed: Hi"] and not watchers.waiting
    # Too late: dropped.
    watchers.deliver.online = False
    watchers._mail_at = 0
    watchers.items[-1]["seen"] = []
    run(watchers.check())
    watchers.now = lambda: NOW + timedelta(hours=STALE_HOURS + 1)
    watchers.deliver.online = True
    watchers.connected(Phone())
    assert len(watchers.deliver.alerts) == 1 and not watchers.waiting


def test_sender_names():
    assert sender("Nino Beridze <nino@example.com>") == "Nino Beridze"
    assert sender("nino@example.com") == "nino@example.com"
    assert sender("") == "Someone"


def test_the_tools_are_personal(tmp_path):
    watchers, _ = make(tmp_path)
    tools = {tool.name: tool for tool in watchers.tools()}
    assert set(tools) == {"watch", "watchers_list", "watchers_cancel"}
    assert all(tool.personal and not tool.confirm for tool in tools.values())
    assert tools["watch"].final


# --- Through the server -------------------------------------------------------------------------

def alert_hello(name, label, watch=False):
    return {**hello(name, label), "alerts": True, "watch_notifications": watch}


def test_an_alert_shows_everywhere_and_an_urgent_one_is_spoken_where_he_is(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "PIECE_SECONDS", 0)
    agent = Agent(client=FakeClient())
    watchers = Watchers(tmp_path / "watchers.json", agent.devices)
    server = Server(agent, watchers=watchers)
    run(watchers.watch({"kind": "notification", "app": "Bank of Georgia", "urgent": True}))
    rule = watchers.items[-1]["id"]

    async def play(pc, phone):
        rules = await phone.next("watch_rules")
        await phone.send(type="notification_matched", rule=rule, app="Bank of Georgia", title="Transfer",
                         text="You received 500 GEL.")
        return rules, await pc.next("alert"), await phone.next("alert"), await pc.next("chat_entry")

    rules, on_pc, on_phone, entry = run(devices(server, alert_hello("pc", "PC"), alert_hello("phone", "phone", True),
                                                play=play))
    assert rules["rules"] == [{"id": rule, "app": "Bank of Georgia", "words": ""}]
    assert on_pc["text"] == on_phone["text"] == "Bank of Georgia: Transfer: You received 500 GEL."
    assert (on_pc["speak"], on_phone["speak"]) == (False, True)  # neither used lately: the phone
    entry = entry["entry"]
    assert entry["alert"] and not entry["reminder"] and entry["device"] == "natro" and entry["asked"] == ""
    # Claude hears of it with the next request, and answers that one.
    assert agent.private_notes == ["you told him: \"Bank of Georgia: Transfer: You received 500 GEL.\""]
    assert agent.personal_next


def test_a_meeting_heads_up_says_nothing_to_claude(tmp_path):
    agent = Agent(client=FakeClient())
    watchers = Watchers(tmp_path / "watchers.json", agent.devices)
    server = Server(agent, watchers=watchers)

    async def play(phone):
        watchers._alert(watchers.items[0], "Dentist in 10 minutes.", NOW)
        return await phone.next("alert")

    shown = run(devices(server, alert_hello("phone", "phone"), play=play))
    assert shown["speak"] and shown["kind"] == "meetings"
    assert agent.private_notes == [] and not agent.personal_next
