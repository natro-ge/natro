"""The dashboard's Places page: his places on Google's map, to mark, move and delete. It's a document of its own, as a
policy goes with a document and only this page may load Google's scripts; every other page keeps the dashboard's.

GET /dashboard/places    the page (web/places.html, places.js, places.css), with a new nonce each time, the way Google
                         asks for a strict policy, and the map's key (GOOGLE_MAPS_BROWSER_KEY), which the browser sees,
                         as it sees every map key: it's limited to this address, and only draws the map. Addresses are
                         looked up here (places.py), on the Routes API's key.
WS  /dashboard/socket    how the page saves, as the dashboard's server answers GET only. Admitted only with the
                         dashboard's own Origin and Host (with dashboard/__init__.py's check of the Host), so a
                         page from another site open in his browser can't use it; the tailnet stays the boundary.
  page to server   {"type": "place_save", "id": "pl1" (none: a new one), "name": "Gym", "names": ["the gym"],
                    "lat": 41.54, "lng": 45.0, "radius": 150, "address": "..." (place_found's, if it's from there)}
                   {"type": "place_delete", "id": "pl1"}
                   {"type": "place_search", "text": "Carrefour Rustavi"}
                   {"type": "place_happening_delete", "id": "rm1"}: stop something set to happen at a place
  server to page   {"type": "places", "places": [{"id", "name", "names", "lat", "lng", "radius", "address",
                    "happens": [{"id", "kind", "on", "every", "text"}]}]}, all of them: when it connects, and on each
                   change, from here or by voice
                   {"type": "place_saved", "id": "pl1", "name": "Gym"}, to the page that saved it
                   {"type": "place_found", "text": "Carrefour Rustavi", "found": [{"address", "lat", "lng"}]}
                   {"type": "place_error", "for": "place_save", "message": "..."}
"""
import html
import json
import os
import secrets
import urllib.parse

from websockets.asyncio.server import broadcast
from websockets.exceptions import ConnectionClosed

from natro_agent.dashboard import WEB, set_headers
from natro_agent.tools import ToolError

MOST_SEARCH_CHARS = 200


def policy(nonce):
    """Google's strict policy for its map, with what the dashboard's own files need."""
    return (f"default-src 'none'; script-src 'nonce-{nonce}' 'strict-dynamic' https: 'unsafe-eval' blob:; "
            f"style-src 'nonce-{nonce}' https://fonts.googleapis.com; "
            "img-src 'self' data: https://*.googleapis.com https://*.gstatic.com *.google.com *.googleusercontent.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self' https://*.googleapis.com *.google.com https://*.gstatic.com data: blob:; "
            "frame-src *.google.com; worker-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class PlacesPage:
    """places: the places.Places it shows. key: the map's (default GOOGLE_MAPS_BROWSER_KEY; none: the page has no map,
    and its list, search and form still work)."""

    def __init__(self, places, key=None):
        self.places = places
        self.key = os.environ.get("GOOGLE_MAPS_BROWSER_KEY", "") if key is None else key
        self._open = set()  # the pages open now
        places.changed = self._changed

    def page(self, connection):
        nonce = secrets.token_urlsafe(16)
        text = (WEB / "places.html").read_text(encoding="utf-8")
        response = connection.respond(200, "")
        response.body = (text.replace("{{nonce}}", nonce)
                         .replace("{{key}}", html.escape(self.key, quote=True)).encode("utf-8"))
        # Its address goes to Google as the referrer, which the map's key is limited to. On http it's sent to https.
        set_headers(response, "text/html; charset=utf-8", {"Content-Security-Policy": policy(nonce),
                                                           "Referrer-Policy": "strict-origin-when-cross-origin"})
        return response

    @staticmethod
    def refused(connection, request):
        """A response, if this socket can't open: only the dashboard's own page may. None lets it open."""
        origin = urllib.parse.urlsplit(request.headers.get("Origin", ""))
        host = str(request.headers.get("Host", "")).strip().lower()
        if host and origin.scheme in ("http", "https") and origin.netloc.lower() == host:
            return None
        return connection.respond(403, "Only the dashboard's own page can use this.\n")

    async def handle(self, connection):
        """One open page, until it's closed."""
        self._open.add(connection)
        try:
            await connection.send(self._all())
            async for message in connection:
                await self._message(connection, message)
        except ConnectionClosed:
            pass
        finally:
            self._open.discard(connection)

    def _all(self):
        places = [{**item, "happens": self.places.happening(item["id"])} for item in self.places.items]
        return json.dumps({"type": "places", "places": places}, ensure_ascii=False)

    def _changed(self):
        broadcast(self._open, self._all())

    async def _message(self, connection, message):
        try:
            data = json.loads(message)
        except ValueError:
            return
        kind = data.get("type") if isinstance(data, dict) else None
        try:
            if kind == "place_save":
                item = await self.places.mark(data.get("id"), data.get("name"), data.get("names"), data.get("lat"),
                                              data.get("lng"), data.get("radius"), data.get("address"))
                await self._send(connection, type="place_saved", id=item["id"], name=item["name"])
            elif kind == "place_delete":
                self.places.remove(str(data.get("id") or ""))
            elif kind == "place_happening_delete":
                self.places.stop_happening(str(data.get("id") or ""))
            elif kind == "place_search":
                text = " ".join(str(data.get("text") or "").split())[:MOST_SEARCH_CHARS]
                found = await self.places.search(text) if text else []
                await self._send(connection, type="place_found", text=text, found=found)
        except ToolError as e:
            await self._send(connection, **{"type": "place_error", "for": kind, "message": str(e)})

    @staticmethod
    async def _send(connection, **message):
        await connection.send(json.dumps(message, ensure_ascii=False))
