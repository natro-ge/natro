//! Apps on the PC: open them from the Start menu, see which are open, close them (the tools
//! open_app, close_app, force_close_app and list_open_apps).
//!
//! Apps are found the way the Start menu lists them (Get-StartApps: desktop and Store apps
//! alike) and opened through shell:AppsFolder, as if clicked there. Closing asks each of the
//! app's windows to close, as clicking X does, so the app itself asks about unsaved work.
//! Force-closing kills its processes; the brain asks the owner first.
//!
//! Ported from the Python helper's apps.py (cleanup step 5.1) with the same words and the same
//! matching: a spoken name is scored against the real ones as it was there (difflib.rs).

use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use windows::Win32::Foundation::{CloseHandle, HWND, LPARAM, WPARAM};
use windows::Win32::Graphics::Dwm::{DWMWA_CLOAKED, DwmGetWindowAttribute};
use windows::Win32::Storage::FileSystem::{GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::Threading::{
    OpenProcess, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    QueryFullProcessImageNameW, TerminateProcess,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumChildWindows, EnumWindows, GW_OWNER, GWL_EXSTYLE, GetWindow, GetWindowLongW, GetWindowTextLengthW,
    GetWindowTextW, GetWindowThreadProcessId, IsWindow, IsWindowVisible, PostMessageW, WM_CLOSE, WS_EX_TOOLWINDOW,
};
use windows::core::{BOOL, HSTRING, PWSTR, w};

use super::{quoted, spec};
use crate::difflib;

/// Start menu entries Natro never opens: uninstallers, help pages and web links.
const NOT_APPS: [&str; 6] = ["uninstall", "readme", "help", "documentation", "website", "release notes"];
/// What people call some apps, by their Start menu names.
const ALIASES: [(&str, &str); 11] = [
    ("vs code", "visual studio code"),
    ("vscode", "visual studio code"),
    ("code", "visual studio code"),
    ("chrome", "google chrome"),
    ("edge", "microsoft edge"),
    ("explorer", "file explorer"),
    ("files", "file explorer"),
    ("cmd", "command prompt"),
    ("store", "microsoft store"),
    ("task manager", "task manager"),
    ("control panel", "control panel"),
];
const GOOD_MATCH: f64 = 0.6;
/// Not found in a list older than this: read the Start menu again (it may have just been installed).
const RELOAD: Duration = Duration::from_secs(30);
const CLOSE_WAIT: Duration = Duration::from_secs(3);
/// Reading the Start menu takes about a second.
const START_APPS_WAIT: Duration = Duration::from_secs(30);
/// Never force-closed, whatever they're called.
const PROTECTED: [&str; 16] = [
    "system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "tailscaled.exe", "tailscale-ipn.exe",
    "everything.exe", "syncthing.exe",
];
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// The tools as the brain sees them: the first of desktop/tools.json.
pub fn specs() -> Vec<Value> {
    let app = json!({"app": {"type": "string",
                             "description": "The app's name, as he says it (\"Spotify\", \"VS Code\")."}});
    vec![
        spec("open_app", "Open an app on the PC by name, as from the Start menu.", &app, &["app"], false, true),
        spec(
            "close_app",
            "Close an app normally, as if clicking X on its windows: the app itself asks about unsaved work. \
             Some apps (like Spotify) only hide in the tray.",
            &app,
            &["app"],
            false,
            true,
        ),
        spec(
            "force_close_app",
            "Kill an app that won't close or is stuck, with all its processes. Unsaved work in it is lost.",
            &app,
            &["app"],
            true,
            false,
        ),
        spec("list_open_apps", "Which apps have windows open on the PC.", &json!({}), &[], false, false),
    ]
}

/// Whether name is one of these tools.
pub fn offers(name: &str) -> bool {
    matches!(name, "open_app" | "close_app" | "force_close_app" | "list_open_apps")
}

// Names

/// The letters and digits of a name, lowercase (as Python's casefold), one space between words.
fn words(text: &str) -> String {
    let mut folded = String::new();
    for c in text.chars() {
        match c {
            // Where casefold gives letters that to_lowercase doesn't.
            'ß' | 'ẞ' => folded.push_str("ss"),
            'ſ' => folded.push('s'),
            'ﬀ' => folded.push_str("ff"),
            'ﬁ' => folded.push_str("fi"),
            'ﬂ' => folded.push_str("fl"),
            'ﬃ' => folded.push_str("ffi"),
            'ﬄ' => folded.push_str("ffl"),
            'ﬅ' | 'ﬆ' => folded.push_str("st"),
            _ => folded.extend(c.to_lowercase()),
        }
    }
    folded
        .split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit()))
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// How well a spoken app name matches a real one, 0 to 1.
pub fn match_score(query: &str, name: &str) -> f64 {
    let (query, name) = (words(query), words(name));
    let query = ALIASES.iter().find(|(said, _)| *said == query).map_or(query.clone(), |(_, real)| real.to_string());
    if query.is_empty() || name.is_empty() {
        return 0.0;
    }
    if query == name || query.replace(' ', "") == name.replace(' ', "") {
        return 1.0;
    }
    let name_words: Vec<&str> = name.split(' ').collect();
    let parts: Vec<&str> = query.split(' ').collect();
    if parts.iter().all(|part| name_words.iter().any(|word| word.starts_with(part))) {
        // "Spotify" over "Spotify Widget".
        return 0.9 - 0.02 * (name_words.len() as f64 - parts.len() as f64);
    }
    if name.contains(&query) {
        return 0.75;
    }
    0.75 * difflib::ratio(&query, &name)
}

/// A program's file name without its extension ("Code" for C:\VS\Code.exe).
fn stem(exe: &str) -> String {
    Path::new(exe).file_stem().map_or_else(String::new, |stem| stem.to_string_lossy().into_owned())
}

// Opening

#[derive(Clone, Debug, PartialEq)]
pub struct StartApp {
    pub name: String,
    /// As shell:AppsFolder knows it.
    pub app_id: String,
}

/// Why an app wasn't found: it isn't installed (what Natro is told, with the closest names), or the
/// Start menu couldn't be read.
pub enum Missing {
    NotInstalled(String),
    Unread(String),
}

impl Missing {
    fn message(self) -> String {
        match self {
            Missing::NotInstalled(message) => message,
            Missing::Unread(why) => format!("The PC's Start menu couldn't be read: {why}."),
        }
    }
}

type Load = Box<dyn Fn() -> Result<Vec<StartApp>, String> + Send + Sync>;
type Launch = Box<dyn Fn(&str) -> std::io::Result<()> + Send + Sync>;

/// The Start menu's apps, read once and again when a name isn't found.
pub struct StartMenu {
    load: Load,
    launch: Launch,
    /// The apps, and when they were read.
    apps: Mutex<(Vec<StartApp>, Option<Instant>)>,
}

impl StartMenu {
    pub fn new() -> StartMenu {
        StartMenu::with(Box::new(start_apps), Box::new(launch))
    }

    fn with(load: Load, launch: Launch) -> StartMenu {
        StartMenu { load, launch, apps: Mutex::default() }
    }

    /// Read the list ahead of the first request (it takes about a second). Blocking.
    pub fn warm_up(&self) {
        let mut apps = self.apps.lock().unwrap();
        if apps.0.is_empty()
            && let Err(Missing::Unread(why)) = self.reload(&mut apps)
        {
            log::warn!("the Start menu couldn't be read: {why}");
        }
    }

    fn reload(&self, apps: &mut (Vec<StartApp>, Option<Instant>)) -> Result<(), Missing> {
        *apps = ((self.load)().map_err(Missing::Unread)?, Some(Instant::now()));
        Ok(())
    }

    /// The installed app that best matches a spoken name (open_app's, and the dock's icons'). Blocking.
    pub fn find(&self, name: &str) -> Result<StartApp, Missing> {
        let mut apps = self.apps.lock().unwrap();
        if apps.0.is_empty() {
            self.reload(&mut apps)?;
        }
        let mut found = best(name, &apps.0);
        if found.is_none() && apps.1.is_none_or(|read| read.elapsed() > RELOAD) {
            self.reload(&mut apps)?;
            found = best(name, &apps.0);
        }
        found.cloned().ok_or_else(|| {
            let names: Vec<String> = apps.0.iter().map(|app| words(&app.name)).collect();
            let close = difflib::close_matches(&words(name), &names, 5, 0.3);
            let hint =
                if close.is_empty() { String::new() } else { format!(" The closest names: {}.", close.join(", ")) };
            Missing::NotInstalled(format!("No app called {} is installed on the PC.{hint}", quoted(name)))
        })
    }

    fn open(&self, app: &str) -> Result<String, String> {
        let found = self.find(app).map_err(Missing::message)?;
        (self.launch)(&found.app_id).map_err(|e| format!("{} didn't open: {e}", found.name))?;
        Ok(format!("Opened {}.", found.name))
    }
}

/// The first of the best matches, if it's good enough.
fn best<'a>(query: &str, apps: &'a [StartApp]) -> Option<&'a StartApp> {
    let mut best: Option<(f64, &StartApp)> = None;
    for app in apps {
        let score = match_score(query, &app.name);
        if score >= GOOD_MATCH && best.is_none_or(|(top, _)| score > top) {
            best = Some((score, app));
        }
    }
    best.map(|(_, app)| app)
}

/// The Start menu's apps (blocking: runs PowerShell, about a second).
fn start_apps() -> Result<Vec<StartApp>, String> {
    let mut child = Command::new("powershell")
        .args([
            "-NoProfile",
            "-Command",
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|e| format!("PowerShell didn't start ({e})"))?;
    let mut stdout = child.stdout.take().expect("piped");
    let reading = thread::spawn(move || {
        let mut out = Vec::new();
        let _ = stdout.read_to_end(&mut out);
        out
    });
    let deadline = Instant::now() + START_APPS_WAIT;
    while child.try_wait().map_err(|e| e.to_string())?.is_none() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            return Err(format!("Get-StartApps took over {} s", START_APPS_WAIT.as_secs()));
        }
        thread::sleep(Duration::from_millis(50));
    }
    parse_start_apps(&String::from_utf8_lossy(&reading.join().unwrap_or_default()))
}

/// Get-StartApps' JSON: one app is an object, more a list. Help pages, uninstallers and web links left out.
fn parse_start_apps(text: &str) -> Result<Vec<StartApp>, String> {
    let text = text.trim_start_matches('\u{feff}').trim();
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let listed: Value = serde_json::from_str(text).map_err(|e| format!("Get-StartApps' list didn't read ({e})"))?;
    let listed = match listed {
        Value::Array(items) => items,
        item => vec![item],
    };
    Ok(listed
        .iter()
        .filter_map(|item| {
            Some(StartApp { name: item["Name"].as_str()?.to_string(), app_id: item["AppID"].as_str()?.to_string() })
        })
        .filter(|app| !not_an_app(&app.name) && !app.app_id.starts_with("http:") && !app.app_id.starts_with("https:"))
        .collect())
}

/// Whether a Start menu entry is one Natro never opens: one of NOT_APPS as whole words, in any case.
fn not_an_app(name: &str) -> bool {
    let lower: Vec<char> = name.chars().map(|c| c.to_lowercase().next().unwrap_or(c)).collect();
    let word = |i: usize| lower.get(i).is_some_and(|c| c.is_alphanumeric() || *c == '_');
    NOT_APPS.iter().any(|phrase| {
        let phrase: Vec<char> = phrase.chars().collect();
        (0..lower.len()).any(|start| {
            lower[start..].starts_with(&phrase) && !(start > 0 && word(start - 1)) && !word(start + phrase.len())
        })
    })
}

fn launch(app_id: &str) -> std::io::Result<()> {
    Command::new("explorer.exe").arg(format!(r"shell:AppsFolder\{app_id}")).spawn().map(drop)
}

// Open windows

#[derive(Clone, Debug)]
struct Window {
    hwnd: isize,
    title: String,
    /// The program behind the window, its full path ("" when Windows won't say).
    exe: String,
    /// What the program calls itself ("Google Chrome"), else its file name.
    app: String,
}

impl Window {
    fn handle(&self) -> HWND {
        HWND(self.hwnd as *mut core::ffi::c_void)
    }
}

/// The app windows on screen (or minimized), as the taskbar shows them.
fn open_windows() -> Vec<Window> {
    let mut found: Vec<Window> = Vec::new();
    unsafe {
        let _ = EnumWindows(Some(visit), LPARAM(&mut found as *mut Vec<Window> as isize));
    }
    found
}

unsafe extern "system" fn visit(hwnd: HWND, found: LPARAM) -> BOOL {
    let found = unsafe { &mut *(found.0 as *mut Vec<Window>) };
    if let Some(window) = unsafe { app_window(hwnd) } {
        found.push(window);
    }
    true.into()
}

unsafe fn app_window(hwnd: HWND) -> Option<Window> {
    unsafe {
        if !IsWindowVisible(hwnd).as_bool() || GetWindow(hwnd, GW_OWNER).is_ok() {
            return None;
        }
        let title = window_text(hwnd);
        if title.is_empty() || (GetWindowLongW(hwnd, GWL_EXSTYLE) as u32) & WS_EX_TOOLWINDOW.0 != 0 || cloaked(hwnd) {
            return None;
        }
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        let mut exe = process_path(pid);
        let frame =
            Path::new(&exe).file_name().is_some_and(|name| name.eq_ignore_ascii_case("applicationframehost.exe"));
        let app = if frame {
            // A Store app's frame: the app itself is the process of a child window.
            if let Some(child) = child_pids(hwnd).into_iter().find(|child| *child != pid) {
                exe = process_path(child);
            }
            title.clone()
        } else if exe.is_empty() {
            title.clone()
        } else {
            program_name(&exe)
        };
        Some(Window { hwnd: hwnd.0 as isize, title, exe, app })
    }
}

fn window_text(hwnd: HWND) -> String {
    unsafe {
        let mut text = vec![0u16; GetWindowTextLengthW(hwnd).max(0) as usize + 1];
        let length = GetWindowTextW(hwnd, &mut text).max(0) as usize;
        String::from_utf16_lossy(&text[..length])
    }
}

/// Store apps keep hidden ("cloaked") windows that look visible.
fn cloaked(hwnd: HWND) -> bool {
    let mut cloaked = 0u32;
    let read = unsafe {
        DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, (&mut cloaked as *mut u32).cast(), size_of::<u32>() as u32)
    };
    read.is_ok() && cloaked != 0
}

/// The processes of a window's child windows, in their order.
fn child_pids(hwnd: HWND) -> Vec<u32> {
    unsafe extern "system" fn child(hwnd: HWND, pids: LPARAM) -> BOOL {
        let pids = unsafe { &mut *(pids.0 as *mut Vec<u32>) };
        let mut pid = 0;
        unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
        pids.push(pid);
        true.into()
    }
    let mut pids: Vec<u32> = Vec::new();
    unsafe {
        let _ = EnumChildWindows(Some(hwnd), Some(child), LPARAM(&mut pids as *mut Vec<u32> as isize));
    }
    pids
}

/// The program a process runs, its full path ("" when Windows won't say).
fn process_path(pid: u32) -> String {
    unsafe {
        let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return String::new();
        };
        let mut path = [0u16; 1024];
        let mut length = path.len() as u32;
        let read = QueryFullProcessImageNameW(process, PROCESS_NAME_WIN32, PWSTR(path.as_mut_ptr()), &mut length);
        let _ = CloseHandle(process);
        if read.is_ok() { String::from_utf16_lossy(&path[..length as usize]) } else { String::new() }
    }
}

/// A program's own name for itself (its FileDescription), else its file name.
fn program_name(exe: &str) -> String {
    static NAMES: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();
    let names = NAMES.get_or_init(Mutex::default);
    if let Some(name) = names.lock().unwrap().get(exe) {
        return name.clone();
    }
    let name = described(exe).unwrap_or_else(|| stem(exe));
    names.lock().unwrap().insert(exe.to_string(), name.clone());
    name
}

/// The FileDescription in a program's version information, in its first language.
fn described(exe: &str) -> Option<String> {
    unsafe {
        let path = HSTRING::from(exe);
        let size = GetFileVersionInfoSizeW(&path, None);
        if size == 0 {
            return None;
        }
        let mut info = vec![0u8; size as usize];
        GetFileVersionInfoW(&path, None, size, info.as_mut_ptr().cast()).ok()?;
        let (mut value, mut length) = (std::ptr::null_mut(), 0u32);
        if !VerQueryValueW(info.as_ptr().cast(), w!(r"\VarFileInfo\Translation"), &mut value, &mut length).as_bool()
            || length < 4
        {
            return None;
        }
        let (language, codepage) = (*(value as *const u16), *(value as *const u16).add(1));
        let key = HSTRING::from(format!(r"\StringFileInfo\{language:04x}{codepage:04x}\FileDescription"));
        if !VerQueryValueW(info.as_ptr().cast(), &key, &mut value, &mut length).as_bool() || length == 0 {
            return None;
        }
        let text = std::slice::from_raw_parts(value as *const u16, length as usize);
        let text = String::from_utf16_lossy(text.split(|c| *c == 0).next().unwrap_or_default());
        Some(text.trim().to_string()).filter(|text| !text.is_empty())
    }
}

/// The windows by app, in the order the apps were first seen.
fn by_app(windows: &[Window]) -> Vec<(String, Vec<Window>)> {
    let mut apps: Vec<(String, Vec<Window>)> = Vec::new();
    for window in windows {
        match apps.iter_mut().find(|(app, _)| *app == window.app) {
            Some((_, its)) => its.push(window.clone()),
            None => apps.push((window.app.clone(), vec![window.clone()])),
        }
    }
    apps
}

/// The open app that best matches name: its name and its windows.
fn find_open_app(name: &str, windows: &[Window]) -> Result<(String, Vec<Window>), String> {
    let apps = by_app(windows);
    let score = |(app, its): &(String, Vec<Window>)| {
        its.iter()
            .filter(|w| !w.exe.is_empty())
            .map(|w| match_score(name, &stem(&w.exe)))
            .fold(match_score(name, app), f64::max)
    };
    let mut best: Option<(f64, usize)> = None;
    for (i, app) in apps.iter().enumerate() {
        let score = score(app);
        if best.is_none_or(|(top, _)| score > top) {
            best = Some((score, i));
        }
    }
    if let Some((score, i)) = best
        && score >= GOOD_MATCH
    {
        return Ok(apps[i].clone());
    }
    // Else a window whose title names it ("Spotify" in a browser tab's title doesn't win over the app itself).
    let wanted = words(name);
    if !wanted.is_empty()
        && let Some(titled) = windows.iter().find(|w| words(&w.title).contains(&wanted))
    {
        let app = titled.app.clone();
        return Ok((app.clone(), windows.iter().filter(|w| w.app == app).cloned().collect()));
    }
    let mut names: Vec<&str> = apps.iter().map(|(app, _)| app.as_str()).collect();
    names.sort();
    let open_now = if names.is_empty() { "none".to_string() } else { names.join(", ") };
    Err(format!("No open app called {}. Open apps: {open_now}.", quoted(name)))
}

// Running processes

pub(super) struct Process {
    pub pid: u32,
    parent: u32,
    /// Its program's file name ("Spotify.exe").
    pub name: String,
}

pub(super) fn processes() -> Vec<Process> {
    let mut found = Vec::new();
    unsafe {
        let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return found;
        };
        let mut entry = PROCESSENTRY32W { dwSize: size_of::<PROCESSENTRY32W>() as u32, ..Default::default() };
        let mut more = Process32FirstW(snapshot, &mut entry).is_ok();
        while more {
            let name = entry.szExeFile.split(|c| *c == 0).next().unwrap_or_default();
            found.push(Process {
                pid: entry.th32ProcessID,
                parent: entry.th32ParentProcessID,
                name: String::from_utf16_lossy(name),
            });
            more = Process32NextW(snapshot, &mut entry).is_ok();
        }
        let _ = CloseHandle(snapshot);
    }
    found
}

/// Natro (own), what started her (Explorer, Task Scheduler...) and what she started:
/// never force-closed.
fn spared(own: u32, processes: &[Process]) -> HashSet<u32> {
    let parents: HashMap<u32, u32> = processes.iter().map(|p| (p.pid, p.parent)).collect();
    let mut spared: HashSet<u32> = processes.iter().filter(|p| p.parent == own).map(|p| p.pid).collect();
    spared.insert(own);
    let mut pid = own;
    while let Some(&parent) = parents.get(&pid) {
        if parent == 0 || !parents.contains_key(&parent) || !spared.insert(parent) {
            break;
        }
        pid = parent;
    }
    spared
}

fn kill(pid: u32) -> bool {
    unsafe {
        let Ok(process) = OpenProcess(PROCESS_TERMINATE, false, pid) else {
            return false;
        };
        let killed = TerminateProcess(process, 1).is_ok();
        let _ = CloseHandle(process);
        killed
    }
}

type OpenWindows = Box<dyn Fn() -> Vec<Window> + Send + Sync>;

pub struct Apps {
    menu: Arc<StartMenu>,
    windows: OpenWindows,
}

impl Apps {
    pub fn new(menu: Arc<StartMenu>) -> Apps {
        Apps { menu, windows: Box::new(open_windows) }
    }

    /// Run one of the tools (offers). Blocking. Ok: its result for Natro; Err: why it failed, for her.
    pub fn run(&self, name: &str, args: &Value) -> Result<String, String> {
        if name == "list_open_apps" {
            return Ok(self.running());
        }
        let Some(app) = args["app"].as_str() else {
            return Err(format!("{name} needs the app's name."));
        };
        match name {
            "open_app" => self.menu.open(app),
            "close_app" => self.close(app),
            "force_close_app" => self.force_close(app),
            _ => Err(format!("The PC has no tool called {}.", quoted(name))),
        }
    }

    fn running(&self) -> String {
        let mut apps = by_app(&(self.windows)());
        if apps.is_empty() {
            return "No apps are open.".into();
        }
        apps.sort_by(|(a, _), (b, _)| a.cmp(b));
        let listed: Vec<String> = apps
            .iter()
            .map(|(app, its)| if its.len() > 1 { format!("{app} ({} windows)", its.len()) } else { app.clone() })
            .collect();
        format!("Open apps: {}.", listed.join(", "))
    }

    fn close(&self, app: &str) -> Result<String, String> {
        let (name, windows) = find_open_app(app, &(self.windows)())?;
        for window in &windows {
            unsafe {
                let _ = PostMessageW(Some(window.handle()), WM_CLOSE, WPARAM(0), LPARAM(0));
            }
        }
        let deadline = Instant::now() + CLOSE_WAIT;
        loop {
            let still = windows
                .iter()
                .filter(|w| unsafe { IsWindow(Some(w.handle())).as_bool() && IsWindowVisible(w.handle()).as_bool() })
                .count();
            if still == 0 {
                return Ok(format!("Closed {name}."));
            }
            if Instant::now() >= deadline {
                return Ok(format!(
                    "Asked {name} to close, but {still} of its windows are still open: it may be asking about \
                     unsaved work."
                ));
            }
            thread::sleep(Duration::from_millis(200));
        }
    }

    fn force_close(&self, app: &str) -> Result<String, String> {
        let (name, exes): (String, HashSet<String>) = match find_open_app(app, &(self.windows)()) {
            Ok((name, windows)) => {
                (name, windows.iter().filter(|w| !w.exe.is_empty()).map(|w| w.exe.to_lowercase()).collect())
            }
            Err(_) => (app.to_string(), HashSet::new()),
        };
        let running = processes();
        let spared = spared(std::process::id(), &running);
        let victims: Vec<u32> = running
            .iter()
            .filter(|p| !spared.contains(&p.pid) && !PROTECTED.contains(&p.name.to_lowercase().as_str()))
            .filter(|p| {
                let exe = process_path(p.pid);
                // Its windows' programs; or, with no window (say, in the tray), a program of that name.
                !exe.is_empty()
                    && (exes.contains(&exe.to_lowercase())
                        || (exes.is_empty() && match_score(app, &program_name(&exe)) >= GOOD_MATCH))
            })
            .map(|p| p.pid)
            .collect();
        if victims.is_empty() {
            return Err(format!("No running app called {}.", quoted(app)));
        }
        let killed = victims.into_iter().filter(|pid| kill(*pid)).count();
        if killed == 0 {
            return Err(format!("Windows didn't let Natro force-close {name}."));
        }
        Ok(format!("Force-closed {name} ({killed} process{}).", if killed > 1 { "es" } else { "" }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn menu() -> (StartMenu, Arc<Mutex<Vec<String>>>) {
        let launched: Arc<Mutex<Vec<String>>> = Arc::default();
        let apps = [
            ("Spotify", "SpotifyAB.SpotifyMusic!Spotify"),
            ("Spotify Widget", "w1"),
            ("Visual Studio Code", "Microsoft.VisualStudioCode"),
            ("Google Chrome", "Chrome"),
            ("Calculator", "Microsoft.WindowsCalculator!App"),
            ("Steam", "steam.exe"),
        ]
        .map(|(name, app_id)| StartApp { name: name.into(), app_id: app_id.into() })
        .to_vec();
        let opened = launched.clone();
        let menu = StartMenu::with(
            Box::new(move || Ok(apps.clone())),
            Box::new(move |app_id| {
                opened.lock().unwrap().push(app_id.to_string());
                Ok(())
            }),
        );
        (menu, launched)
    }

    #[test]
    fn app_names_as_people_say_them() {
        let (menu, launched) = menu();
        assert_eq!(menu.open("spotify").unwrap(), "Opened Spotify."); // not "Spotify Widget"
        assert_eq!(menu.open("VS Code").unwrap(), "Opened Visual Studio Code.");
        assert_eq!(menu.open("chrome").unwrap(), "Opened Google Chrome.");
        assert_eq!(menu.open("calculater").unwrap(), "Opened Calculator."); // a misheard name
        assert_eq!(
            *launched.lock().unwrap(),
            [
                "SpotifyAB.SpotifyMusic!Spotify",
                "Microsoft.VisualStudioCode",
                "Chrome",
                "Microsoft.WindowsCalculator!App"
            ]
        );
        assert_eq!(
            menu.open("telegram").unwrap_err(),
            "No app called 'telegram' is installed on the PC. The closest names: steam, google chrome."
        );
        assert_eq!(match_score("steam", "Steam"), 1.0);
    }

    /// Python's scores for each (natro_pc/apps.py's match_score).
    #[test]
    fn the_same_scores_as_the_python_helper() {
        for (query, name, python) in [
            ("spotify", "Spotify Widget", 0.88),
            ("calculater", "Calculator", 0.675),
            ("telegram", "Steam", 0.46153846153846156),
            ("code", "Visual Studio Code", 1.0),
            ("studio", "Visual Studio Code", 0.86),
            ("Word", "Microsoft Word", 0.88),
        ] {
            assert_eq!(match_score(query, name), python, "{query:?} {name:?}");
        }
        assert_eq!(words("Hello, World! 123 VS-Code"), "hello world 123 vs code");
        assert_eq!(words("Straße ﬁle"), "strasse file");
    }

    fn window(app: &str, title: &str, exe: &str, hwnd: isize) -> Window {
        Window { hwnd, title: title.into(), exe: exe.into(), app: app.into() }
    }

    fn windows() -> Vec<Window> {
        vec![
            window("Google Chrome", "Spotify - Web Player - Google Chrome", r"C:\chrome\chrome.exe", 1),
            window("Spotify", "Yellow - Coldplay", r"C:\Apps\Spotify.exe", 2),
            window("Visual Studio Code", "app.py - Natro 2 - Visual Studio Code", r"C:\VS\Code.exe", 3),
            window("Visual Studio Code", "PLAN.md - Natro 2", r"C:\VS\Code.exe", 4),
        ]
    }

    #[test]
    fn finding_an_open_app() {
        // The app itself wins over a browser tab that mentions it.
        assert_eq!(find_open_app("spotify", &windows()).unwrap().0, "Spotify");
        let (name, its) = find_open_app("vs code", &windows()).unwrap();
        assert_eq!(name, "Visual Studio Code");
        assert_eq!(its.iter().map(|w| w.hwnd).collect::<Vec<_>>(), [3, 4]);
        assert_eq!(find_open_app("code", &windows()).unwrap().0, "Visual Studio Code"); // Code.exe
        // A window whose title names it.
        assert_eq!(find_open_app("web player", &windows()).unwrap().0, "Google Chrome");
        assert_eq!(
            find_open_app("discord", &windows()).unwrap_err(),
            "No open app called 'discord'. Open apps: Google Chrome, Spotify, Visual Studio Code."
        );
        assert_eq!(find_open_app("it's", &[]).unwrap_err(), "No open app called \"it's\". Open apps: none.");
    }

    #[test]
    fn list_open_apps_shows_no_window_titles() {
        // Titles can name documents and emails; the apps' names are enough to say what's open.
        let apps = Apps { menu: Arc::new(menu().0), windows: Box::new(windows) };
        assert_eq!(
            apps.run("list_open_apps", &json!({})).unwrap(),
            "Open apps: Google Chrome, Spotify, Visual Studio Code (2 windows)."
        );
        let none = Apps { menu: Arc::new(menu().0), windows: Box::new(Vec::new) };
        assert_eq!(none.run("list_open_apps", &json!({})).unwrap(), "No apps are open.");
        assert_eq!(none.run("open_app", &json!({})).unwrap_err(), "open_app needs the app's name.");
    }

    #[test]
    fn the_start_menus_list() {
        let listed = r#"[{"Name":"Spotify","AppID":"SpotifyAB.SpotifyMusic!Spotify"},
                         {"Name":"Uninstall Steam","AppID":"u1"},{"Name":"Python 3.13 Help","AppID":"h1"},
                         {"Name":"Helper","AppID":"x1"},{"Name":"Natro website","AppID":"https://natro.ge"},
                         {"Name":"Docs","AppID":"http://example.com"},{"Name":"Release Notes","AppID":"r1"}]"#;
        let names: Vec<String> = parse_start_apps(listed).unwrap().into_iter().map(|app| app.name).collect();
        assert_eq!(names, ["Spotify", "Helper"]); // "Helper" isn't "help" as a word
        // One app is an object; PowerShell may start with a byte order mark.
        assert_eq!(parse_start_apps("\u{feff}{\"Name\":\"Steam\",\"AppID\":\"steam.exe\"}").unwrap().len(), 1);
        assert!(parse_start_apps("").unwrap().is_empty());
    }

    #[test]
    fn natro_her_starters_and_what_she_started_are_spared() {
        let process = |pid, parent| Process { pid, parent, name: String::new() };
        // explorer (10) started Natro (20), who started a program (30); 40 is Spotify, also Explorer's.
        let running = [process(4, 0), process(10, 5), process(20, 10), process(30, 20), process(40, 10)];
        let spared = spared(20, &running);
        assert_eq!(spared, HashSet::from([10, 20, 30]));
    }

    /// By hand, on the PC: cargo test -- --ignored --nocapture reads_this_pcs_apps_and_windows
    #[test]
    #[ignore]
    fn reads_this_pcs_apps_and_windows() {
        let menu = StartMenu::new();
        let names = ["calculator", "calculater", "edge", "vs code", "paint", "word", "notepad", "settings", "telegram"];
        for name in names {
            let found = menu.find(name).map(|app| format!("{} | {}", app.name, app.app_id));
            println!("{name} -> {}", found.unwrap_or_else(Missing::message));
        }
        println!("{} apps", menu.apps.lock().unwrap().0.len());
        let apps = Apps::new(Arc::new(menu));
        println!("{}", apps.run("list_open_apps", &json!({})).unwrap());
        for window in open_windows() {
            println!("{} | {}", window.app, window.exe);
        }
    }
}
