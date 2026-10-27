"""Commands on the PC: PowerShell lines that answer questions about it or make small changes.

run_command runs a line right away when it is harmless: it only reads, opens a web page, settings page,
folder or document, or makes one of a few small changes that are easy to undo (dark mode, brightness, the
clipboard, locking the PC). Anything else is refused there and goes through run_command_risky, which the
brain asks him about first, like the phone's tap and tap_risky.

Whether a line is harmless is decided from what PowerShell's own parser says it would run (commands.ps1):
every command, through pipes, ";" and nested blocks, and every method call, redirection and assignment,
checked against the lists below. Anything not on them asks.

One PowerShell is kept open (warmed up when the helper starts), so a command doesn't wait for PowerShell to
start. Harmless lines share one runspace; risky ones each get a new one (see commands.ps1).
"""
import json
import ntpath
import os
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path

from natro_pc.files import PROGRAMS as RUNS_SOMETHING
from natro_pc.tools import PCTool, ToolError

SCRIPT = Path(__file__).with_name("commands.ps1")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
START_SECONDS = 30  # the first answer waits for PowerShell to start
CHECK_SECONDS = 15
RUN_SECONDS = 30  # a command still running after this is stopped
OUTPUT_CHARS = 6000

# Cmdlets whose verb only reads (Get-Process, Test-Path, ConvertTo-Json...).
READ_VERBS = {"get", "test", "measure", "select", "sort", "group", "compare", "find", "resolve", "convertto",
              "convertfrom", "join", "split"}
# Other cmdlets that only read, show, wait or move around.
HARMLESS_COMMANDS = {
    "where-object", "format-table", "format-list", "format-wide", "format-custom", "format-hex", "out-string",
    "out-null", "out-host", "out-default", "write-output", "write-host", "write-verbose", "write-warning",
    "write-information", "write-debug", "write-error", "convert-path", "start-sleep", "set-location",
    "push-location", "pop-location", "set-clipboard", "clear-host", "new-timespan", "new-guid", "import-csv",
    "import-clixml", "wait-process",
}
# Methods that only compute or read; any other method call asks (.Delete(), .Kill(), [IO.File]::WriteAllText()).
SAFE_METHODS = {
    "tostring", "toupper", "tolower", "toupperinvariant", "tolowerinvariant", "trim", "trimstart", "trimend",
    "split", "substring", "replace", "contains", "startswith", "endswith", "indexof", "lastindexof", "padleft",
    "padright", "join", "format", "equals", "compareto", "gettype", "gethashcode", "round", "floor", "ceiling",
    "abs", "max", "min", "pow", "sqrt", "truncate", "log", "log10", "exp", "sign", "parse", "tryparse",
    "parseexact", "isnullorempty", "isnullorwhitespace", "getfolderpath", "getenvironmentvariable",
    "getlogicaldrives", "adddays", "addhours", "addminutes", "addseconds", "addmilliseconds", "addmonths",
    "addyears", "toshortdatestring", "toshorttimestring", "tolongdatestring", "tolongtimestring", "tolocaltime",
    "touniversaltime", "subtract", "toint32", "toint64", "todouble", "todecimal", "toboolean", "todatetime",
    "tochararray", "getbytes", "getstring", "frombase64string", "tobase64string", "getvalue", "getvaluenames",
    "getsubkeynames", "readalltext", "readalllines", "exists", "getfiles", "getdirectories", "enumeratefiles",
    "enumeratedirectories", "getfilename", "getfilenamewithoutextension", "getextension", "getdirectoryname",
    "combine", "getfullpath", "getpathroot", "where", "gethostname", "gethostentry", "gethostaddresses",
    "wmisetbrightness",
}
# Links that only show something: web pages, Settings pages, mail, shell folders (shell:Downloads), Spotify.
SAFE_SCHEMES = {"http", "https", "ms-settings", "mailto", "shell", "spotify"}
# Dark mode: these two values of this key, 0 or 1.
THEME_KEY = r"hkcu:\software\microsoft\windows\currentversion\themes\personalize"
THEME_VALUES = {"appsuselighttheme", "systemuseslighttheme"}
NETSH_CHANGES = {"set", "add", "delete", "reset", "connect", "disconnect", "import", "export", "dump", "install",
                 "uninstall", "start", "stop", "trace"}


def _lower(args):
    return [arg.lower() for arg in args if arg is not None] if None not in args else None


def _only(*allowed, at_least_one=False):
    def rule(args):
        lowered = _lower(args)
        return lowered is not None and set(lowered) <= set(allowed) and (lowered or not at_least_one)
    return rule


def _any(args):
    return True


def _netsh(args):
    lowered = _lower(args)
    return lowered is not None and "show" in lowered and not set(lowered) & NETSH_CHANGES


def _first(*allowed):
    def rule(args):
        lowered = _lower(args)
        return bool(lowered) and lowered[0] in allowed
    return rule


# Windows' programs that only report, with the arguments they may have.
PROGRAM_RULES = {
    "ipconfig": _only("/all", "/displaydns", "/flushdns", "/?"),
    "arp": _only("-a", "/a", "-g", at_least_one=True),
    "route": _first("print"),
    "netsh": _netsh,
    "winget": _first("list", "search", "show", "--version"),
    "powercfg": _only("/list", "/l", "/getactivescheme", "/a", "/availablesleepstates", at_least_one=True),
    "rundll32": lambda args: _lower(args) == ["user32.dll,lockworkstation"],  # locking the PC
    **{name: _any for name in ("ping", "tracert", "pathping", "nslookup", "whoami", "hostname", "systeminfo",
                               "getmac", "driverquery", "tasklist", "netstat", "tree", "where", "findstr", "find",
                               "fc")},
}

_ENV = re.compile(r"\$(?:\{env:(\w+)\}|env:(\w+)|\{home\}|home(?!\w))", re.IGNORECASE)


def expand(text):
    """An argument as it will run: $env:NAME, ${env:NAME} and $HOME filled in."""
    def fill(match):
        name = match.group(1) or match.group(2)
        return os.environ.get(name, "") if name else str(Path.home())
    return _ENV.sub(fill, text)


def opens_harmlessly(target):
    """Whether opening this only shows something: a web or Settings link, a folder, a document."""
    if target is None:
        return False
    target = expand(target).strip()
    if not target or target.startswith(("\\\\", "//")):
        return False
    scheme = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]+):", target)  # two letters or more: C: is a drive
    if scheme:
        return scheme.group(1).lower() in SAFE_SCHEMES
    path = target if os.path.isabs(target) or ntpath.isabs(target) else os.path.join(Path.home(), target)
    if os.path.isdir(path):
        return True
    suffix = ntpath.splitext(target)[1].lower()
    return bool(suffix) and suffix not in RUNS_SOMETHING


def _value(parameters, *names):
    for name in names:
        if name in parameters:
            return parameters[name].get("value")
    return None


def _registry_path(path):
    path = (path or "").lower().rstrip("\\")
    for long_name in ("microsoft.powershell.core\\registry::hkey_current_user", "registry::hkey_current_user"):
        if path.startswith(long_name):
            path = "hkcu:" + path[len(long_name):]
    return path


def _theme_change(parameters):
    if not set(parameters) <= {"Path", "LiteralPath", "Name", "Value", "Type", "Force"}:
        return False
    return (_registry_path(_value(parameters, "Path", "LiteralPath")) == THEME_KEY
            and (_value(parameters, "Name") or "").lower() in THEME_VALUES
            and _value(parameters, "Value") in ("0", "1"))


def _foreach_blocks_only(parameters):
    # ForEach-Object Delete calls .Delete() on everything it gets; only script blocks are checked like the rest.
    return all(info.get("block") if name in ("Process", "Begin", "End", "RemainingScripts") else
               name == "InputObject" for name, info in parameters.items())


def _command_problem(command):
    """Why one command in a line isn't harmless, or None."""
    name = command.get("name")
    if not name:
        return "it runs a command that is only known when it runs"
    kind = command.get("type")
    resolved = command.get("command") or name
    if kind == "ExternalScript":
        return f"it runs the script {name}"
    looks_like_program = "-" not in resolved or resolved.lower().endswith(".exe")
    if kind == "Application" or (kind is None and looks_like_program):
        return _program_problem(name, command)
    if kind not in (None, "Cmdlet", "Function", "Filter"):
        return f"{resolved} isn't on the harmless list"
    lowered = resolved.lower()
    parameters = command.get("parameters") or {}
    if lowered in ("start-process", "invoke-item"):
        target = _value(parameters, "FilePath", "Path", "LiteralPath", "0")
        verb = (_value(parameters, "Verb") or "open").lower()
        if verb != "open" or "Credential" in parameters:
            return "it runs something as another user or as admin"
        if not opens_harmlessly(target):
            return f"{resolved} would start a program or open something that isn't a page, folder or document"
        return None
    if lowered in ("set-itemproperty", "new-itemproperty"):
        return None if _theme_change(parameters) else f"{resolved} changes something other than dark mode"
    if lowered == "foreach-object":
        return None if _foreach_blocks_only(parameters) else "ForEach-Object calls a method by name"
    if lowered in HARMLESS_COMMANDS or lowered.split("-")[0] in READ_VERBS:
        return None
    return f"{resolved} isn't on the harmless list"


def _program_problem(name, command):
    if "\\" in name or "/" in name:
        return f"it runs {name} by its path"
    path = command.get("path")
    if path and os.name == "nt" and not _windows_own(path):
        return f"{name} here is {path}, not Windows' own"
    program = ntpath.splitext(name.lower())[0] if name.lower().endswith(".exe") else name.lower()
    arguments = command.get("arguments") or []
    if program == "explorer":
        return None if not arguments or (len(arguments) == 1 and opens_harmlessly(arguments[0])) else \
            "explorer would open something that isn't a page, folder or document"
    rule = PROGRAM_RULES.get(program)
    if rule is None or not rule(arguments):
        shown = " ".join(arg if arg is not None else "…" for arg in [name, *arguments])
        return f"{shown} isn't on the harmless list"
    return None


def _windows_own(path):
    root = os.environ.get("SystemRoot", r"C:\Windows").lower().rstrip("\\")
    path = path.lower()
    return path.startswith(root + "\\") or "\\microsoft\\windowsapps\\" in path


def harmless(analysis):
    """None when the line only reads, opens or makes a listed small change; otherwise why not."""
    if analysis.get("errors"):
        return "PowerShell can't read it: " + "; ".join(analysis["errors"])
    for location in analysis.get("redirections") or []:
        if location.strip().lower() != "$null":
            return f"it writes to {location}"
    for target in analysis.get("assignments") or []:
        if target == "*" or ":" in target or target.lower().startswith("ps") or \
                target.lower() in ("executioncontext", "host"):
            return f"it changes ${target}" if target != "*" else "it changes something other than a variable"
    if analysis.get("drives"):
        return f"it uses the {analysis['drives'][0]}: drive through a variable"
    for value in analysis.get("values") or []:
        if (value or "").startswith(("\\\\", "//")):
            return f"it reaches another computer ({value})"
    for method in analysis.get("methods") or []:
        if method.lower() not in SAFE_METHODS:
            return "it calls a method chosen when it runs" if method == "*" else f"it calls .{method}()"
    for command in analysis.get("commands") or []:
        if problem := _command_problem(command):
            return problem
    return None


def executable():
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    found = shutil.which("pwsh")
    if not found:
        raise ToolError("PowerShell isn't installed here.")
    return found


class PowerShell:
    """commands.ps1 in one PowerShell, kept open; one request at a time. It restarts if it stops answering."""

    def __init__(self, program=None):
        self._program = program
        self._process = None
        self._answers = None
        self._lock = threading.Lock()

    def _start(self):
        self._process = subprocess.Popen(
            [self._program or executable(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            creationflags=NO_WINDOW if os.name == "nt" else 0)
        self._answers = queue.Queue()
        threading.Thread(target=self._read, args=(self._process.stdout, self._answers), daemon=True).start()

    @staticmethod
    def _read(stream, answers):
        for line in stream:
            answers.put(line)
        answers.put(None)  # it ended

    def ask(self, request, seconds):
        with self._lock:
            first = self._process is None or self._process.poll() is not None
            if first:
                self._start()
            try:
                self._process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                self._process.stdin.flush()
                line = self._answers.get(timeout=seconds + (START_SECONDS if first else 0))
            except (OSError, queue.Empty):
                line = None
            if line is None:
                self.stop()
                raise ToolError("PowerShell stopped answering (it starts again for the next command).")
        answer = json.loads(line.decode("utf-8"))
        if "failed" in answer:
            raise ToolError(f"PowerShell couldn't do that: {answer['failed']}")
        return answer

    def analyze(self, command):
        return self.ask({"op": "check", "command": command}, CHECK_SECONDS)

    def run(self, command, fresh=False, seconds=RUN_SECONDS):
        return self.ask({"op": "run", "command": command, "seconds": seconds, "fresh": fresh}, seconds + 10)

    def stop(self):
        if self._process is not None:
            self._process.kill()
            self._process = None

    def warm_up(self):
        """Start PowerShell ahead of the first command (it takes a second or two)."""
        try:
            self.analyze("Get-Date")
        except ToolError:
            pass


def shown(answer, seconds=RUN_SECONDS):
    """A command's answer as text for the brain; a command that only failed raises ToolError."""
    if answer.get("timed_out"):
        raise ToolError(f"The command was still running after {seconds} seconds and was stopped.")
    output = "\n".join(line.rstrip() for line in (answer.get("output") or "").splitlines()).strip("\n")
    errors = [error.strip() for error in answer.get("errors") or [] if error and error.strip()]
    if errors and not output.strip():
        raise ToolError("The command failed: " + "\n".join(errors))
    text = output if output.strip() else "Done."
    if errors:
        text += "\nErrors:\n" + "\n".join(errors)
    if len(text) > OUTPUT_CHARS:
        text = (f"{text[:OUTPUT_CHARS]}\n[cut here: {len(text)} characters in all. Ask for less, for example "
                "with Select-Object -First 10.]")
    return text


class Commands:
    def __init__(self, shell=None):
        self.shell = shell or PowerShell()

    def problem(self, command):
        """Why the line isn't harmless, or None."""
        return harmless(self.shell.analyze(command))

    def run(self, command):
        command = (command or "").strip()
        if not command:
            raise ToolError("Say which command to run.")
        if problem := self.problem(command):
            raise ToolError(f"Not run, because {problem}. If it's what he asked for, use run_command_risky: "
                            "it asks him first.")
        return shown(self.shell.run(command))

    def run_risky(self, command):
        command = (command or "").strip()
        if not command:
            raise ToolError("Say which command to run.")
        return shown(self.shell.run(command, fresh=True))


def tools(commands=None):
    commands = commands or Commands()
    command = {"type": "string",
               "description": "PowerShell (Windows PowerShell 5.1), on one line; it starts in his home folder."}
    return [
        PCTool("run_command", "Run a PowerShell command on the PC and get its output: questions about the PC (IP "
               "address, disk space, battery, Wi-Fi, processes, a file's details) and small changes. It runs only "
               "when harmless: it only reads (Get-*, Test-*, ipconfig, netsh ... show), opens a page, a Settings "
               "page (Start-Process ms-settings:bluetooth), a folder or a document, or it is dark mode (Set-"
               "ItemProperty on HKCU:\\...\\Themes\\Personalize AppsUseLightTheme or SystemUsesLightTheme, 0 or "
               "1), brightness (WmiSetBrightness), Set-Clipboard, or locking the PC (rundll32.exe user32.dll,"
               "LockWorkStation). Anything else is refused: then use run_command_risky.", commands.run,
               {"command": command}, ["command"], personal=True),
        PCTool("run_command_risky", "Run a PowerShell command that changes, deletes, installs or writes "
               "something, or one that run_command refused.", commands.run_risky, {"command": command},
               ["command"], personal=True, confirm=True),
    ]
