"""What Natro is doing right now, in a few words, for the device that asked to show ("Opening Spotify").

describe(name, args) -> {"text", "icon", "app"?}:
  text  a very short phrase, at most a few words.
  icon  the kind of step, for a small picture: app, music, calendar, tasks, mail, drive, docs, web,
        memory, notes, files, link, alarm, timer, phone, message, contacts, notifications, flashlight,
        volume, screen, or work (anything else).
  app   the app it's about, when there is one ("Spotify", "Chrome"), so a device can show that app's
        own icon if it has the app.

Device tools arrive named after their device ("pc_open_app", "phone_set_alarm").
"""
import re

# Longer details (a song, a search) are cut to this many characters.
DETAIL = 22


def short(value, length=DETAIL):
    value = " ".join(str(value or "").split())
    return value if len(value) <= length else value[:length - 1].rstrip() + "…"


def describe(name, args):
    args = args or {}
    device, _, tool = name.partition("_") if name.startswith(("pc_", "phone_")) else ("", "", name)
    for describe_step in (_device_step if device else None, _natro_step):
        if describe_step and (step := describe_step(tool, args)):
            text, icon, *app = step
            return {"text": text, "icon": icon, **({"app": app[0]} if app and app[0] else {})}
    words = tool.replace("_", " ").strip().capitalize() or "Working"
    return {"text": short(words), "icon": "work"}


def file_name(path):
    """The last part of a path, a PC's or the phone's (the server may be on either kind of system)."""
    return re.split(r"[/\\]", str(path or ""))[-1]


def _device_step(tool, args):
    app = short(args.get("app"), 18)
    file = short(file_name(args.get("path")))
    return {
        "open_app": (f"Opening {app}", "app", app),
        "close_app": (f"Closing {app}", "app", app),
        "force_close_app": (f"Force-closing {app}", "app", app),
        "list_open_apps": ("Checking open apps", "app"),
        "search_files": ("Searching files", "files"),
        "search_file_contents": ("Searching files", "files"),
        "read_file": (f"Reading {file}" if file else "Reading a file", "files"),
        "open_file": (f"Opening {file}" if file else "Opening a file", "files"),
        "open_link": ("Opening a link", "link"),
        "run_command": _command(args.get("command")),
        "run_command_risky": _command(args.get("command")),
        "find": ("Looking at the screen", "screen"),
        "act": (_act(args.get("actions")), "screen"),
        "act_risky": (_act(args.get("actions")), "screen"),
        "switch_window": (f"Switching to {short(args.get('name'), 18)}", "app", short(args.get("name"), 18)),
        "wait_for": ("Waiting", "screen"),
        "screenshot": ("Taking a screenshot", "screen"),
        "browser_open": (f"Opening {short(_domain(args.get('url')) or args.get('url'), 18)}", "web"),
        "browser_page": ("Reading the page", "web"),
        "browser_act": (_browser_act(args.get("actions")), "web"),
        "browser_act_risky": (_browser_act(args.get("actions")), "web"),
        "browser_tabs": ("Switching tabs" if args.get("action") == "select" else "Checking tabs", "web"),
        "set_alarm": (f"Alarm for {args['time']}" if args.get("time") else "Setting an alarm", "alarm"),
        "set_timer": (_timer(args.get("seconds")), "timer"),
        "next_alarm": ("Checking alarms", "alarm"),
        "media": (_media(args.get("action")), "music"),
        "flashlight": ("Flashlight on" if args.get("on") else "Flashlight off", "flashlight"),
        "volume": ("Setting the volume", "volume"),
        "contacts": (f"Finding {short(args.get('name'))}" if args.get("name") else "Checking contacts", "contacts"),
        "call": ("Calling", "phone"),
        "send_sms": ("Sending a text", "message"),
        "read_sms": ("Reading texts", "message"),
        "notifications": ("Reading notifications", "notifications"),
        "screen": ("Looking at the screen", "screen"),
        "tap": ("Tapping", "screen"),
        "tap_risky": ("Tapping", "screen"),
        "type_text": ("Typing", "screen"),
        "scroll": ("Scrolling", "screen"),
        "press": (f"Pressing {args.get('key') or 'a key'}", "screen"),
    }.get(tool)


def _natro_step(tool, args):
    query = short(args.get("query"))
    words = short(args.get("words"), 14)
    domain = _domain(args.get("url"))
    title = short(args.get("title"))
    if tool == "spotify_play":
        if args.get("kind") == "liked_songs":
            what = "Liked Songs"
        else:
            what = query or ("music" if not args.get("uri") else "")
        if not what:
            return "Playing on Spotify", "music", "Spotify"
        return f"{'Queueing' if args.get('queue') else 'Playing'} {what}", "music", "Spotify"
    if tool == "spotify_control":
        return _spotify_control(args.get("action"), args.get("value")), "music", "Spotify"
    if tool == "spotify_add_to_playlist":
        playlist = str(args.get("playlist") or "")
        where = "a playlist" if re.match(r"spotify:|https?:", playlist) else short(playlist)  # a URI says nothing
        return f"Adding to {where}", "music", "Spotify"
    return {
        "spotify_now_playing": ("Checking what's on", "music", "Spotify"),
        "spotify_like": ("Liking it", "music", "Spotify"),
        "spotify_create_playlist": (f"Creating {short(args.get('name'))}" if args.get("name") else "Creating a playlist",
                                    "music", "Spotify"),
        "spotify_library": ("Checking playlists", "music", "Spotify"),
        "calendar_events": ("Checking calendar", "calendar"),
        "calendar_create_event": (f"Adding {title}" if title else "Adding an event", "calendar"),
        "calendar_update_event": ("Updating an event", "calendar"),
        "calendar_delete_event": ("Deleting an event", "calendar"),
        "tasks_list": ("Checking tasks", "tasks"),
        "tasks_add": (f"Adding {title}" if title else "Adding a task", "tasks"),
        "tasks_complete": ("Completing a task", "tasks"),
        "tasks_delete": ("Deleting a task", "tasks"),
        "gmail_search": ("Searching email", "mail"),
        "gmail_read": ("Reading email", "mail"),
        "gmail_send": ("Sending email", "mail"),
        "drive_search": ("Searching Drive", "drive"),
        "drive_read": ("Reading a file", "drive"),
        "docs_create": (f"Creating {title}" if title else "Creating a doc", "docs"),
        "docs_append": ("Writing a doc", "docs"),
        "docs_replace": ("Editing a doc", "docs"),
        "web_search": (f"Searching {query}" if query else "Searching the web", "web"),
        "remember": ("Remembering", "memory"),
        "forget": ("Forgetting", "memory"),
        "notes_list": ("Checking notes", "notes"),
        "notes_read": ("Reading notes", "notes"),
        "notes_search": ("Searching notes", "notes"),
        "notes_write": ("Writing a note", "notes"),
        "notes_edit": ("Editing a note", "notes"),
        "notes_delete": ("Deleting a note", "notes"),
        "copy_to_device": (f"Sending {short(file_name(args.get('path')))}" if args.get("path") else "Sending a file",
                           "files"),
        "queue_for_device": (f"Keeping it for the {'PC' if args.get('device') == 'pc' else 'phone'}", "work"),
        "remind": (_reminder(args), "alarm"),
        "reminders_list": ("Checking reminders", "alarm"),
        "reminders_cancel": ("Cancelling a reminder", "alarm"),
        "weather": (f"Weather in {short(args.get('place'), 14)}" if args.get("place") else "Checking the weather",
                    "web"),
        "briefing": ("Getting your day", "calendar"),
        "start_job": ("Starting a job", "work"),
        "jobs_list": ("Checking jobs", "work"),
        "jobs_cancel": ("Cancelling a job", "work"),
        "convert_file": (f"Converting to {short(args.get('to'), 8).upper()}" if args.get("to") else "Converting a file",
                         "files"),
        "watch": ("Watching for it", "notifications"),
        "travel_time": (f"Route to {short(args.get('destination'), 16)}" if args.get("destination") else
                        "Checking the route", "web"),
        "travel_routes": (f"Ways to {short(args.get('destination'), 16)}" if args.get("destination") else
                          "Comparing routes", "web"),
        "travel_plan": ("Planning the trip", "web"),
        "history_search": (f"Looking back for {words}" if words else "Looking back", "memory"),
        "web_page": (f"Reading {short(domain, 18)}" if domain else "Reading a page", "web"),
        "watchers_list": ("Checking watchers", "notifications"),
        "watchers_cancel": ("Stopping a watcher", "notifications"),
    }.get(tool)


def _domain(url):
    """example.com, from a whole address."""
    found = re.match(r"\w+://(?:www\.)?([^/:?#]+)", str(url or ""))
    return found.group(1) if found else ""


def _reminder(args):
    if args.get("when_online") in ("pc", "phone"):
        return f"Reminder for the {'PC' if args['when_online'] == 'pc' else 'phone'}"
    minutes = args.get("in_minutes")
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes > 0:
        return f"Reminder in {round(minutes)} min" if minutes < 120 else f"Reminder in {round(minutes / 60)} h"
    when = re.search(r"T(\d\d:\d\d)", str(args.get("at") or ""))
    return f"Reminder for {when.group(1)}" if when else "Setting a reminder"


def _browser_act(actions):
    first = (actions or [{}])[0] if isinstance(actions, list) else {}
    do = first.get("do") if isinstance(first, dict) else None
    return {"type": "Typing", "key": "Pressing a key", "select": "Choosing", "back": "Going back"}.get(do, "Clicking")


def _command(command):
    command = str(command or "").lower()
    if "ms-settings:" in command:
        return "Opening Settings", "app", "Settings"
    if "lockworkstation" in command:
        return "Locking the PC", "work"
    return "Running a command", "work"


def _act(actions):
    first = (actions or [{}])[0] if isinstance(actions, list) else {}
    do = first.get("do") if isinstance(first, dict) else None
    return {"type": "Typing", "key": f"Pressing {first.get('text') or 'a key'}", "scroll": "Scrolling"}.get(
        do, "Clicking")


def _media(action):
    return {"play": "Playing music", "pause": "Pausing music", "next": "Next song",
            "previous": "Previous song"}.get(action, "Controlling music")


def _spotify_control(action, value):
    return {"pause": "Pausing music", "resume": "Resuming music", "next": "Next song",
            "previous": "Previous song", "volume": "Setting the volume",
            "shuffle": f"Shuffle {value}" if value in ("on", "off") else "Shuffling",
            "repeat": "Repeat off" if value == "off" else "Repeating", "seek": "Skipping ahead"}.get(action, "Spotify")


def _timer(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "Setting a timer"
    if seconds < 60:
        return f"Timer for {seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"Timer for {minutes} min" + (f" {rest} s" if rest else "")
