"""The dashboard: a page on Natro's own address (/dashboard) that shows how she's used and how she's doing:
spending against the month's budget, requests, models, tokens, tools, the devices, the VPS, her reminders,
routines, watchers and jobs, and the log. Read-only: nothing on it changes Natro.

It's served beside the WebSocket (serve's process_request), so it's reachable only where Natro listens: on the
VPS her Tailscale address, never a public one. A web page from anywhere else can't read it either: a request
must name that address (or localhost, or a name in NATRO_DASHBOARD_HOSTS) as its Host, so a site that points a
name of its own at the address (DNS rebinding) gets nothing. The page loads nothing from anywhere else.

GET /dashboard                         the page (web/)
GET /dashboard/api/stats?range=7d      24h, 7d, 30d or month: spending, tokens, requests, models, tools, issues,
                                       where his requests' time went (timing.py), and the same totals for the
                                       span just before, to compare
GET /dashboard/api/live                now: the service, the devices, the VPS, this month's spending, what's
                                       scheduled and running
GET /dashboard/api/logs?range=7d&kinds=request,tool&device=pc&q=flight&before=2026-10-19:120
                                       log entries, newest first, at most LOGS_PAGE (limit= for fewer); after=
                                       instead of before= for those newer than it (the page's live tail); day=
                                       or month= instead of range=. A request comes with its tool calls and
                                       where its time went.
GET /dashboard/api/year                his requests on each day of the past year, from a Sunday (as GitHub
                                       draws them)
GET /dashboard/api/month?month=2026-10 a month's bill: spending by day, model and command, against the budget
GET /dashboard/api/month.csv?month=…   that month's requests and what each cost, to download
GET /dashboard/places                  the Places page, the one page that saves, over WS /dashboard/socket
                                       (places_page.py)

One module per page, as in web/app.js: home.py, activity.py, billing.py, speed.py and system.py, with logs.py for
reading the log. This one serves them.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs

from natro_agent.dashboard import activity, billing, home, system
# Re-exported: tests and the other pages import these from here.
from natro_agent.dashboard.activity import attachments, cursor, matches  # noqa: F401
from natro_agent.dashboard.billing import command, commands, csv_cell  # noqa: F401
from natro_agent.dashboard.home import summarize, totals  # noqa: F401
from natro_agent.dashboard.logs import Logs, month_span, span  # noqa: F401
from natro_agent.dashboard.speed import stages, timings  # noqa: F401
from natro_agent.dashboard.system import Host, version

WEB = Path(__file__).resolve().parent.parent / "web"
FILES = {"": "index.html", "/": "index.html", "/style.css": "style.css", "/icon.svg": "icon.svg",
         # The page's script: app.js and the modules it imports, one per page.
         **{f"/{name}.js": f"{name}.js" for name in ("app", "common", "charts", "load", "home", "activity", "billing",
                                                      "speed", "system")},
         "/places.js": "places.js", "/places.css": "places.css",
         # Noto Sans, Latin and Georgian (SIL Open Font License, web/fonts/OFL.txt).
         "/fonts/noto-sans.woff2": "fonts/noto-sans.woff2",
         "/fonts/noto-sans-georgian.woff2": "fonts/noto-sans-georgian.woff2"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2"}
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Dashboard:
    """server: the server.Server whose state the page shows. listen: the host Natro listens on. places: a
    places_page.PlacesPage (None: no such page)."""

    def __init__(self, server, listen="127.0.0.1", logs=None, places=None):
        self.server = server
        self.places = places
        self.hosts = LOCAL_HOSTS | {listen.strip("[]")} | {
            name.strip().lower() for name in os.environ.get("NATRO_DASHBOARD_HOSTS", "").split(",") if name.strip()}
        self.logs = logs or Logs()
        self.host = Host()
        self.started = time.time()
        self.version = version()

    async def http(self, connection, request):
        """serve()'s process_request for the dashboard's addresses (None for any other: a WebSocket)."""
        path, _, query = request.path.partition("?")
        if path == "/" and request.headers.get("Upgrade", "").lower() != "websocket":  # a browser at her address
            response = connection.respond(302, "")
            response.headers["Location"] = "/dashboard"
            return response
        if path != "/dashboard" and not path.startswith("/dashboard/"):
            return None
        if host_name(request.headers.get("Host")) not in self.hosts:
            return connection.respond(403, "Open the dashboard at the address Natro listens on.\n")
        path = path.removeprefix("/dashboard")
        args = {key: values[-1] for key, values in parse_qs(query).items()}
        try:
            if path in FILES:
                return page(connection, WEB / FILES[path])
            if path == "/places" and self.places:
                return self.places.page(connection)
            if path == "/socket" and self.places:
                return self.places.refused(connection, request)
            if path == "/api/stats":
                return reply(connection, await asyncio.to_thread(self.stats, args.get("range", "7d")))
            if path == "/api/live":
                return reply(connection, await self.live())
            if path == "/api/logs":
                return reply(connection, await asyncio.to_thread(self.entries, args))
            if path == "/api/year":
                return reply(connection, await asyncio.to_thread(self.year))
            if path == "/api/month":
                return reply(connection, await asyncio.to_thread(self.month, args.get("month")))
            if path == "/api/month.csv":
                return download(connection, *await asyncio.to_thread(self.month_csv, args.get("month")))
        except Exception as e:  # the page says so; Natro goes on
            return reply(connection, {"error": repr(e)}, 500)
        return connection.respond(404, "Not found.\n")

    async def websocket(self, connection):
        """serve()'s handler: the Places page's socket, or (any other address) a device."""
        if self.places and connection.request.path.partition("?")[0] == "/dashboard/socket":
            await self.places.handle(connection)
        else:
            await self.server.handle(connection)


    def stats(self, name):
        return home.stats(self.logs, name)

    async def live(self):
        return await system.live(self)

    def year(self):
        return home.year(self.logs)

    def month(self, value):
        return billing.month(self.logs, self.server.agent.ledger.budget, value)

    def month_csv(self, value):
        return billing.month_csv(self.logs, value)

    def entries(self, args):
        return activity.entries(self.logs, args)


def host_name(header):
    """The host a request names, without its port: "100.101.107.5:8700" -> "100.101.107.5"."""
    host = str(header or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]")[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host



def page(connection, path):
    response = connection.respond(200, "")
    response.body = path.read_bytes()
    set_headers(response, TYPES.get(path.suffix, "application/octet-stream"))
    return response


def download(connection, name, text):
    response = connection.respond(200, "")
    response.body = text.encode("utf-8-sig")  # with a BOM, so spreadsheets read it as UTF-8 (Georgian too)
    set_headers(response, "text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


def reply(connection, data, status=200):
    response = connection.respond(status, "")
    response.body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    set_headers(response, "application/json; charset=utf-8")
    return response


def set_headers(response, content_type, others=None):
    """others: headers to send in place of the usual ones."""
    headers = response.headers
    for key, value in {"Content-Type": content_type, "Content-Length": str(len(response.body)), **HEADERS,
                       **(others or {})}.items():
        if key in headers:
            del headers[key]
        headers[key] = value
