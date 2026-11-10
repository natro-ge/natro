"""One thing done by itself when he gets to one of his places or leaves it, with no model: "when I leave work, text Nino
I'm on my way", "open Strava when I get to the gym", "play my evening playlist when I get home". place_action keeps the
tool and its arguments, worked out and checked now, and they run as they are then. He's told what happened.

Only what one call does whole can be set (a final tool: places.py's others are for the rest: a reminder for words to
say, a routine for something to work out then). Anything that asks first can't be done by a place, as he isn't there to
answer, with one exception of his (2026-11-01): a message. Its question comes now, in the server's words, with
everything that will go; a yes keeps that exact message and it goes without asking again; a no keeps nothing, and other
words, another person or another app are a new place_action, asked about again. Calls, deleting and risky taps stay
refused. Kept in place_actions.json.
"""
import asyncio
import itertools
import json
import time

from natro_agent import log, steps
from natro_agent.places import AGAIN_MINUTES, starting, where_words
from natro_agent.tool_calls import ASK, first_line
from natro_agent.tools import Tool, ToolError, result_text

MOST = 50
MOST_SMS_CHARS = 2000
# How long the tool has to run.
RUN_SECONDS = 180
# The only tools that ask first and can be approved ahead.
MESSAGES = {"send_message", "phone_send_sms"}
# Final tools that make no sense here: they set something up (use it with place instead), or need who asked.
NOT_AT_A_PLACE = {"remind", "routine_add", "start_job", "watch", "places_save", "work_on", "convert_file", "edit_pdf",
                  "card_save", "card_show", "week_plan_add"}
TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}


def check_args(schema, args):
    """A ToolError unless these are arguments the tool takes."""
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if args.get(name) in (None, ""):
            raise ToolError(f"It needs {name}.")
    for name, value in args.items():
        spec = properties.get(name)
        if spec is None:
            if properties:
                raise ToolError(f"It takes no argument {name!r}: only {', '.join(properties)}.")
            continue
        wanted = TYPES.get(spec.get("type"))
        if wanted and (not isinstance(value, wanted) or (isinstance(value, bool) and wanted is not bool)):
            raise ToolError(f"{name} is a {spec['type']}.")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{name} is one of: {', '.join(map(str, spec['enum']))}.")


def every_words(every):
    return "every time" if every else "once"


class PlaceActions:
    """path: the JSON file they're kept in. places: the places.Places they're at. The server sets offered() -> every
    tool on offer now (the agent's), sending (its sending.Sending, None: no send_message), tell(text) (tells him) and
    asking() -> the device the request being handled came from."""

    def __init__(self, path, places):
        self.path = path
        self.places = places
        places.users.append(self)
        self.offered = lambda: []
        self.sending = None
        self.tell = lambda text: None
        self.asking = lambda: None
        self._ids = itertools.count(1)
        self._tasks = set()
        self.items = self._load()

    def tools(self):
        return [
            Tool("place_action",
                 "Do one thing by itself when he gets to one of his places or leaves it, with no thought then: a message "
                 "(\"when I leave work, text Nino I'm on my way\"), an app (\"open Strava when I get to the gym\"), music "
                 "(\"play my evening playlist when I get home\"). Give the tool and its arguments exactly as you'd call "
                 "it now: they run as they are then. A message (send_message, phone_send_sms) asks him now, in its own "
                 "words, and a yes keeps that exact message: don't ask him first. Calls, deleting, paying and anything "
                 "else that asks first can't be done by a place: tell him so. Words to say then are remind with place; "
                 "something to work out then is routine_add with place.",
                 {"type": "object", "properties": {
                     "place": {"type": "string", "description": "One of his saved places, by name."},
                     "on": {"type": "string", "enum": ["arrive", "leave"], "description": "Default arrive."},
                     "tool": {"type": "string", "description": "The tool's name: send_message, phone_open_app, "
                                                               "spotify_play..."},
                     "args": {"type": "object", "description": "Its arguments, complete."},
                     "every_time": {"type": "boolean", "description": "True when he says every time; else once."},
                 }, "required": ["place", "tool", "args"]},
                 self.set, "natro", confirm=True, changes=True, final=True, own_question=True),
            Tool("place_action_cancel",
                 "Stop something set with place_action, by its id (from places_list). To change one, cancel it and set "
                 "it again.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                 self.cancel, "natro", changes=True),
        ]

    # Tools

    async def set(self, args):
        place, event = self.places.trigger(args)
        if len(self.items) >= MOST:
            raise ToolError(f"He already has {MOST} of these: cancel some first.")
        name = str(args.get("tool") or "").strip()
        given = args.get("args") or {}
        if not isinstance(given, dict):
            raise ToolError("args is the tool's arguments.")
        every = bool(args.get("every_time"))
        tool = next((tool for tool in self.offered() if tool.name == name), None)
        if tool is None:
            raise ToolError(f"There is no tool named {name!r}.")
        item = {"id": f"pa{int(time.time() * 1000):x}{next(self._ids)}", "place": place["id"], "place_on": event,
                "every": every, "tool": name, "args": given, "made": int(time.time() * 1000)}
        if name in MESSAGES:
            kept = await self._approved(item, tool, place, event)
            if kept is None:
                # The first line is the reply when nothing else is needed (final).
                return "I didn't keep it.\n(He said no, or didn't answer in time, so nothing will go.)"
        else:
            if tool.confirm:
                raise ToolError(f"{name} asks first, and he won't be there to answer: a place can't do that (only a "
                                "message, approved now). Tell him so.")
            if not tool.final or name in NOT_AT_A_PLACE:
                raise ToolError(f"{name} isn't one thing done whole: words to say then are remind with place, and "
                                "something to work out then is routine_add with place.")
            if "device" in (tool.input_schema.get("properties") or {}) and not given.get("device"):
                if asked_on := getattr(self.asking(), "name", None):
                    given["device"] = asked_on
            check_args(tool.input_schema, given)
            item["words"] = steps.describe(name, given).get("text") or name
        self.items.append(item)
        self._save()
        self.places.changed()
        log.write("place", event="action_set", id=item["id"], place=place["name"], on=event, tool=name, every=every)
        return (f"OK. {starting(where_words(place, event))}: {item['words']} ({every_words(every)}).\n"
                f"Place action {item['id']}.")

    async def _approved(self, item, tool, place, event):
        """A message: asked about now, in the server's words, with everything that will go. The item with what's to be
        sent, or None if he didn't say yes."""
        ask = ASK.get()
        where, every = starting(where_words(place, event)), every_words(item["every"])
        if item["tool"] == "send_message":
            if self.sending is None:
                raise ToolError("Natro can't send messages here.")
            ready = self.sending.prepare(item["args"])
            shown, label, text = ready["shown"], ready["label"], ready["text"]
            item["call"] = ready["call"]
            item["sent"] = f"Sent {shown} \"{text}\""
            item["words"] = f"send {shown} on {label}: \"{text}\""
            question = f"{where}, I'll send {shown} on {label}: \"{text}\" ({every}). OK?"
        else:
            check_args(tool.input_schema, item["args"])
            number, text = str(item["args"].get("number") or "").strip(), str(item["args"].get("text") or "").strip()
            if not number or not text:
                raise ToolError("It needs number and text.")
            if len(text) > MOST_SMS_CHARS:
                raise ToolError(f"That's too long (at most {MOST_SMS_CHARS} characters).")
            item["sent"] = f"Texted {number} \"{text}\""
            item["words"] = f"text {number}: \"{text}\""
            question = f"{where}, I'll text {number}: \"{text}\" ({every}). OK?"
        details = f"place_action {json.dumps({'place': place['name'], 'on': event, 'tool': item['tool'], 'args': item['args']}, ensure_ascii=False)}"
        if ask is None or not await ask(question, details):
            return None
        return item

    async def cancel(self, args):
        wanted = str(args.get("id") or "").strip()
        if not self.drop(wanted):
            raise ToolError(f"There's nothing with the id {wanted!r}: places_list has their ids.")
        return "Cancelled."

    # For places.py

    def happening(self, place_id):
        return [{"id": item["id"], "kind": "message" if item["tool"] in MESSAGES else "action", "on": item["place_on"],
                 "every": item["every"], "text": item["words"]} for item in self.items if item["place"] == place_id]

    def drop(self, id):
        """Stop one, by its id. Whether there was one."""
        item = next((item for item in self.items if item["id"] == id), None)
        if item is None:
            return False
        self.items.remove(item)
        self._save()
        self.places.changed()
        log.write("place", event="action_cancelled", id=id)
        return True

    def place_gone(self, place_id):
        self.items = [item for item in self.items if item["place"] != place_id]
        self._save()

    def place_event(self, place, event, at, late):
        """He got to a place or left it. One that's late (the phone was offline) stays for next time; what happens at a
        place happens at most once an hour, so walking in and out doesn't repeat it."""
        for item in list(self.items):
            if item["place"] != place["id"] or item["place_on"] != event:
                continue
            if late:
                log.write("place", event="action_late", id=item["id"], place=place["name"])
                continue
            if at - item.get("fired", 0) < AGAIN_MINUTES * 60 * 1000:
                continue
            if item["every"]:
                item["fired"] = at
            else:
                self.items.remove(item)
            self._save()
            self.places.changed()
            task = asyncio.create_task(self._do(item, place, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _do(self, item, place, event):
        moment = f"you {'got to' if event == 'arrived' else 'left'} {place['name']}"
        try:
            if "call" in item:
                if self.sending is None:
                    raise ToolError("Natro can't send messages here.")
                await asyncio.wait_for(self.sending.deliver(item["call"]), RUN_SECONDS)
                said = item["sent"]
            else:
                tool = next((tool for tool in self.offered() if tool.name == item["tool"]), None)
                if tool is None:
                    raise ToolError(f"{item['tool']} isn't there any more.")
                result = await asyncio.wait_for(tool.run(item["args"]), RUN_SECONDS)
                said = item.get("sent") or first_line(result_text(result)).rstrip(".")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # his to hear of, and no one else's to stop
            why = str(e).split(". ")[0].rstrip(".") or type(e).__name__
            log.write("place", event="action_failed", id=item["id"], tool=item["tool"], error=why[:200])
            self.tell(f"I couldn't {item['words']}, when {moment}: {why}.")
            return
        log.write("place", event="action_done", id=item["id"], tool=item["tool"])
        self.tell(f"{said}: {moment}.")

    # On disk

    def _load(self):
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in items if isinstance(item, dict)
                and all(key in item for key in ("id", "place", "place_on", "tool", "args", "words"))]

    def _save(self):
        partial = self.path.with_suffix(".part")
        partial.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(self.path)
