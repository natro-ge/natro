"""The dashboard: what it makes of the log, how it reads the log as it grows, and who may open it."""
import asyncio
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from conftest import FakeClient, run
from natro_agent.agent import Agent
from natro_agent.config import timezone
from natro_agent.dashboard import (Dashboard, Logs, attachments, command, commands, host_name, month_span, span,
                                   summarize)
from natro_agent.jobs import Jobs
from natro_agent.server import Server, health

NOW = datetime(2026, 10, 19, 15, 30, tzinfo=timezone())


def at(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


ENTRIES = [
    {"time": at(30), "kind": "request", "device": "phone", "model": "claude-sonnet-5-5", "cost": 0.05, "voice_cost": 0.01,
     "seconds": 4.0, "tokens": {"input_tokens": 10, "cache_read_input_tokens": 900, "output_tokens": 50}},
    {"time": at(2), "kind": "request", "device": "pc", "model": "claude-haiku-5-5", "cost": 0.01, "voice_cost": 0.0,
     "seconds": 2.0, "tokens": {"input_tokens": 10, "cache_creation_input_tokens": 90, "output_tokens": 20}},
    {"time": at(2), "kind": "tool", "device": "pc", "tool": "spotify_play", "ok": True},
    {"time": at(2), "kind": "tool", "device": "pc", "tool": "spotify_play", "ok": False},
    {"time": at(1), "kind": "tool", "device": "pc", "tool": "pc_force_close_app", "ok": False, "declined": True},
    {"time": at(1), "kind": "request", "device": "test", "model": "gemini-3.8-flash", "cost": 0, "seconds": 1.0},
    {"time": at(1), "kind": "request", "device": "pc", "text": "stop", "cancelled": True, "voice_cost": 0},
    {"time": at(1), "kind": "job", "id": "jb1", "event": "done", "minutes": 3, "cost": 0.2},
    {"time": at(1), "kind": "voice", "device": "phone", "voice_cost": 0.003},
    {"time": at(1), "kind": "model_rest", "model": "gemini-3.8-flash", "seconds": 600, "failures": 1, "error": "503"},
]


def rows(entries):
    return [(None, n, datetime.fromisoformat(entry["time"]).astimezone(timezone()), entry) for n, entry in enumerate(entries)]


def test_a_week_summed_by_day_model_device_and_tool():
    starts, end, unit = span("7d", NOW)
    found = summarize(rows(ENTRIES), starts, end, unit)

    assert unit == "day" and len(found["buckets"]) == 7 and found["buckets"][-1].startswith("2026-10-19T00:00")
    assert found["spend"]["sonnet"][-2] == 0.05 and found["spend"]["haiku"][-1] == 0.01
    assert found["spend"]["jobs"][-1] == 0.2 and found["spend"]["voice"][-2:] == [0.01, 0.003]
    assert found["spent"] == round(0.05 + 0.01 + 0.01 + 0.2 + 0.003, 6)  # what the month's ledger sums
    assert found["requests"] == {"phone": [0] * 5 + [1, 0], "pc": [0] * 6 + [2], "other": [0] * 6 + [1]}
    assert found["request_count"] == 4 and found["cancelled"] == 1
    assert found["seconds"]["median"][-1] == 2.0 and found["seconds"]["all_median"] == 2.0
    assert found["token_totals"]["cache_read_input_tokens"] == 900
    assert round(found["cache_share"], 3) == round(900 / (20 + 90 + 900), 3)
    assert found["tools"][0] == {"tool": "spotify_play", "calls": 2, "failed": 1, "declined": 0}
    assert found["tool_calls"] == 3 and found["tool_failed"] == 1 and found["tool_declined"] == 1
    assert found["tool_buckets"][-1] == 3
    assert found["issues"] == {"model_rest": 1}
    assert found["recent_issues"][0]["text"] == "gemini-3.8-flash rests 600 s after 1 failure"
    assert sum(map(sum, found["week"])) == 4
    assert [m["model"] for m in found["models"]][:1] == ["claude-haiku-5-5"]


def test_the_last_24_hours_are_by_hour():
    starts, end, unit = span("24h", NOW)
    found = summarize(rows(ENTRIES), starts, end, unit)

    assert unit == "hour" and len(starts) == 24 and end == NOW.replace(minute=0) + timedelta(hours=1)
    assert found["request_count"] == 3  # the phone's, 30 hours ago, is outside
    assert found["requests"]["pc"][-3] == 1


def test_the_log_is_read_as_it_grows(tmp_path):
    logs = Logs(tmp_path)
    day = datetime.now()
    path = tmp_path / f"agent-{day:%Y-%m-%d}.jsonl"
    first = {"time": day.isoformat(timespec="seconds"), "kind": "device", "device": "pc", "event": "connected"}
    path.write_text(json.dumps(first) + "\n" + '{"time": "', encoding="utf-8")  # the second line is half written

    assert [entry for _, _, entry in logs.day(day.date())] == [first]
    with open(path, "a", encoding="utf-8") as f:
        f.write(f'{day.isoformat(timespec="seconds")}", "kind": "tool", "tool": "remind"}}\n')
    found = logs.day(day.date())
    assert [entry["kind"] for _, _, entry in found] == ["device", "tool"] and found[1][0] == 1


def test_a_request_s_line_on_the_bill():
    assert command(["calendar_create_event", "gmail_send"], "claude-sonnet-5-5") == "Calendar"
    assert command(["pc_force_close_app"], "none (quick)") == "PC" and command(["send_file"], "x") == "Files"
    assert command([], "none (quick)") == "Quick, no tools" and command(None, "claude-haiku-5-5") == "Conversation"
    assert command(["frobnicate_now"], "x") == "Frobnicate"


def test_the_bill_by_command_adds_up_to_what_was_spent():
    starts, end, unit = span("7d", NOW)
    found = commands(rows(ENTRIES))
    by_name = {item["name"]: item for item in found}

    assert round(sum(item["cost"] for item in found), 6) == summarize(rows(ENTRIES), starts, end, unit)["spent"]
    assert found[0] == {"name": "Background jobs", "times": 1, "cost": 0.2}
    assert by_name["Conversation"]["times"] == 3 and by_name["Cancelled"]["times"] == 1
    assert by_name["Heard, not answered"]["cost"] == 0.003


def test_a_request_s_tools_and_time_go_with_it():
    entries = [
        {"time": at(1), "kind": "tool", "device": "pc", "tool": "spotify_play", "args": {"query": "jazz"}, "ok": True},
        {"time": at(1), "kind": "request", "device": "pc", "text": "play jazz", "tools": ["spotify_play"]},
        {"time": at(1), "kind": "timing", "device": "pc", "id": "r1", "text": "play jazz", "total": 2.0,
         "calls": [{"seconds": 1.0, "first_token": 0.6}], "steps": [0.5]},
        {"time": at(1), "kind": "spoken", "device": "pc", "id": "r1", "seconds": 0.4},
        {"time": at(1), "kind": "request", "device": "phone", "text": "hi"},
        {"time": at(1), "kind": "timing", "device": "phone", "id": "r2", "text": "someone else's"},
    ]
    found = attachments(rows(entries))

    assert found[(None, 1)]["tools"] == [{"tool": "spotify_play", "args": {"query": "jazz"}, "ok": True}]
    assert found[(None, 1)]["stages"] == {"speech": 0, "translate": 0, "first_token": 0.6, "writing": 0.4,
                                          "tools": 0.5, "speaking": 0.4, "other": 0.5}
    assert found[(None, 4)] == {"tools": []}


def test_months_and_the_days_of_a_year(tmp_path):
    starts, end = month_span("2026-02", NOW)
    assert len(starts) == 28 and starts[0].day == 1 and end == datetime(2026, 3, 1, tzinfo=timezone())
    assert month_span("next week", NOW)[0][0] == datetime(2026, 10, 1, tzinfo=timezone())

    logs = Logs(tmp_path)
    days = [datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=d) for d in (0, 3)]
    for day, devices in zip(days, (["pc", "phone", "test"], ["job"])):
        (tmp_path / f"agent-{day:%Y-%m-%d}.jsonl").write_text("".join(json.dumps(
            {"time": day.isoformat(), "kind": "request", "device": device}) + "\n" for device in devices), encoding="utf-8")
    mine = [day.astimezone(timezone()).date() for day in days]

    assert logs.requests_per_day(mine[1], mine[0]) == {mine[0]: 2, mine[1]: 1}  # not the test device's
    with open(tmp_path / f"agent-{days[0]:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": days[0].isoformat(), "kind": "request", "device": "pc"}) + "\n")
    assert logs.requests_per_day(mine[0], mine[0]) == {mine[0]: 3}  # counted again once it grew
    assert logs.months() == sorted({f"{day:%Y-%m}" for day in days})


def test_host_names():
    assert host_name("100.101.107.5:8700") == "100.101.107.5"
    assert host_name("[::1]:8700") == "::1"
    assert host_name("LOCALHOST") == "localhost"
    assert host_name(None) == ""


async def fetch(port, path, host=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host} if host else {})

    def go():
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()
    return await asyncio.to_thread(go)


def test_the_page_its_data_and_who_may_open_it(tmp_path):
    day = datetime.now()
    (tmp_path / f"agent-{day:%Y-%m-%d}.jsonl").write_text("".join(json.dumps(
        {"time": day.isoformat(timespec="seconds"), "kind": "request", "device": "pc", "text": f"hello {word}",
         "model": "claude-sonnet-5-5", "cost": 0.01, "seconds": 2.0}) + "\n" for word in ("alpha", "beta", "gamma")),
        encoding="utf-8")
    server = Server(Agent(client=FakeClient()), token="secret", jobs=Jobs())
    dashboard = Dashboard(server, "127.0.0.1", Logs(tmp_path))

    async def check():
        async with serve(server.handle, "127.0.0.1", 0,
                         process_request=lambda c, r: health(c, r) or dashboard.http(c, r)) as listener:
            port = listener.sockets[0].getsockname()[1]
            page = await fetch(port, "/dashboard")
            script = await fetch(port, "/dashboard/app.js")
            stats = await fetch(port, "/dashboard/api/stats?range=24h")
            live = await fetch(port, "/dashboard/api/live")
            logs = await fetch(port, "/dashboard/api/logs?q=Hello+GAMMA")
            none = await fetch(port, "/dashboard/api/logs?kinds=-request,-tool")
            one_day = await fetch(port, f"/dashboard/api/logs?day={today}&kinds=request&limit=2")
            year = await fetch(port, "/dashboard/api/year")
            month = await fetch(port, "/dashboard/api/month")
            sheet = await fetch(port, "/dashboard/api/month.csv")
            font = await fetch(port, "/dashboard/fonts/noto-sans-georgian.woff2")
            older = await fetch(port, f"/dashboard/api/logs?before={day:%Y-%m-%d}:1")
            elsewhere = await fetch(port, "/dashboard/api/logs", host=f"natro.attacker.example:{port}")
            browser = await fetch(port, "/")
            ok = await fetch(port, "/health")
            async with connect(f"ws://127.0.0.1:{port}") as ws:  # the WebSocket at / still works
                await ws.send(json.dumps({"type": "hello", "device": "pc", "token": "secret"}))
                welcome = json.loads(await ws.recv())
            return page, script, stats, live, logs, older, elsewhere, browser, ok, welcome, (one_day, year, month,
                                                                                             sheet, font, none)

    today = datetime.now(timezone()).date()
    page, script, stats, live, logs, older, elsewhere, browser, ok, welcome, new = run(check())
    one_day, year, month, sheet, font, none = new
    assert page[0] == 200 and page[1]["Content-Type"].startswith("text/html") and b"Natro Dashboard" in page[2]
    assert "script-src 'self'" in page[1]["Content-Security-Policy"] and page[1]["Cache-Control"] == "no-store"
    assert script[0] == 200 and script[1]["Content-Type"].startswith("text/javascript")
    data = json.loads(stats[2])
    assert stats[1]["Content-Type"].startswith("application/json") and data["request_count"] == 3
    now = json.loads(live[2])
    assert now["month"]["budget"] == server.agent.ledger.budget and [d["name"] for d in now["devices"]] == ["pc", "phone"]
    assert now["service"]["voice"] is False and now["jobs"] == []
    found = json.loads(logs[2])
    assert [row["entry"]["text"] for row in found["entries"]] == ["hello gamma"] and found["matched"] == 1
    assert [row["entry"]["text"] for row in json.loads(older[2])["entries"]] == ["hello alpha"]
    assert elsewhere[0] == 403
    assert browser[0] == 200 and b"Natro Dashboard" in browser[2]  # redirected to /dashboard
    assert ok[2] == b"ok\n" and welcome["type"] == "welcome"
    assert [row["entry"]["text"] for row in json.loads(one_day[2])["entries"]] == ["hello gamma", "hello beta"]
    days = json.loads(year[2])
    assert date.fromisoformat(days["start"]).weekday() == 6 and days["today"] == today.isoformat()
    assert days["counts"][-1] == 3 and len(days["counts"]) == (today - date.fromisoformat(days["start"])).days + 1
    bill = json.loads(month[2])
    assert bill["current"] and bill["budget"] == server.agent.ledger.budget and bill["month"] == f"{today:%Y-%m}"
    assert bill["commands"] == [{"name": "Conversation", "times": 3, "cost": 0.03}] and bill["quick"] == 0
    assert sheet[1]["Content-Type"].startswith("text/csv") and "attachment" in sheet[1]["Content-Disposition"]
    assert sheet[2].startswith(b"\xef\xbb\xbftime,device,request") and len(sheet[2].splitlines()) == 4
    assert font[0] == 200 and font[1]["Content-Type"] == "font/woff2"
    assert json.loads(none[2])["entries"] == []  # all but requests and tools: none here
    assert "font-src 'self'" in page[1]["Content-Security-Policy"]
