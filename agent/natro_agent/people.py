"""Natro's own address book for messaging (send_message, sending.py): memory/people.md, editable by hand.

One line per chat on Instagram or Messenger, its parts split by " · ": the name as the app shows it, the app, then
any of: the chat's id ("chat ..."), on Messenger the person's Facebook id ("facebook ..."), the username
("username ..."), "group", and the day the chat last wrote ("last 2026-10-22"):

    - Nino Kapanadze · Instagram · chat 340282366841710300949128137443944319108 · username nino.k · last 2026-10-22
    - Andria Beridze · Messenger · chat 7483920 · facebook 100012345678901 · last 2026-10-22

It fills itself in: the phone sends "person" (the app, the name, the ids, whether it's a group; never what the
message says) the first time a chat writes each day. Usernames come from him ("Nino's Instagram is nino.k", or his
answer when she asks), through people_save; people_forget takes a line out. It isn't in her instructions: it grows
with each new person, and each change would write her cached instructions again; send_message looks people up.
Personal.

Device to Natro:
  {"type": "person", "app": "instagram", "name": "Nino Kapanadze", "chat": "3402...", "facebook": "",
   "group": false}                                                  a chat wrote (once a day per chat)
"""
import os
import re
import urllib.parse
from dataclasses import dataclass, replace
from datetime import date

from natro_agent.tools import Tool, ToolError

APPS = {"instagram": "Instagram", "messenger": "Messenger"}
HEADER = ("# People he messages\n\n"
          "Natro adds a line when a chat on Instagram or Messenger writes to him, and keeps it up to date; usernames "
          "come from him. Edit freely: one line per chat, its parts split by \" · \".\n\n")
# Instagram: letters, digits, "." and "_", up to 30. Facebook (Messenger's m.me links): letters, digits and ".",
# at least 5.
USERNAME = {"instagram": re.compile(r"[a-z0-9._]{1,30}"), "messenger": re.compile(r"[a-z0-9.]{5,50}")}
GEORGIAN = dict(zip("აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
                    ["a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh", "r", "s", "t",
                     "u", "p", "k", "gh", "q", "sh", "ch", "ts", "dz", "ts", "ch", "kh", "j", "h"]))
# Words he may add to a group's name ("the Family group").
GROUP_WORDS = {"the", "group", "chat"}


@dataclass
class Person:
    name: str
    app: str  # instagram or messenger
    chat: str = ""  # the chat's id, as the app's notification names it
    facebook: str = ""  # Messenger: the person's Facebook id
    username: str = ""
    group: bool = False
    last: str = ""  # the day the chat last wrote, YYYY-MM-DD

    def line(self):
        parts = [self.name, APPS[self.app]]
        parts += ["group"] if self.group else []
        parts += [f"chat {self.chat}"] if self.chat else []
        parts += [f"facebook {self.facebook}"] if self.facebook else []
        parts += [f"username {self.username}"] if self.username else []
        parts += [f"last {self.last}"] if self.last else []
        return "- " + " · ".join(parts)

    def shown(self):
        """As she'd name them to him: the name, and the username when there's one."""
        if self.name and self.username and plain(self.name) != self.username:
            return f"{self.name} ({self.username})"
        return self.name or self.username


def plain(text):
    """Lower case, Georgian letters in Latin ones, spaces collapsed: "ნინო  Kapanadze" -> "nino kapanadze"."""
    latin = "".join(GEORGIAN.get(char, char) for char in str(text).casefold())
    return " ".join(re.sub(r"[^\w.@ ]", " ", latin).split())


def username_from(app, text):
    """A username as he gave it ("@nino.k", "instagram.com/nino.k/", "m.me/nino.kapanadze"), checked; or a
    ToolError saying what's wrong with it."""
    text = str(text or "").strip()
    if "/" in text:
        path = urllib.parse.urlsplit(text if "://" in text else f"https://{text}").path
        text = next((part for part in path.split("/") if part and part not in ("m", "t", "direct")), "")
    text = text.lstrip("@").casefold()
    if app not in USERNAME or not USERNAME[app].fullmatch(text) or text.strip(".") != text:
        rule = ("letters, digits, \".\" and \"_\", up to 30" if app == "instagram"
                else "letters, digits and \".\", at least 5")
        raise ToolError(f"\"{text}\" isn't a {APPS.get(app, app)} username ({rule}): ask him for it again.")
    return text


def app_of(text):
    """instagram or messenger, from how he said it (default Instagram); else a ToolError."""
    said = plain(text or "instagram")
    if said in ("instagram", "insta", "ig"):
        return "instagram"
    if said in ("messenger", "facebook", "facebook messenger", "fb"):
        return "messenger"
    raise ToolError(f"Natro messages people on Instagram and Messenger only, not {text}.")


class People:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        people = []
        for line in lines:
            if not line.startswith("- "):
                continue
            parts = [part.strip() for part in line[2:].split(" · ")]
            if len(parts) < 2 or plain(parts[1]) not in APPS:
                continue
            person = Person(parts[0], plain(parts[1]))
            for part in parts[2:]:
                key, _, value = part.partition(" ")
                key, value = key.casefold(), value.strip()
                if key == "group" and not value:
                    person.group = True
                elif key in ("chat", "facebook", "last") and value:
                    setattr(person, key, value)
                elif key == "username" and value:
                    person.username = value.lstrip("@").casefold()
            people.append(person)
        return people

    def _save(self, people):
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            text = HEADER
        # His own lines that aren't people (headings, notes) stay where they are; people's lines are rewritten.
        kept = [line for line in text.splitlines() if not line.startswith("- ")]
        while kept and not kept[-1].strip():
            kept.pop()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(self.path.name + ".tmp")
        partial.write_text("\n".join(kept + [""] + [person.line() for person in people]) + "\n", encoding="utf-8")
        os.replace(partial, self.path)

    def seen(self, app, name, chat="", facebook="", group=False, today=None):
        """A chat wrote to him (the phone's "person"): its line added, or brought up to date. Returns whether the
        file changed."""
        app, name = str(app or "").casefold(), " ".join(str(name or "").split())[:80]
        chat, facebook = str(chat or "").strip()[:200], re.sub(r"\D", "", str(facebook or ""))[:30]
        if app not in APPS or not name or not (chat or facebook) or " · " in name:
            return False
        today = (today or date.today()).isoformat()
        people = self.load()
        found = next((p for p in people if p.app == app and chat and p.chat == chat), None) \
            or next((p for p in people if p.app == app and facebook and p.facebook == facebook and not p.group), None) \
            or next((p for p in people if p.app == app and not p.chat and not p.facebook
                     and plain(p.name) == plain(name)), None)  # one he added with only a username
        updated = (replace(found, name=name, chat=chat or found.chat, facebook=facebook or found.facebook,
                           group=bool(group), last=today) if found
                   else Person(name, app, chat, facebook, group=bool(group), last=today))
        if found == updated:
            return False
        if found:
            people[people.index(found)] = updated
        else:
            people.append(updated)
        self._save(people)
        return True

    def find(self, app, to):
        """The people in the book who fit whom he named (by name in either alphabet, or by username), in one app.
        A group fits only when he names it whole."""
        said = plain(to).removeprefix("@")
        words = [word for word in (word.strip(".") for word in said.split()) if word]
        if not words:
            return []
        group_name = " ".join(word for word in words if word not in GROUP_WORDS)
        exact, close = [], []
        for person in (p for p in self.load() if p.app == app):
            name = plain(person.name)
            parts = [part.strip(".") for part in name.split()]
            if person.group:
                if name in (said, group_name):
                    exact.append(person)
            elif said in (name, person.username):
                exact.append(person)
            elif all(any(part.startswith(word) for part in parts) for word in words):
                close.append(person)
        return exact or close

    def username(self, app, name, username):
        """people_save: his username for someone; a new line if they aren't in the book yet."""
        people = self.load()
        fits = [p for p in self.find(app, name) if not p.group]
        if len(fits) > 1:
            raise ToolError(f"Several people fit \"{name}\" on {APPS[app]}: "
                            f"{', '.join(person.shown() for person in fits)}. Ask him which one.")
        if fits:
            person = fits[0]
            people[people.index(person)] = replace(person, username=username)
            name = person.name
        else:
            name = " ".join(str(name).split())[:80]
            if not name or " · " in name:
                raise ToolError("Say whose username it is.")
            people.append(Person(name, app, username=username))
        self._save(people)
        return f"Saved: {name} is {username} on {APPS[app]}."

    def forget(self, app, name):
        people = self.load()
        fits = self.find(app, name)
        if not fits:
            raise ToolError(f"Nobody called \"{name}\" is in Natro's address book for {APPS[app]}.")
        if len(fits) > 1:
            raise ToolError(f"Several fit \"{name}\" on {APPS[app]}: {', '.join(p.shown() for p in fits)}. "
                            "Ask him which one.")
        people.remove(fits[0])
        self._save(people)
        return f"Forgot {fits[0].shown()} on {APPS[app]}."

    def tools(self):
        async def save(args):
            app = app_of(args.get("app"))
            return self.username(app, str(args.get("name") or ""), username_from(app, args.get("username")))

        async def forget(args):
            return self.forget(app_of(args.get("app")), str(args.get("name") or ""))

        app = {"type": "string", "enum": ["instagram", "messenger"], "description": "Instagram unless he says "
                                                                                   "Messenger."}
        return [
            Tool("people_save",
                 "Save someone's username for messaging them (send_message), when he tells you it (\"Nino's "
                 "Instagram is nino.k\", or his answer when you asked). Only a username he gave: never guess one.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "Whose it is, as he says it."},
                     "username": {"type": "string", "description": "As he gave it: nino.k, @nino.k or a profile "
                                                                   "link."},
                     "app": app}, "required": ["name", "username"]},
                 save, "natro", personal=True),
            Tool("people_forget",
                 "Take someone out of the address book Natro messages people from, when he asks.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "Who, as he says it."}, "app": app},
                  "required": ["name"]},
                 forget, "natro", personal=True, confirm=True),
        ]
