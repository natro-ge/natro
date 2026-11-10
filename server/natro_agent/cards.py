"""Cards: a voucher, loyalty card or ticket he saved as a picture, shown full screen on his phone for scanning at the
till. "Save this as my Carrefour card" keeps the picture he just sent, with an expiry date if he says one; "show my
Carrefour card" shows it now; "show my Carrefour card when I get there" shows it when he gets to one of his places or
leaves it (places.py).

A card set at a place is the phone's to show, as the shop may have no signal: its place_rules name it, the phone asks
for a copy of each (card_get), and keeps them until no place names them. The server only keeps count of what's been
shown: one that's once goes when the phone says it got there. The messages are in shared/PROTOCOL.md (card, card_get).

Kept in cards/: the pictures, and cards.json.
"""
import itertools
import json
import time
from datetime import date, datetime

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.places import name_key, where_words
from natro_agent.tools import Tool, ToolError

MOST = 50
MOST_NAME_CHARS = 60
# Its file's end, by its type.
ENDS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic",
        "image/heif": ".heif"}


class Cards:
    """folder: where they're kept (made if missing). devices: name -> device, while connected (the agent's). places: the
    places.Places they can be set at (None: none). The server sets picture_sent() -> (mime, bytes) of the picture he
    sent just now, or None."""

    def __init__(self, folder, devices, places=None):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.devices = devices
        self.places = places
        if places:
            places.users.append(self)
        self.picture_sent = lambda: None
        self._ids = itertools.count(1)
        self.cards, self.at_places = self._load()

    def tools(self):
        place = {"type": "string", "description": "To show it when he gets to one of his saved places or leaves it, "
                                                  "not now: the place, by name."}
        return [
            Tool("card_save",
                 "Keep the picture he just sent as a card (a voucher, loyalty card or ticket), to show at the till: "
                 "\"save this as my Carrefour card\". Saving a name he has already replaces it.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "What he calls it: \"Carrefour\"."},
                     "expires": {"type": "string", "description": "The day it ends, if he says one: 2026-12-31."},
                 }, "required": ["name"]},
                 self.save, "natro", changes=True, final=True),
            Tool("card_show",
                 "Show one of his cards full screen on his phone: now (\"show my Carrefour card\"), or when he gets to "
                 "one of his places or leaves it (\"show my Carrefour card when I get there\"): then the phone keeps a "
                 "copy, and shows it even with no signal.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "The card, by name."},
                     "place": place,
                     "on": {"type": "string", "enum": ["arrive", "leave"],
                            "description": "With place: when he gets there, or leaves. Default arrive."},
                     "every_time": {"type": "boolean", "description": "With place: every time, when he says so; else "
                                                                      "once."},
                 }, "required": ["name"]},
                 self.show, "natro", changes=True, final=True),
            Tool("cards_list",
                 "His cards, when each expires, and where each is set to show.",
                 {"type": "object", "properties": {}}, self.list, "natro"),
            Tool("card_forget",
                 "Forget one of his cards, its picture and where it's set to show, when he asks you to.",
                 {"type": "object", "properties": {"name": {"type": "string", "description": "The card, by name."}},
                  "required": ["name"]},
                 self.forget, "natro", confirm=True),
        ]

    # Tools

    async def save(self, args):
        name = " ".join(str(args.get("name") or "").split())[:MOST_NAME_CHARS]
        if not name:
            raise ToolError("Say what he calls it.")
        sent = self.picture_sent()
        if sent is None:
            raise ToolError("He hasn't just sent a picture: ask him to send it into the chat, then save it.")
        mime, data = sent
        expires = self._day(args.get("expires"))
        old = self._named(name)
        if old is None and len(self.cards) >= MOST:
            raise ToolError(f"He already has {MOST} cards: forget some first.")
        card = old or {"id": f"cd{int(time.time() * 1000):x}{next(self._ids)}", "name": name}
        if old:
            self._picture(old).unlink(missing_ok=True)
        else:
            self.cards.append(card)
        card.update(mime=mime, expires=expires, saved=int(time.time() * 1000))
        self._picture(card).write_bytes(data)
        self._save()
        log.write("card", event="saved", card=card["name"])
        ending = f" It ends on {date.fromisoformat(expires):%d %B %Y}." if expires else ""
        return f"Saved your {card['name']} card.{ending}"

    async def show(self, args):
        card = self._named(args.get("name"))
        if card is None:
            raise ToolError(f"He has no card called \"{' '.join(str(args.get('name') or '').split())}\": ask him to send "
                            "its picture, and save it with card_save.")
        if str(args.get("place") or "").strip():
            return self._set_at_place(card, args)
        phone = self.devices.get("phone")
        if phone is None:
            raise ToolError("His phone is offline, so it can't show the card.")
        if not getattr(phone, "cards", False):
            raise ToolError("Natro's app on his phone can't show cards yet: it needs updating.")
        self._send(phone, card, show=True)
        log.write("card", event="shown", card=card["name"])
        ended = self._ended(card)
        return f"It's on your phone.{f' It ended on {ended}.' if ended else ''}"

    def _set_at_place(self, card, args):
        if not self.places:
            raise ToolError("Natro has no places here.")
        place, event = self.places.trigger(args)
        every = bool(args.get("every_time"))
        self.at_places.append({"id": f"ct{int(time.time() * 1000):x}{next(self._ids)}", "card": card["id"],
                               "place": place["id"], "place_on": event, "every": every})
        self._save()
        self.places.refresh()
        log.write("card", event="set", card=card["name"], place=place["name"], on=event, every=every)
        return (f"OK, I'll show your {card['name']} card {where_words(place, event)}"
                f"{', every time' if every else ''}. Your phone keeps a copy.")

    async def list(self, args):
        if not self.cards:
            return "No cards."
        lines = []
        for card in self.cards:
            ended = self._ended(card)
            ends = f", ended on {ended}" if ended else f", ends on {card['expires']}" if card.get("expires") else ""
            lines.append(f"- {card['name']}{ends}")
            for shown in self.at_places:
                place = self._place(shown)
                if shown["card"] == card["id"] and place:
                    every = "every time" if shown["every"] else "once"
                    lines.append(f"  {where_words(place, shown['place_on'])}, {every} ({shown['id']})")
        return "\n".join(lines)

    async def forget(self, args):
        card = self._named(args.get("name"))
        if card is None:
            raise ToolError(f"He has no card called \"{args.get('name')}\".")
        self._picture(card).unlink(missing_ok=True)
        self.cards.remove(card)
        self.at_places = [shown for shown in self.at_places if shown["card"] != card["id"]]
        self._save()
        if self.places:
            self.places.refresh()
        log.write("card", event="forgotten", card=card["name"])
        return f"Forgot your {card['name']} card."

    # Devices

    def messages(self):
        return {"card_get": self._get}

    def _get(self, device, data):
        """The phone wants a copy of a card (a place names it), or (show) to show it now."""
        card = next((card for card in self.cards if card["id"] == str(data.get("id") or "")), None)
        if card is not None and getattr(device, "cards", False):
            self._send(device, card, show=bool(data.get("show")))

    def _send(self, device, card, show):
        device.post(type="card", id=card["id"], name=card["name"], mime=card["mime"], show=show,
                    ended=self._ended(card))
        device.post_bytes(self._picture(card).read_bytes())

    # For places.py

    def on_phone(self, place_id):
        """The cards this place shows, as place_rules names them: the phone shows them itself."""
        named = []
        for shown in self.at_places:
            card = next((card for card in self.cards if card["id"] == shown["card"]), None)
            if shown["place"] == place_id and card is not None and not self._ended(card):
                named.append({"id": shown["id"], "card": card["id"], "name": card["name"], "on": shown["place_on"],
                              "every": shown["every"]})
        return named

    def happening(self, place_id):
        names = {card["id"]: card["name"] for card in self.cards}
        return [{"id": shown["id"], "kind": "card", "on": shown["place_on"], "every": shown["every"],
                 "text": f"show your {names.get(shown['card'], '')} card"}
                for shown in self.at_places if shown["place"] == place_id]

    def drop(self, id):
        shown = next((shown for shown in self.at_places if shown["id"] == id), None)
        if shown is None:
            return False
        self.at_places.remove(shown)
        self._save()
        self.places.refresh()
        return True

    def place_gone(self, place_id):
        self.at_places = [shown for shown in self.at_places if shown["place"] != place_id]
        self._save()

    def place_event(self, place, event, at, late):
        """He got to a place or left it: the phone has shown what it names, so a card that's once is done."""
        done = [shown for shown in self.at_places if shown["place"] == place["id"] and shown["place_on"] == event
                and not shown["every"]]
        if done:
            self.at_places = [shown for shown in self.at_places if shown not in done]
            self._save()
            self.places.refresh()

    # Finding them

    def _named(self, name):
        wanted = name_key(name)
        return next((card for card in self.cards if name_key(card["name"]) == wanted), None) if wanted else None

    def _place(self, shown):
        return next((place for place in self.places.items if place["id"] == shown["place"]), None) if self.places else None

    def _picture(self, card):
        return self.folder / f"{card['id']}{ENDS.get(card['mime'], '.picture')}"

    @staticmethod
    def _day(value):
        """A day he said, as 2026-12-31, or ""."""
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            raise ToolError("expires is a day like 2026-12-31.")

    @staticmethod
    def _ended(card):
        """The day it ended, in words, if that's past; else ""."""
        expires = card.get("expires")
        if not expires or date.fromisoformat(expires) >= datetime.now(timezone()).date():
            return ""
        return f"{date.fromisoformat(expires):%d %B %Y}"

    # On disk

    def _load(self):
        try:
            data = json.loads((self.folder / "cards.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], []
        cards = [card for card in data.get("cards", []) if isinstance(card, dict)
                 and all(key in card for key in ("id", "name", "mime"))]
        shown = [item for item in data.get("at_places", []) if isinstance(item, dict)
                 and all(key in item for key in ("id", "card", "place", "place_on"))]
        return cards, [{"every": False, **item} for item in shown]

    def _save(self):
        partial = self.folder / "cards.part"
        partial.write_text(json.dumps({"cards": self.cards, "at_places": self.at_places}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        partial.replace(self.folder / "cards.json")
