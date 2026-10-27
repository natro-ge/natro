"""The most common short commands, run without a model: ~0.3 s instead of 2.5-7 s.

Only exact, unambiguous phrasings, after dropping "OK Natro", "please" and the
like:
- music on Spotify ("pause the music", "next song", "volume up"). If Spotify
  can't do it (nothing is playing, say), the request goes to a model as usual,
  which may know better (the phone's media, for one). Bare words like "next" or
  "back" count only outside a conversation, where they can't mean "the next
  email".
- the PC's everyday commands ("lock the PC", "dark mode on", "open Downloads",
  "open Bluetooth settings"), as fixed PowerShell lines for pc_run_command
  (each one on its harmless list). Said on the PC, or naming it ("... on my
  PC"): on the phone, "dark mode on" may mean the phone.
"""
import re

MUSIC = r"(the |my )?(music|song|track|spotify|playback)"
SONG = r"(song|track)"

# (pattern, tool, arguments, bare): bare patterns count only outside a conversation.
COMMANDS = [
    (rf"pause( it| this| that| {MUSIC})?|stop {MUSIC}", "spotify_control", {"action": "pause"}, False),
    (rf"(resume|unpause|continue)( {MUSIC})?|(resume|continue) playing", "spotify_control", {"action": "resume"},
     False),
    (rf"(next|skip( to the next)?) {SONG}|skip( this| the)? {SONG}|play the next {SONG}|skip it",
     "spotify_control", {"action": "next"}, False),
    (rf"(previous|last) {SONG}|play the previous {SONG}|go back( to the previous {SONG}| a {SONG})",
     "spotify_control", {"action": "previous"}, False),
    (r"(turn )?(the )?volume up|turn (it|the music|the volume) up( a bit)?|(a bit )?louder|raise the volume",
     "spotify_control", {"action": "volume", "value": "up"}, False),
    (r"(turn )?(the )?volume down|turn (it|the music|the volume) down( a bit)?|(a bit )?(quieter|softer)|"
     r"lower the volume", "spotify_control", {"action": "volume", "value": "down"}, False),
    (r"next|skip", "spotify_control", {"action": "next"}, True),
    (r"previous|back|go back", "spotify_control", {"action": "previous"}, True),
]
THEMES = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _theme(light):
    return "; ".join(f"Set-ItemProperty -Path {THEMES} -Name {name} -Value {int(light)}"
                     for name in ("AppsUseLightTheme", "SystemUsesLightTheme"))


FOLDERS = {"downloads": "shell:Downloads", "documents": "shell:Personal", "desktop": "shell:Desktop",
           "pictures": "'shell:My Pictures'", "music": "'shell:My Music'", "videos": "'shell:My Video'"}
# Windows Settings pages, by what he calls them (words as words() leaves them: "wi-fi" is "wi fi").
SETTINGS = {"bluetooth": "bluetooth", "wi fi": "network-wifi", "wifi": "network-wifi", "network": "network",
            "display": "display", "sound": "sound", "notifications": "notifications", "apps": "appsfeatures",
            "default apps": "defaultapps", "battery": "batterysaver", "power": "powersleep",
            "storage": "storagesense", "windows update": "windowsupdate", "update": "windowsupdate",
            "background": "personalization-background", "colors": "colors", "mouse": "mousetouchpad",
            "keyboard": "typing", "printers": "printers", "privacy": "privacy", "date and time": "dateandtime"}
TURN_ON = r"(turn on |switch to |switch on |enable |use |go to )?"
# (pattern, PowerShell line for pc_run_command).
PC_COMMANDS = [
    (r"lock (the |my )?(pc|computer|laptop|screen)", "rundll32.exe user32.dll,LockWorkStation"),
    (rf"{TURN_ON}dark mode( on)?|turn dark mode on|(turn off |disable )light mode|light mode off|"
     r"turn light mode off", _theme(light=False)),
    (rf"{TURN_ON}light mode( on)?|turn light mode on|(turn off |disable )dark mode|dark mode off|"
     r"turn dark mode off", _theme(light=True)),
    *((rf"open (my |the )?{folder}( folder)?", f"Start-Process {target}") for folder, target in FOLDERS.items()),
    (r"open (the )?settings|open (the )?settings app", "Start-Process ms-settings:"),
    *((rf"open (the )?{page} settings|open (the )?settings for {page}", f"Start-Process ms-settings:{uri}")
      for page, uri in SETTINGS.items()),
]
ON_THE_PC = r"( on (the |my )?(pc|computer|laptop))?"
NAMES_THE_PC = re.compile(r"\b(pc|computer|laptop)\b")

LEADING = re.compile(r"^((ok|okay|hey) )?natro |^(ok|okay) |^(please|can you|could you|would you) ")
TRAILING = re.compile(r" (please|now|natro|for me)$")


def words(text):
    """Lowercase words without punctuation or polite padding: "Okay, Natro. Pause it, please." -> "pause it"."""
    text = " ".join(re.findall(r"[a-z0-9']+", text.lower()))
    for _ in range(3):
        text = TRAILING.sub("", LEADING.sub("", text))
    return text


def command(text, in_conversation, from_pc=False):
    """(tool name, arguments) for a quick command, or None. from_pc: the request was made on the PC."""
    said = words(text)
    for pattern, tool, args, bare in COMMANDS:
        if re.fullmatch(pattern, said) and not (bare and in_conversation):
            return tool, dict(args)
    for pattern, line in PC_COMMANDS:
        if re.fullmatch(f"(?:{pattern}){ON_THE_PC}", said) and (from_pc or NAMES_THE_PC.search(said)):
            return "pc_run_command", {"command": line}
    return None


# A word or two and a comma before the request: "Okay, Natro, ...", "Claude, open it." (the app, as translated).
VOCATIVE = re.compile(r"^(?:\W*(?:(?:ok|okay|hey) )?[\w']+\s*,)+")
PREFIX = re.compile(r"^(?:(?:ok|okay|hey)\W+)?(?:natro\W+)?(?:(?:please|can you|could you|would you)\W+)?")
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
# A request kept for a device starts with a note in brackets ("[He asked for this on his other device ...]").
NOTE = re.compile(r"^\[[^\]]*\]")
# Where his own words start, the ones to note, add, remind him of or say: after a colon (not a time's), after
# "remind me", "make a note", "add natro feature" and the like, or after "say ...,". They may be long, with "and"s
# and commas of their own (2026-10-26: 8 of 11 requests that took a second call only to word a note, task or
# reminder had them).
CONTENT = re.compile(r"(?<!\d):(?!\d)|^(?:remind me|remember|make a note|take a note|note down|write down|"
                     r"add (?:[\w-]+ ){0,2}?(?:notes?|tasks?|to-?dos?|features?))(?: (?:that|to|about|of))?\b|"
                     r"^say\b[^,]*,")
# Another thing to do, even inside his own words.
VERBS = (r"open|close|play|pause|stop|start|send|text|message|call|turn|set|show|tell|search|find|delete|remove|"
         r"add|remind|move|go|take|make|check|read|write|save|copy|put|run|book|order")
ANOTHER = re.compile(rf"\b(then|also|afterwards|after that)\b|(\band|,)( please)? ({VERBS})\b")


def simple(text):
    """A request with one thing to do: short, with no "and", "then" or commas joining several. His own words to note,
    add, remind him of or say (CONTENT, or quoted) only mustn't hold another thing to do."""
    request = NOTE.sub("", QUOTED.sub(" quote ", text.lower().strip())).strip()
    request = PREFIX.sub("", VOCATIVE.sub("", request).strip())
    found = CONTENT.search(request)
    command, content = (request[:found.end()].rstrip(",:"), request[found.end():]) if found else (request, "")
    said = words(command)
    return len(said.split()) <= 12 and not re.search(r"\b(and|then|also|after|before|plus|but)\b", said) \
        and not re.search(r"[,;]", command) and not ANOTHER.search(content)
