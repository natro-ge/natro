//! Commands on the PC: PowerShell lines that answer questions about it or make small changes (the
//! tools run_command and run_command_risky).
//!
//! run_command runs a line right away when it is harmless: it only reads, opens a web page,
//! settings page, folder or document, or makes one of a few small changes that are easy to undo
//! (dark mode, brightness, the clipboard, locking the PC). Anything else is refused there and goes
//! through run_command_risky, which the brain asks him about first, like the phone's tap and
//! tap_risky.
//!
//! Whether a line is harmless is decided from what PowerShell's own parser says it would run
//! (commands.ps1): every command, through pipes, ";" and nested blocks, and every method call,
//! redirection and assignment, checked against the lists below. Anything not on them asks.
//!
//! One PowerShell is kept open (warmed up when the app starts), so a command doesn't wait for
//! PowerShell to start. Harmless lines share one runspace; risky ones each get a new one (see
//! commands.ps1).
//!
//! Ported from the Python helper's commands.py (cleanup step 5.3); commands.ps1 is the same script.

use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::Duration;

use serde_json::{Map, Value, json};

use super::files::{PROGRAMS as RUNS_SOMETHING, kind_at};
use super::{home, is_space, spec, text_arg};

/// The script PowerShell runs: what a line would run, and running it (commands.ps1).
const SCRIPT: &str = include_str!("commands.ps1");
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// The first answer waits for PowerShell to start.
const START: Duration = Duration::from_secs(30);
const CHECK: Duration = Duration::from_secs(15);
/// A command still running after this is stopped.
const RUN_SECONDS: u64 = 30;
const OUTPUT_CHARS: usize = 6000;

/// Cmdlets whose verb only reads (Get-Process, Test-Path, ConvertTo-Json...).
const READ_VERBS: &[&str] = &[
    "get", "test", "measure", "select", "sort", "group", "compare", "find", "resolve", "convertto", "convertfrom",
    "join", "split",
];
/// Other cmdlets that only read, show, wait or move around.
const HARMLESS_COMMANDS: &[&str] = &[
    "where-object", "format-table", "format-list", "format-wide", "format-custom", "format-hex", "out-string",
    "out-null", "out-host", "out-default", "write-output", "write-host", "write-verbose", "write-warning",
    "write-information", "write-debug", "write-error", "convert-path", "start-sleep", "set-location",
    "push-location", "pop-location", "set-clipboard", "clear-host", "new-timespan", "new-guid", "import-csv",
    "import-clixml", "wait-process",
];
/// Methods that only compute or read; any other method call asks (.Delete(), .Kill(), [IO.File]::WriteAllText()).
const SAFE_METHODS: &[&str] = &[
    "tostring", "toupper", "tolower", "toupperinvariant", "tolowerinvariant", "trim", "trimstart", "trimend",
    "split", "substring", "replace", "contains", "startswith", "endswith", "indexof", "lastindexof", "padleft",
    "padright", "join", "format", "equals", "compareto", "gettype", "gethashcode", "round", "floor", "ceiling", "abs",
    "max", "min", "pow", "sqrt", "truncate", "log", "log10", "exp", "sign", "parse", "tryparse", "parseexact",
    "isnullorempty", "isnullorwhitespace", "getfolderpath", "getenvironmentvariable", "getlogicaldrives", "adddays",
    "addhours", "addminutes", "addseconds", "addmilliseconds", "addmonths", "addyears", "toshortdatestring",
    "toshorttimestring", "tolongdatestring", "tolongtimestring", "tolocaltime", "touniversaltime", "subtract",
    "toint32", "toint64", "todouble", "todecimal", "toboolean", "todatetime", "tochararray", "getbytes", "getstring",
    "frombase64string", "tobase64string", "getvalue", "getvaluenames", "getsubkeynames", "readalltext",
    "readalllines", "exists", "getfiles", "getdirectories", "enumeratefiles", "enumeratedirectories", "getfilename",
    "getfilenamewithoutextension", "getextension", "getdirectoryname", "combine", "getfullpath", "getpathroot",
    "where", "gethostname", "gethostentry", "gethostaddresses", "wmisetbrightness",
];
/// Links that only show something: web pages, Settings pages, mail, shell folders (shell:Downloads), Spotify.
const SAFE_SCHEMES: &[&str] = &["http", "https", "ms-settings", "mailto", "shell", "spotify"];
/// Dark mode: these two values of this key, 0 or 1.
const THEME_KEY: &str = r"hkcu:\software\microsoft\windows\currentversion\themes\personalize";
const THEME_VALUES: &[&str] = &["appsuselighttheme", "systemuseslighttheme"];
const NETSH_CHANGES: &[&str] = &[
    "set", "add", "delete", "reset", "connect", "disconnect", "import", "export", "dump", "install", "uninstall",
    "start", "stop", "trace",
];

/// The tools as the brain sees them: in desktop/tools.json, after the files'.
pub fn specs() -> Vec<Value> {
    let command = json!({"command": {"type": "string",
        "description": "PowerShell (Windows PowerShell 5.1), on one line; it starts in his home folder."}});
    vec![
        spec(
            "run_command",
            "Run a PowerShell command on the PC and get its output: questions about the PC (IP address, disk \
             space, battery, Wi-Fi, processes, a file's details) and small changes. It runs only when harmless: it \
             only reads (Get-*, Test-*, ipconfig, netsh ... show), opens a page, a Settings page (Start-Process \
             ms-settings:bluetooth), a folder or a document, or it is dark mode (Set-ItemProperty on \
             HKCU:\\...\\Themes\\Personalize AppsUseLightTheme or SystemUsesLightTheme, 0 or 1), brightness \
             (WmiSetBrightness), Set-Clipboard, or locking the PC (rundll32.exe user32.dll,LockWorkStation). \
             Anything else is refused: then use run_command_risky.",
            &command,
            &["command"],
            false,
            false,
        ),
        spec(
            "run_command_risky",
            "Run a PowerShell command that changes, deletes, installs or writes something, or one that \
             run_command refused.",
            &command,
            &["command"],
            true,
            false,
        ),
    ]
}

/// Whether name is one of these tools.
pub fn offers(name: &str) -> bool {
    matches!(name, "run_command" | "run_command_risky")
}

pub struct Commands {
    shell: PowerShell,
}

impl Commands {
    pub fn new() -> Commands {
        Commands { shell: PowerShell::new() }
    }

    /// Start PowerShell ahead of the first command (it takes a second or two). Blocking.
    pub fn warm_up(&self) {
        let _ = self.shell.analyze("Get-Date");
    }

    /// Run one of the tools (offers). Blocking. Ok: its result for Natro; Err: why it failed, for her.
    pub fn run(&self, name: &str, args: &Value) -> Result<String, String> {
        let command = text_arg(args, "command").unwrap_or_default().trim_matches(is_space);
        if command.is_empty() {
            return Err("Say which command to run.".into());
        }
        if name == "run_command" {
            if let Some(problem) = harmless(&self.shell.analyze(command)?) {
                return Err(format!(
                    "Not run, because {problem}. If it's what he asked for, use run_command_risky: it asks him first."
                ));
            }
            return shown(&self.shell.run(command, false, RUN_SECONDS)?, RUN_SECONDS);
        }
        shown(&self.shell.run(command, true, RUN_SECONDS)?, RUN_SECONDS)
    }
}

// Whether a line is harmless

fn lower(args: &[Option<String>]) -> Option<Vec<String>> {
    args.iter().map(|arg| arg.as_ref().map(|arg| arg.to_lowercase())).collect()
}

/// Every argument one of these (and at least one, when asked).
fn only(args: &[Option<String>], allowed: &[&str], at_least_one: bool) -> bool {
    lower(args).is_some_and(|args| {
        args.iter().all(|arg| allowed.contains(&arg.as_str())) && (!args.is_empty() || !at_least_one)
    })
}

/// The first argument one of these.
fn first(args: &[Option<String>], allowed: &[&str]) -> bool {
    lower(args).is_some_and(|args| args.first().is_some_and(|arg| allowed.contains(&arg.as_str())))
}

fn netsh(args: &[Option<String>]) -> bool {
    lower(args).is_some_and(|args| {
        args.iter().any(|arg| arg == "show") && !args.iter().any(|arg| NETSH_CHANGES.contains(&arg.as_str()))
    })
}

/// Windows' programs that only report, with the arguments they may have: whether these are
/// allowed, or None for a program not on the list.
fn program_allows(program: &str, args: &[Option<String>]) -> Option<bool> {
    Some(match program {
        "ipconfig" => only(args, &["/all", "/displaydns", "/flushdns", "/?"], false),
        "arp" => only(args, &["-a", "/a", "-g"], true),
        "route" => first(args, &["print"]),
        "netsh" => netsh(args),
        "winget" => first(args, &["list", "search", "show", "--version"]),
        "powercfg" => only(args, &["/list", "/l", "/getactivescheme", "/a", "/availablesleepstates"], true),
        // Locking the PC.
        "rundll32" => lower(args) == Some(vec!["user32.dll,lockworkstation".to_string()]),
        "ping" | "tracert" | "pathping" | "nslookup" | "whoami" | "hostname" | "systeminfo" | "getmac"
        | "driverquery" | "tasklist" | "netstat" | "tree" | "where" | "findstr" | "find" | "fc" => true,
        _ => return None,
    })
}

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// An argument as it will run: $env:NAME, ${env:NAME} and $HOME filled in (in any case).
fn expand(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(at) = rest.find('$') {
        out.push_str(&rest[..at]);
        let after = &rest[at + 1..];
        match variable(after) {
            Some((value, used)) => {
                out.push_str(&value);
                rest = &after[used..];
            }
            None => {
                out.push('$');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// The variable at the start of what follows a "$", if it's one expand fills in: its value, and
/// how many bytes it took.
fn variable(after: &str) -> Option<(String, usize)> {
    let lower = after.to_ascii_lowercase();
    let name_length = |from: usize| -> usize {
        after[from..].chars().take_while(|c| is_word(*c)).map(char::len_utf8).sum()
    };
    let value = |name: &str| std::env::var(name).unwrap_or_default();
    if lower.starts_with("{env:") {
        let length = name_length(5);
        return (length > 0 && after[5 + length..].starts_with('}'))
            .then(|| (value(&after[5..5 + length]), 5 + length + 1));
    }
    if lower.starts_with("env:") {
        let length = name_length(4);
        return (length > 0).then(|| (value(&after[4..4 + length]), 4 + length));
    }
    if lower.starts_with("{home}") {
        return Some((home(), 6));
    }
    if lower.starts_with("home") && !after[4..].chars().next().is_some_and(is_word) {
        return Some((home(), 4));
    }
    None
}

/// A link's scheme ("https" in https://...): two letters or more, as C: is a drive.
fn scheme(target: &str) -> Option<&str> {
    let mut chars = target.char_indices();
    if !chars.next().is_some_and(|(_, c)| c.is_ascii_alphabetic()) {
        return None;
    }
    for (at, c) in chars {
        if c == ':' {
            return (at >= 2).then(|| &target[..at]);
        }
        if !(c.is_ascii_alphanumeric() || matches!(c, '+' | '.' | '-')) {
            return None;
        }
    }
    None
}

/// A path's extension, lowercase, as Python's ntpath.splitext finds it (".pdf"; "" for ".bashrc").
fn extension(path: &str) -> String {
    let name = &path[path.rfind(['\\', '/']).map_or(0, |at| at + 1)..];
    match name.rfind('.') {
        Some(dot) if name[..dot].chars().any(|c| c != '.') => name[dot..].to_lowercase(),
        _ => String::new(),
    }
}

/// Whether opening this only shows something: a web or Settings link, a folder, a document.
fn opens_harmlessly(target: Option<&str>) -> bool {
    let Some(target) = target else {
        return false;
    };
    let target = expand(target);
    let target = target.trim_matches(is_space);
    if target.is_empty() || target.starts_with("\\\\") || target.starts_with("//") {
        return false;
    }
    if let Some(scheme) = scheme(target) {
        return SAFE_SCHEMES.contains(&scheme.to_lowercase().as_str());
    }
    // A colon past the drive names a file's stream ("x.exe::$DATA"), which runs as the file would.
    let drive = target.len() >= 2 && target.as_bytes()[1] == b':' && target.as_bytes()[0].is_ascii_alphabetic();
    if target[if drive { 2 } else { 0 }..].contains(':') {
        return false;
    }
    let path = if Path::new(target).is_absolute() { PathBuf::from(target) } else { Path::new(&home()).join(target) };
    if kind_at(&path) == Some(false) {
        return true; // a folder
    }
    // Windows drops a name's trailing dots and spaces: "setup.exe." runs setup.exe.
    let suffix = extension(target.trim_end_matches(['.', ' ']));
    !suffix.is_empty() && !RUNS_SOMETHING.contains(&suffix.as_str())
}

/// A list in the analysis (one alone, or none, as a list too).
fn list(value: &Value) -> Vec<&Value> {
    match value {
        Value::Array(items) => items.iter().collect(),
        Value::Null => Vec::new(),
        other => vec![other],
    }
}

/// A parameter's value, when it's known before running: the first of these names that's there.
fn parameter(parameters: &Map<String, Value>, names: &[&str]) -> Option<String> {
    let name = names.iter().find(|name| parameters.contains_key(**name))?;
    parameters[*name]["value"].as_str().map(String::from)
}

fn registry_path(path: Option<String>) -> String {
    let mut path = path.unwrap_or_default().to_lowercase().trim_end_matches('\\').to_string();
    for long_name in ["microsoft.powershell.core\\registry::hkey_current_user", "registry::hkey_current_user"] {
        if let Some(rest) = path.strip_prefix(long_name) {
            path = format!("hkcu:{rest}");
        }
    }
    path
}

fn theme_change(parameters: &Map<String, Value>) -> bool {
    let allowed = ["Path", "LiteralPath", "Name", "Value", "Type", "Force"];
    parameters.keys().all(|name| allowed.contains(&name.as_str()))
        && registry_path(parameter(parameters, &["Path", "LiteralPath"])) == THEME_KEY
        && THEME_VALUES.contains(&parameter(parameters, &["Name"]).unwrap_or_default().to_lowercase().as_str())
        && matches!(parameter(parameters, &["Value"]).as_deref(), Some("0" | "1"))
}

/// ForEach-Object Delete calls .Delete() on everything it gets; only script blocks are checked like the rest.
fn foreach_blocks_only(parameters: &Map<String, Value>) -> bool {
    parameters.iter().all(|(name, info)| match name.as_str() {
        "Process" | "Begin" | "End" | "RemainingScripts" => info["block"].as_bool().unwrap_or(false),
        name => name == "InputObject",
    })
}

/// Why one command in a line isn't harmless, or None.
fn command_problem(command: &Value) -> Option<String> {
    let Some(name) = command["name"].as_str().filter(|name| !name.is_empty()) else {
        return Some("it runs a command that is only known when it runs".into());
    };
    let kind = command["type"].as_str();
    let resolved = command["command"].as_str().filter(|resolved| !resolved.is_empty()).unwrap_or(name);
    if kind == Some("ExternalScript") {
        return Some(format!("it runs the script {name}"));
    }
    let looks_like_program = !resolved.contains('-') || resolved.to_lowercase().ends_with(".exe");
    if kind == Some("Application") || (kind.is_none() && looks_like_program) {
        return program_problem(name, command);
    }
    if !matches!(kind, None | Some("Cmdlet" | "Function" | "Filter")) {
        return Some(format!("{resolved} isn't on the harmless list"));
    }
    let lowered = resolved.to_lowercase();
    let empty = Map::new();
    let parameters = command["parameters"].as_object().unwrap_or(&empty);
    if lowered == "start-process" || lowered == "invoke-item" {
        let target = parameter(parameters, &["FilePath", "Path", "LiteralPath", "0"]);
        let verb = parameter(parameters, &["Verb"]).unwrap_or_else(|| "open".into()).to_lowercase();
        if verb != "open" || parameters.contains_key("Credential") {
            return Some("it runs something as another user or as admin".into());
        }
        if !opens_harmlessly(target.as_deref()) {
            return Some(format!(
                "{resolved} would start a program or open something that isn't a page, folder or document"
            ));
        }
        return None;
    }
    if lowered == "set-itemproperty" || lowered == "new-itemproperty" {
        return (!theme_change(parameters)).then(|| format!("{resolved} changes something other than dark mode"));
    }
    if lowered == "foreach-object" {
        return (!foreach_blocks_only(parameters)).then(|| "ForEach-Object calls a method by name".into());
    }
    let verb = lowered.split('-').next().unwrap_or_default();
    if HARMLESS_COMMANDS.contains(&lowered.as_str()) || READ_VERBS.contains(&verb) {
        return None;
    }
    Some(format!("{resolved} isn't on the harmless list"))
}

fn program_problem(name: &str, command: &Value) -> Option<String> {
    if name.contains(['\\', '/']) {
        return Some(format!("it runs {name} by its path"));
    }
    if let Some(path) = command["path"].as_str().filter(|path| !path.is_empty())
        && !windows_own(path)
    {
        return Some(format!("{name} here is {path}, not Windows' own"));
    }
    let lowered = name.to_lowercase();
    let program = match lowered.strip_suffix(".exe") {
        Some(program) => program.to_string(),
        None => lowered,
    };
    let arguments: Vec<Option<String>> =
        list(&command["arguments"]).into_iter().map(|arg| arg.as_str().map(String::from)).collect();
    if program == "explorer" {
        let opens = arguments.is_empty() || (arguments.len() == 1 && opens_harmlessly(arguments[0].as_deref()));
        return (!opens).then(|| "explorer would open something that isn't a page, folder or document".into());
    }
    if program_allows(&program, &arguments) != Some(true) {
        let shown: Vec<&str> = std::iter::once(name)
            .chain(arguments.iter().map(|arg| arg.as_deref().unwrap_or("…")))
            .collect();
        return Some(format!("{} isn't on the harmless list", shown.join(" ")));
    }
    None
}

/// Whether a program is Windows' own (in its folder, or one of its apps).
fn windows_own(path: &str) -> bool {
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    let root = root.to_lowercase().trim_end_matches('\\').to_string();
    let path = path.to_lowercase();
    path.starts_with(&format!("{root}\\")) || path.contains("\\microsoft\\windowsapps\\")
}

/// None when the line only reads, opens or makes a listed small change; otherwise why not.
fn harmless(analysis: &Value) -> Option<String> {
    let errors: Vec<&str> = list(&analysis["errors"]).into_iter().filter_map(Value::as_str).collect();
    if !errors.is_empty() {
        return Some(format!("PowerShell can't read it: {}", errors.join("; ")));
    }
    for location in list(&analysis["redirections"]).into_iter().filter_map(Value::as_str) {
        if location.trim_matches(is_space).to_lowercase() != "$null" {
            return Some(format!("it writes to {location}"));
        }
    }
    for target in list(&analysis["assignments"]).into_iter().filter_map(Value::as_str) {
        let lowered = target.to_lowercase();
        if target == "*" {
            return Some("it changes something other than a variable".into());
        }
        if target.contains(':') || lowered.starts_with("ps") || lowered == "executioncontext" || lowered == "host" {
            return Some(format!("it changes ${target}"));
        }
    }
    if let Some(drive) = list(&analysis["drives"]).into_iter().find_map(Value::as_str) {
        return Some(format!("it uses the {drive}: drive through a variable"));
    }
    for value in list(&analysis["values"]).into_iter().filter_map(Value::as_str) {
        if value.starts_with("\\\\") || value.starts_with("//") {
            return Some(format!("it reaches another computer ({value})"));
        }
    }
    for method in list(&analysis["methods"]).into_iter().filter_map(Value::as_str) {
        let lowered = method.to_lowercase();
        if !SAFE_METHODS.contains(&lowered.as_str()) {
            return Some(if method == "*" {
                "it calls a method chosen when it runs".into()
            } else {
                format!("it calls .{method}()")
            });
        }
    }
    list(&analysis["commands"]).into_iter().find_map(command_problem)
}

// Running a line

/// Python's str.splitlines: every kind of line end.
fn lines(text: &str) -> Vec<&str> {
    let ends = |c: char| {
        matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}')
    };
    let mut found = Vec::new();
    let mut rest = text;
    while let Some(at) = rest.find(ends) {
        found.push(&rest[..at]);
        let end = &rest[at..];
        rest = if end.starts_with("\r\n") { &end[2..] } else { &end[end.chars().next().map_or(1, char::len_utf8)..] };
    }
    if !rest.is_empty() {
        found.push(rest);
    }
    found
}

/// A command's answer as text for the brain; a command that only failed is an Err.
fn shown(answer: &Value, seconds: u64) -> Result<String, String> {
    if answer["timed_out"].as_bool().unwrap_or(false) {
        return Err(format!("The command was still running after {seconds} seconds and was stopped."));
    }
    let output = answer["output"].as_str().unwrap_or_default();
    let output: Vec<&str> = lines(output).into_iter().map(|line| line.trim_end_matches(is_space)).collect();
    let output = output.join("\n");
    let output = output.trim_matches('\n');
    let errors: Vec<&str> = list(&answer["errors"])
        .into_iter()
        .filter_map(Value::as_str)
        .map(|error| error.trim_matches(is_space))
        .filter(|error| !error.is_empty())
        .collect();
    let has_output = !output.trim_matches(is_space).is_empty();
    if !errors.is_empty() && !has_output {
        return Err(format!("The command failed: {}", errors.join("\n")));
    }
    let mut text = if has_output { output.to_string() } else { "Done.".to_string() };
    if !errors.is_empty() {
        text += &format!("\nErrors:\n{}", errors.join("\n"));
    }
    let length = text.chars().count();
    if length > OUTPUT_CHARS {
        let kept: String = text.chars().take(OUTPUT_CHARS).collect();
        text = format!(
            "{kept}\n[cut here: {length} characters in all. Ask for less, for example with Select-Object -First 10.]"
        );
    }
    Ok(text)
}

/// commands.ps1 in one PowerShell, kept open; one request at a time. It starts again if it stops answering.
struct PowerShell {
    running: Mutex<Option<Running>>,
}

struct Running {
    child: Child,
    stdin: ChildStdin,
    /// Its answers, a line each; None once it ended.
    answers: Receiver<Option<Vec<u8>>>,
}

impl PowerShell {
    fn new() -> PowerShell {
        PowerShell { running: Mutex::new(None) }
    }

    fn start() -> Result<Running, String> {
        let script = std::env::temp_dir().join("Natro").join("commands.ps1");
        std::fs::create_dir_all(script.parent().expect("a folder"))
            .and_then(|_| std::fs::write(&script, SCRIPT))
            .map_err(|e| format!("PowerShell's script couldn't be written to {} ({e}).", script.display()))?;
        let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
        let program = Path::new(&root).join(r"System32\WindowsPowerShell\v1.0\powershell.exe");
        let mut child = Command::new(program)
            .args(["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&script)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| format!("PowerShell didn't start ({e})."))?;
        let (stdout, stderr) = (child.stdout.take().expect("piped"), child.stderr.take().expect("piped"));
        let (answer, answers) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).split(b'\n') {
                let Ok(line) = line else { break };
                if answer.send(Some(line)).is_err() {
                    return;
                }
            }
            let _ = answer.send(None); // it ended
        });
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                log::warn!("the commands' PowerShell: {line}");
            }
        });
        let stdin = child.stdin.take().expect("piped");
        Ok(Running { child, stdin, answers })
    }

    fn ask(&self, request: Value, wait: Duration) -> Result<Value, String> {
        let line = {
            let mut running = self.running.lock().unwrap();
            let first = running.as_mut().is_none_or(|it| !matches!(it.child.try_wait(), Ok(None)));
            if first {
                *running = Some(PowerShell::start()?);
            }
            let it = running.as_mut().expect("started");
            let sent = writeln!(it.stdin, "{request}").and_then(|_| it.stdin.flush()).is_ok();
            let wait = wait + if first { START } else { Duration::ZERO };
            let line = if sent { it.answers.recv_timeout(wait).ok().flatten() } else { None };
            match line {
                Some(line) => line,
                None => {
                    if let Some(mut it) = running.take() {
                        let _ = it.child.kill();
                    }
                    return Err("PowerShell stopped answering (it starts again for the next command).".into());
                }
            }
        };
        let answer: Value = serde_json::from_slice(&line)
            .map_err(|e| format!("PowerShell's answer didn't read ({e}): {}", String::from_utf8_lossy(&line)))?;
        if let Some(failed) = answer.get("failed") {
            let failed = failed.as_str().map_or_else(|| failed.to_string(), String::from);
            return Err(format!("PowerShell couldn't do that: {failed}"));
        }
        Ok(answer)
    }

    fn analyze(&self, command: &str) -> Result<Value, String> {
        self.ask(json!({"op": "check", "command": command}), CHECK)
    }

    fn run(&self, command: &str, fresh: bool, seconds: u64) -> Result<Value, String> {
        let request = json!({"op": "run", "command": command, "seconds": seconds, "fresh": fresh});
        self.ask(request, Duration::from_secs(seconds + 10))
    }

    #[cfg(test)]
    fn stop(&self) {
        if let Some(mut it) = self.running.lock().unwrap().take() {
            let _ = it.child.kill();
        }
    }
}

impl Drop for PowerShell {
    fn drop(&mut self) {
        if let Some(mut it) = self.running.get_mut().unwrap().take() {
            let _ = it.child.kill();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::OnceLock;

    const THEMES: &str = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize";

    /// One PowerShell for the tests that only ask what a line would run.
    fn shell() -> &'static PowerShell {
        static SHELL: OnceLock<PowerShell> = OnceLock::new();
        SHELL.get_or_init(PowerShell::new)
    }

    fn problem(line: &str) -> Option<String> {
        harmless(&shell().analyze(line).unwrap())
    }

    #[test]
    fn harmless_lines_run_right_away() {
        let dark = format!("Set-ItemProperty -Path {THEMES} -Name AppsUseLightTheme -Value 0");
        let light = format!("Set-ItemProperty {THEMES} SystemUsesLightTheme 1");
        for line in [
            "Get-Date",
            "Get-ChildItem $HOME | Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name",
            "Get-Process | Where-Object CPU -gt 10 | Format-Table Name, CPU",
            "Get-ChildItem | ForEach-Object { $_.Name.ToUpper() }",
            "(Get-Date).AddDays(3).ToString('dddd')",
            "$free = (Get-PSDrive C).Free; [math]::Round($free / 1GB, 1)",
            "Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress",
            "ipconfig /all",
            "ipconfig",
            "netsh wlan show interfaces",
            "ping -n 1 example.com",
            "Start-Process https://example.com",
            "Start-Process ms-settings:bluetooth",
            "saps 'shell:Downloads'",
            "Invoke-Item $HOME",
            &dark,
            &light,
            "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1, 60)",
            "Set-Clipboard -Value 'hi'",
            "rundll32.exe user32.dll,LockWorkStation",
            "Get-Process > $null",
            "Test-Connection example.com -Count 1",
            "$env:USERPROFILE",
            "Get-Content $HOME\\notes.txt | Select-String todo",
            "Write-Output 'გამარჯობა'",
        ] {
            assert_eq!(problem(line), None, "{line}");
        }
    }

    #[test]
    fn everything_else_asks() {
        let seven = format!("Set-ItemProperty {THEMES} AppsUseLightTheme 7");
        for (line, why) in [
            ("Remove-Item $HOME\\x.txt", "Remove-Item"),
            ("Get-ChildItem | Remove-Item", "Remove-Item"),
            ("Get-Date; Restart-Computer", "Restart-Computer"),
            ("Stop-Process -Name chrome", "Stop-Process"),
            ("Get-Process | ForEach-Object Kill", "method by name"),
            ("Get-Process | % { $_.Kill() }", ".Kill()"),
            ("[IO.File]::Delete('x')", ".Delete()"),
            ("ipconfig /release", "harmless list"),
            ("netsh wlan disconnect", "harmless list"),
            ("Get-Date > $HOME\\out.txt", "writes to"),
            ("Get-Process | Out-File x.txt", "Out-File"),
            ("Start-Process notepad -Verb RunAs", "admin"),
            ("Start-Process $HOME\\Downloads\\setup.exe", "start a program"),
            ("Start-Process file:///C:/x.exe", "start a program"),
            ("Start-Process notepad", "start a program"),
            // Windows drops the trailing dot, and a stream runs as its file would.
            ("Start-Process setup.exe.", "start a program"),
            ("Start-Process 'C:\\x\\setup.exe::$DATA'", "start a program"),
            ("Invoke-Expression 'Get-Date'", "Invoke-Expression"),
            ("iex 'Get-Date'", "Invoke-Expression"),
            ("& $command", "only known when it runs"),
            ("$env:Path = 'C:\\x'", "env:Path"),
            ("$PSDefaultParameterValues['*:Verb'] = 'RunAs'", "other than a variable"),
            ("$ExecutionContext.InvokeCommand.PreCommandLookupAction = { }", "other than a variable"),
            ("Set-ItemProperty HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run -Name x -Value y", "dark mode"),
            (&seven, "dark mode"),
            ("Get-ChildItem -Path $(Remove-Item x)", "Remove-Item"),
            ("function Get-Thing { Remove-Item y }; Get-Thing", "Remove-Item"),
            ("Get-Content \\\\server\\share\\x.txt", "another computer"),
            ("C:\\tools\\ipconfig.exe /all", "by its path"),
            ("cmd /c del x", "cmd"),
            ("shutdown /s /t 0", "shutdown"),
            ("Get-Date (", "can't read"),
            ("New-Item x.txt", "New-Item"),
            ("Set-Alias Get-Thing Remove-Item", "Set-Alias"),
            ("${C:\\x.txt} = 'hi'", "C:"),
            ("rundll32.exe shell32.dll,Control_RunDLL", "harmless list"),
            ("winget install Spotify", "harmless list"),
            ("Invoke-WebRequest https://example.com -OutFile x.zip", "Invoke-WebRequest"),
        ] {
            let found = problem(line);
            assert!(found.as_deref().is_some_and(|found| found.contains(why)), "{line}: {found:?}");
        }
    }

    /// "Lock the PC", "dark mode on"... run without a model (server/natro_agent/quick.py, which
    /// keeps desktop/quick-commands.json): never refused.
    #[test]
    fn the_brains_quick_pc_commands_are_all_harmless() {
        let lines: Vec<String> = serde_json::from_str(include_str!("../../../quick-commands.json")).unwrap();
        assert!(lines.len() > 20);
        for line in lines {
            assert_eq!(problem(&line), None, "{line}");
        }
    }

    #[test]
    fn what_opening_may_open() {
        let folder = std::env::temp_dir().join(format!("natro-commands-test-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        std::fs::write(folder.join("report.pdf"), "x").unwrap();
        let path = |name: &str| folder.join(name).display().to_string();
        assert!(opens_harmlessly(Some(&folder.display().to_string()))); // a folder
        assert!(opens_harmlessly(Some(&path("report.pdf")))); // a document
        assert!(opens_harmlessly(Some(&path("report.pdf.")))); // Windows opens report.pdf
        assert!(opens_harmlessly(Some("https://example.com")) && opens_harmlessly(Some("ms-settings:display")));
        assert!(opens_harmlessly(Some("$HOME")) && opens_harmlessly(Some("${env:USERPROFILE}")));
        for target in [
            Some(path("setup.exe")),
            Some(path("x.ps1")),
            Some(path("setup.exe.")),
            Some(path("setup.exe . .")),
            Some(path("report.pdf:x.exe")),
            Some("notepad".into()),
            Some("notepad.".into()),
            Some("file:///C:/x.exe".into()),
            Some("\\\\server\\share".into()),
            Some("javascript:alert(1)".into()),
            None,
            Some(String::new()),
        ] {
            assert!(!opens_harmlessly(target.as_deref()), "{target:?}");
        }
    }

    #[test]
    fn variables_are_filled_in_as_they_will_run() {
        let home = home();
        assert_eq!(expand("$HOME\\x"), format!("{home}\\x"));
        assert_eq!(expand("${home}x $Home"), format!("{home}x {home}"));
        assert_eq!(expand("$homepath $env:NATRO_NOT_SET!"), "$homepath !");
        assert_eq!(expand("${env:USERPROFILE}"), std::env::var("USERPROFILE").unwrap());
        assert_eq!(expand("${env:x $ 5$"), "${env:x $ 5$");
        assert_eq!(extension("a\\b.tar.GZ"), ".gz");
        assert_eq!((extension(".bashrc"), extension("x.")), ("".into(), ".".into()));
    }

    #[test]
    fn an_answer_as_the_brain_reads_it() {
        let read = |answer: Value| shown(&answer, RUN_SECONDS);
        let table = json!({"output": "\r\nName  CPU\n----  ---\nfoo   12   \n\n", "errors": []});
        assert_eq!(read(table).unwrap(), "Name  CPU\n----  ---\nfoo   12");
        assert_eq!(read(json!({"output": "", "errors": []})).unwrap(), "Done.");
        assert_eq!(read(json!({"output": "a\n", "errors": ["b failed"]})).unwrap(), "a\nErrors:\nb failed");
        assert_eq!(read(json!({"output": "", "errors": ["nope"]})).unwrap_err(), "The command failed: nope");
        assert!(read(json!({"timed_out": true})).unwrap_err().contains("still running"));
        let long = read(json!({"output": "x".repeat(10000), "errors": []})).unwrap();
        assert!(long.chars().count() < 6200 && long.contains("[cut here: 10000 characters"));
    }

    #[test]
    fn running_commands() {
        let pc = Commands::new();
        let run = |line: &str| pc.run("run_command", &json!({"command": line}));
        assert_eq!(run("Write-Output 'გამარჯობა héllo'").unwrap(), "გამარჯობა héllo");
        // Harmless lines share a runspace; a risky one gets its own, so it leaves nothing behind.
        assert_eq!(run("$x = 41; $x + 1").unwrap(), "42");
        assert_eq!(run("$x").unwrap(), "41");
        assert_eq!(pc.run("run_command_risky", &json!({"command": "$y = 7"})).unwrap(), "Done.");
        assert_eq!(run("$y").unwrap(), "Done.");
        // Each command starts in his home folder.
        assert_ne!(run("Set-Location ..; (Get-Location).Path").unwrap(), run("(Get-Location).Path").unwrap());
        assert!(run("Get-Item /no/such/file").unwrap_err().contains("failed"));
        assert_eq!(run("  ").unwrap_err(), "Say which command to run.");
    }

    #[test]
    fn a_risky_line_is_refused_and_nothing_happens() {
        let victim = std::env::temp_dir().join(format!("natro-keep-{}.txt", std::process::id()));
        std::fs::write(&victim, "x").unwrap();
        let line = json!({"command": format!("Remove-Item '{}'", victim.display())});
        let pc = Commands::new();
        assert!(pc.run("run_command", &line).unwrap_err().contains("run_command_risky"));
        assert!(victim.exists());
        assert_eq!(pc.run("run_command_risky", &line).unwrap(), "Done.");
        assert!(!victim.exists());
    }

    #[test]
    fn a_command_that_runs_too_long_is_stopped_and_the_next_one_runs() {
        let pc = Commands::new();
        let answer = pc.shell.run("Start-Sleep 5", false, 1).unwrap();
        assert!(shown(&answer, 1).unwrap_err().contains("still running"));
        assert_eq!(pc.run("run_command", &json!({"command": "Write-Output ok"})).unwrap(), "ok");
    }

    /// By hand, on the PC: what run_command decides for each line of a JSON file (a list of
    /// lines), to compare with the helper's: NATRO_TEST_LINES=lines.json cargo test -- --ignored
    /// --nocapture decides_on_these_lines
    #[test]
    #[ignore]
    fn decides_on_these_lines() {
        let file = std::env::var("NATRO_TEST_LINES").expect("NATRO_TEST_LINES");
        let lines: Vec<String> = serde_json::from_str(&std::fs::read_to_string(file).unwrap()).unwrap();
        let decided: Vec<Option<String>> = lines.iter().map(|line| problem(line)).collect();
        println!("DECIDED {}", serde_json::to_string(&decided).unwrap());
    }

    #[test]
    fn powershell_starts_again_after_it_stopped() {
        let pc = Commands::new();
        pc.warm_up();
        pc.shell.stop();
        assert_eq!(pc.run("run_command", &json!({"command": "Write-Output again"})).unwrap(), "again");
    }
}
