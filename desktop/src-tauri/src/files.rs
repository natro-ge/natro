//! Files between the PC and the phone, passed along by Natro's server
//! (agent/natro_agent/files.py; the messages are in server.py).
//!
//! Files from the phone land in Downloads, and the reply box says so, with links
//! to open the file or show it in its folder. Files go to the phone from
//! Explorer's "Send to > Phone (Natro)" (`natro.exe --send-to phone <files>`,
//! handed to the running Natro), from the tray's "Send files to the phone…", or
//! when Natro is asked ("send the invoice to my phone": the brain asks for it
//! with file_request). The reply box shows how a send goes.
//!
//! A file goes in parts, and the sender sends on only while less than
//! WINDOW_BYTES of it is unanswered, so a big file never piles up in memory nor
//! holds a voice request up for long.

use std::collections::HashMap;
use std::fs;
use std::io::{Read, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};
use tauri::{AppHandle, Manager};
use tokio::sync::watch;
use windows::Win32::Foundation::E_FAIL;
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx,
    CoTaskMemFree, CoUninitialize, IPersistFile,
};
use windows::Win32::UI::Shell::{
    FOLDERID_Downloads, FOLDERID_SendTo, IShellLinkW, KF_FLAG_DEFAULT, SHGetKnownFolderPath,
    ShellExecuteW, ShellLink,
};
use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
use windows::core::{GUID, HSTRING, Interface, w};

use crate::brain::Brain;
use crate::reply::Reply;

/// The biggest file Natro passes on (the server's limit too).
pub const MAX_BYTES: u64 = 1 << 30;
const PART_BYTES: usize = 512 << 10;
const WINDOW_BYTES: u64 = 2 << 20;
/// A transfer that doesn't move on for this long has stalled.
const STALL: Duration = Duration::from_secs(60);
/// A transfer shows its progress only once it takes this long, then this often.
const SHOW_PROGRESS_AFTER: Duration = Duration::from_millis(1500);
const SHOW_PROGRESS_EVERY: Duration = Duration::from_millis(1000);
/// Explorer's "Send to" entry.
const SEND_TO_NAME: &str = "Phone (Natro).lnk";
/// Opening these would run something: a file from the phone is only shown in its folder.
const PROGRAMS: &[&str] = &[
    "exe", "bat", "cmd", "com", "ps1", "vbs", "vbe", "js", "jse", "wsf", "wsh", "msi", "msp", "scr",
    "pif", "reg", "lnk", "url", "hta", "cpl", "jar", "appref-ms", "psm1", "sh",
];

#[derive(Clone, PartialEq, Debug)]
enum Outcome {
    /// The server has it; waiting: the other device is offline and gets it when it's back.
    Sent { waiting: bool },
    Failed(String),
    Offline,
}

/// A file going up: how much the server has, and its last word on it.
#[derive(Clone, Default)]
struct Progress {
    acked: u64,
    outcome: Option<Outcome>,
}

struct Receiving {
    from: String,
    name: String,
    size: u64,
    file: fs::File,
    part: PathBuf,
    received: u64,
    started: Instant,
    shown: Option<Instant>,
}

#[derive(Default)]
struct State {
    sending: HashMap<String, watch::Sender<Progress>>,
    receiving: HashMap<String, Receiving>,
    /// Sent files on the server, on their way to the other device: (name, to).
    passed_on: HashMap<String, (String, String)>,
    /// The file whose piece is the next binary message (after its file_part).
    piece_of: Option<String>,
    /// Files to send once Natro is connected: (path, to).
    queued: Vec<(PathBuf, String)>,
}

pub struct Files {
    state: Mutex<State>,
    ids: AtomicU64,
    /// Files received since Natro started, for the reply box's links (natro:open/3).
    received: Mutex<Vec<PathBuf>>,
}

impl Files {
    pub fn new() -> Files {
        Files {
            state: Mutex::default(),
            ids: AtomicU64::new(1),
            received: Mutex::default(),
        }
    }

    // The connection (brain.rs)

    /// Connected: files asked for while offline go now.
    pub fn online(&self, app: &AppHandle) {
        let queued = std::mem::take(&mut self.state.lock().unwrap().queued);
        for (path, to) in queued {
            self.send(app, path, &to);
        }
    }

    /// The connection went: sends stop, files half received are deleted.
    pub fn lost(&self) {
        let mut state = self.state.lock().unwrap();
        state.piece_of = None;
        for progress in state.sending.values() {
            progress.send_modify(|p| p.outcome = Some(Outcome::Offline));
        }
        for (_, file) in state.receiving.drain() {
            drop(file.file);
            let _ = fs::remove_file(&file.part);
        }
    }

    /// A file_* message from the brain.
    pub fn handle(&self, app: &AppHandle, message: &Value) {
        let id = message["id"].as_str().unwrap_or_default().to_string();
        let text = |key: &str| message[key].as_str().unwrap_or_default().to_string();
        let mut state = self.state.lock().unwrap();
        match message["type"].as_str().unwrap_or_default() {
            "file_incoming" => {
                drop(state);
                self.incoming(app, &id, &text("from"), &text("name"), message["size"].as_u64());
            }
            "file_part" => state.piece_of = Some(id),
            "file_end" => {
                let file = state.receiving.remove(&id);
                drop(state);
                if let Some(file) = file {
                    self.received(app, &id, file);
                }
            }
            "file_got" => {
                if let (Some(progress), Some(count)) = (state.sending.get(&id), message["bytes"].as_u64()) {
                    progress.send_modify(|p| p.acked = p.acked.max(count));
                }
            }
            "file_sent" => {
                if let Some(progress) = state.sending.get(&id) {
                    let waiting = message["waiting"].as_bool().unwrap_or(false);
                    progress.send_modify(|p| p.outcome = Some(Outcome::Sent { waiting }));
                }
            }
            "file_delivered" => {
                if let Some((name, to)) = state.passed_on.remove(&id) {
                    let saved = Some(text("name")).filter(|n| !n.is_empty()).unwrap_or(name);
                    drop(state);
                    notice(app, &id, &format!("**{saved}** is on your {}, in Downloads.", label(&to)));
                }
            }
            "file_failed" => {
                let why = Some(text("message")).filter(|m| !m.is_empty()).unwrap_or("It didn't work.".into());
                if let Some(progress) = state.sending.get(&id) {
                    progress.send_modify(|p| p.outcome = Some(Outcome::Failed(why.clone())));
                }
                if let Some(file) = state.receiving.remove(&id) {
                    let _ = fs::remove_file(&file.part); // the server gave up sending it
                }
                if let Some((name, to)) = state.passed_on.remove(&id) {
                    drop(state);
                    notice(app, &id, &format!("**{name}** didn't reach your {}: {why}", label(&to)));
                }
            }
            "file_request" => {
                drop(state);
                self.start(app, id, PathBuf::from(text("path")), text("to"), true);
            }
            _ => {}
        }
    }

    /// The next piece of a file coming down.
    pub fn piece(&self, app: &AppHandle, bytes: &[u8]) {
        let mut state = self.state.lock().unwrap();
        let Some(id) = state.piece_of.take() else { return };
        let Some(file) = state.receiving.get_mut(&id) else { return };
        let written = if file.received + bytes.len() as u64 > file.size {
            Err("it was bigger than it said".to_string())
        } else {
            file.file.write_all(bytes).map_err(|e| e.to_string())
        };
        if let Err(e) = written {
            let file = state.receiving.remove(&id).expect("it's there");
            drop(state);
            return self.refuse(app, &id, file, &e);
        }
        file.received += bytes.len() as u64;
        let (received, size) = (file.received, file.size);
        let show = due(file.started, &mut file.shown);
        let title = format!("Receiving **{}** from your {}", file.name, label(&file.from));
        drop(state);
        brain(app, json!({"type": "file_got", "id": id, "bytes": received}));
        if show {
            notice(app, &id, &format!("{title}… {}%", percent(received, size)));
        }
    }

    // Files coming down

    fn incoming(&self, app: &AppHandle, id: &str, from: &str, name: &str, size: Option<u64>) {
        let name = safe_name(name);
        let Some(size) = size.filter(|size| *size <= MAX_BYTES) else {
            brain(app, failed(id, "Files can be at most 1 GB."));
            return;
        };
        let opened = downloads().and_then(|folder| {
            let part = folder.join(format!(".natro-{id}.part"));
            fs::File::create(&part).map(|file| (part, file)).map_err(|e| e.to_string())
        });
        match opened {
            Ok((part, file)) => {
                let receiving = Receiving {
                    from: from.into(),
                    name,
                    size,
                    file,
                    part,
                    received: 0,
                    started: Instant::now(),
                    shown: None,
                };
                self.state.lock().unwrap().receiving.insert(id.into(), receiving);
            }
            Err(e) => brain(app, failed(id, &format!("The PC couldn't save {name}: {e}"))),
        }
    }

    fn received(&self, app: &AppHandle, id: &str, file: Receiving) {
        let Receiving { from, name, size, file: handle, part, received, .. } = file;
        let saved = handle
            .sync_all()
            .map_err(|e| e.to_string())
            .and_then(|_| {
                drop(handle);
                if received != size {
                    return Err(format!("only {} of {} arrived", human_size(received), human_size(size)));
                }
                let path = unique(part.parent().expect("in Downloads"), &name);
                fs::rename(&part, &path).map(|_| path).map_err(|e| e.to_string())
            });
        let path = match saved {
            Ok(path) => path,
            Err(e) => {
                let _ = fs::remove_file(&part);
                brain(app, failed(id, &format!("The PC couldn't save {name}: {e}")));
                return notice(app, id, &format!("Couldn't save **{name}** from your {}: {e}", label(&from)));
            }
        };
        let saved_name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
        brain(app, json!({"type": "file_saved", "id": id, "name": saved_name}));
        log::info!("got {saved_name} from the {from}");
        let number = {
            let mut received = self.received.lock().unwrap();
            received.push(path.clone());
            received.len() - 1
        };
        let open = if is_program(&path) { String::new() } else { format!("[Open](natro:open/{number}) · ") };
        notice(
            app,
            id,
            &format!(
                "**{saved_name}** came from your {}. It's in Downloads.\n\n{open}[Show in folder](natro:show/{number})",
                label(&from)
            ),
        );
    }

    fn refuse(&self, app: &AppHandle, id: &str, file: Receiving, why: &str) {
        drop(file.file);
        let _ = fs::remove_file(&file.part);
        brain(app, failed(id, &format!("The PC couldn't save {}: {why}", file.name)));
        notice(app, id, &format!("Couldn't save **{}** from your {}: {why}", file.name, label(&file.from)));
    }

    /// The reply box's link to a file that came: open it, or show it in its folder.
    pub fn open_received(&self, link: &str) {
        let Some((action, number)) = link.strip_prefix("natro:").and_then(|rest| rest.split_once('/')) else {
            return;
        };
        let Some(path) = number
            .parse::<usize>()
            .ok()
            .and_then(|n| self.received.lock().unwrap().get(n).cloned())
        else {
            return;
        };
        match action {
            "open" if !is_program(&path) => unsafe {
                ShellExecuteW(None, w!("open"), &HSTRING::from(path.as_os_str()), None, None, SW_SHOWNORMAL);
            },
            "show" => {
                let _ = Command::new("explorer.exe")
                    .raw_arg(format!("/select,\"{}\"", path.display()))
                    .spawn();
            }
            _ => {}
        }
    }

    // Files going up

    /// Send files to a device (Explorer's "Send to", the tray). Waits for the connection if Natro is offline.
    pub fn send_paths(&self, app: &AppHandle, paths: Vec<PathBuf>, to: &str) {
        if !app.state::<Brain>().online() {
            let names: Vec<String> = paths.iter().map(|p| display_name(p)).collect();
            notice(app, "queued", &format!("Natro is offline: {} will go to your {} once she's back.",
                names.join(", "), label(to)));
            self.state.lock().unwrap().queued.extend(paths.into_iter().map(|p| (p, to.to_string())));
            return;
        }
        for path in paths {
            self.send(app, path, to);
        }
    }

    fn send(&self, app: &AppHandle, path: PathBuf, to: &str) {
        let millis = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis();
        let id = format!("pc-{millis:x}-{}", self.ids.fetch_add(1, Ordering::SeqCst));
        self.start(app, id, path, to.to_string(), false);
    }

    /// Send one file in the background. requested: the brain asked for it, and hears why if it can't go.
    fn start(&self, app: &AppHandle, id: String, path: PathBuf, to: String, requested: bool) {
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            let files = app.state::<Arc<Files>>();
            let name = display_name(&path);
            let outcome = match check(&path) {
                Ok(size) => files.upload(&app, &id, &path, &name, size, &to).await,
                Err(why) => {
                    if requested {
                        brain(&app, failed(&id, &why));
                    }
                    Outcome::Failed(why)
                }
            };
            files.state.lock().unwrap().sending.remove(&id);
            log::info!("sending {name} to the {to}: {outcome:?}");
            let there = label(&to);
            let said = match outcome {
                Outcome::Sent { waiting: true } => {
                    format!("**{name}** will reach your {there} when it's back online.")
                }
                Outcome::Sent { waiting: false } => {
                    files.state.lock().unwrap().passed_on.insert(id.clone(), (name, to));
                    return; // file_delivered says when it's there
                }
                Outcome::Failed(why) => format!("Couldn't send **{name}**: {why}"),
                Outcome::Offline => format!("Couldn't send **{name}**: the connection to Natro went."),
            };
            notice(&app, &id, &said);
        });
    }

    async fn upload(&self, app: &AppHandle, id: &str, path: &Path, name: &str, size: u64, to: &str) -> Outcome {
        let (progress, mut seen) = watch::channel(Progress::default());
        self.state.lock().unwrap().sending.insert(id.to_string(), progress);
        let mut file = match fs::File::open(path) {
            Ok(file) => file,
            Err(e) => return Outcome::Failed(e.to_string()),
        };
        let offer = json!({"type": "file_offer", "id": id, "to": to, "name": name, "size": size});
        if !app.state::<Brain>().send_json(offer) {
            return Outcome::Offline;
        }
        let (started, mut shown) = (Instant::now(), None);
        let mut buffer = vec![0u8; PART_BYTES];
        let mut sent = 0u64;
        loop {
            // Room to send on, or the server's last word.
            let room = seen.wait_for(|p| p.outcome.is_some() || sent < p.acked + WINDOW_BYTES);
            match tokio::time::timeout(STALL, room).await {
                Ok(Ok(p)) if p.outcome.is_none() => {}
                Ok(Ok(p)) => return p.outcome.clone().expect("checked"),
                _ => return Outcome::Offline,
            }
            let count = match read_up_to(&mut file, &mut buffer) {
                Ok(0) => break,
                Ok(count) => count,
                Err(e) => {
                    brain(app, failed(id, &format!("The PC couldn't read {name}: {e}")));
                    return Outcome::Failed(format!("the PC couldn't read it: {e}"));
                }
            };
            let part = json!({"type": "file_part", "id": id});
            if !app.state::<Brain>().send_piece(part, buffer[..count].to_vec()) {
                return Outcome::Offline;
            }
            sent += count as u64;
            if due(started, &mut shown) {
                notice(app, id, &format!("Sending **{name}** to your {}… {}%", label(to), percent(sent, size)));
            }
        }
        if !app.state::<Brain>().send_json(json!({"type": "file_end", "id": id})) {
            return Outcome::Offline;
        }
        match tokio::time::timeout(STALL, seen.wait_for(|p| p.outcome.is_some())).await {
            Ok(Ok(p)) => p.outcome.clone().expect("checked"),
            _ => Outcome::Offline,
        }
    }
}

/// `natro.exe --send-to phone <files>`, from Explorer's "Send to": the files go to the phone.
pub fn from_command_line(app: &AppHandle, args: &[String]) {
    if let Some((to, paths)) = send_to(args) {
        app.state::<Arc<Files>>().send_paths(app, paths, &to);
    }
}

/// The device and the files in `--send-to phone <files>`, if that's what the command line says.
fn send_to(args: &[String]) -> Option<(String, Vec<PathBuf>)> {
    let at = args.iter().position(|arg| arg == "--send-to")?;
    let to = args.get(at + 1)?.clone();
    let paths: Vec<PathBuf> = args[at + 2..].iter().map(PathBuf::from).collect();
    (!paths.is_empty()).then_some((to, paths))
}

/// Puts "Phone (Natro)" in Explorer's "Send to" menu, pointing at this program.
pub fn add_to_send_to() {
    fn make() -> windows::core::Result<PathBuf> {
        let exe = std::env::current_exe().map_err(|e| windows::core::Error::new(E_FAIL, e.to_string()))?;
        let shortcut = known_folder(&FOLDERID_SendTo)?.join(SEND_TO_NAME);
        unsafe {
            let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)?;
            link.SetPath(&HSTRING::from(exe.as_os_str()))?;
            link.SetArguments(w!("--send-to phone"))?;
            link.SetDescription(w!("Send to your phone with Natro"))?;
            link.SetIconLocation(&HSTRING::from(exe.as_os_str()), 0)?;
            link.cast::<IPersistFile>()?.Save(&HSTRING::from(shortcut.as_os_str()), true)?;
        }
        Ok(shortcut)
    }
    // On its own thread, with COM set up as the shell's objects need.
    thread::spawn(|| {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        }
        match make() {
            Ok(shortcut) => log::info!("Explorer's Send to has {}", shortcut.display()),
            Err(e) => log::warn!("couldn't add Natro to Explorer's Send to: {e}"),
        }
        unsafe { CoUninitialize() };
    });
}

// Helpers

/// Shows a word about a file in the reply box (each file's replaces its last).
fn notice(app: &AppHandle, id: &str, text: &str) {
    app.state::<Reply>().notice(app, &format!("file-{id}"), text);
}

fn brain(app: &AppHandle, message: Value) {
    app.state::<Brain>().send_json(message);
}

fn failed(id: &str, message: &str) -> Value {
    json!({"type": "file_failed", "id": id, "message": message})
}

/// The file's size, or why it can't be sent.
fn check(path: &Path) -> Result<u64, String> {
    let name = display_name(path);
    match fs::metadata(path) {
        Ok(meta) if meta.is_dir() => Err(format!("{name} is a folder; Natro sends files.")),
        Ok(meta) if meta.len() > MAX_BYTES => {
            Err(format!("{name} is {}; files can be at most 1 GB.", human_size(meta.len())))
        }
        Ok(meta) => Ok(meta.len()),
        Err(_) => Err(format!("There's no file {} on the PC.", path.display())),
    }
}

/// Reads until the buffer is full or the file ends.
fn read_up_to(file: &mut fs::File, buffer: &mut [u8]) -> std::io::Result<usize> {
    let mut filled = 0;
    while filled < buffer.len() {
        match file.read(&mut buffer[filled..])? {
            0 => break,
            count => filled += count,
        }
    }
    Ok(filled)
}

/// Whether a transfer's progress should show now: once it's taken a while, then every so often.
fn due(started: Instant, shown: &mut Option<Instant>) -> bool {
    let now = Instant::now();
    let ready = match shown {
        None => now.duration_since(started) >= SHOW_PROGRESS_AFTER,
        Some(last) => now.duration_since(*last) >= SHOW_PROGRESS_EVERY,
    };
    if ready {
        *shown = Some(now);
    }
    ready
}

fn percent(done: u64, size: u64) -> u64 {
    if size == 0 { 100 } else { done * 100 / size }
}

fn label(device: &str) -> &str {
    if device == "pc" { "PC" } else { device }
}

fn display_name(path: &Path) -> String {
    path.file_name().unwrap_or(path.as_os_str()).to_string_lossy().to_string()
}

fn is_program(path: &Path) -> bool {
    let extension = path.extension().unwrap_or_default().to_string_lossy().to_lowercase();
    PROGRAMS.contains(&extension.as_str())
}

/// The Downloads folder (wherever it was moved to).
fn downloads() -> Result<PathBuf, String> {
    known_folder(&FOLDERID_Downloads).map_err(|e| e.to_string())
}

fn known_folder(id: &GUID) -> windows::core::Result<PathBuf> {
    unsafe {
        let path = SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, None)?;
        let folder = path.to_string().map(PathBuf::from);
        CoTaskMemFree(Some(path.0 as _));
        folder.map_err(|e| windows::core::Error::new(E_FAIL, e.to_string()))
    }
}

/// name in folder, or "name (1).ext", "name (2).ext"… if that's taken.
fn unique(folder: &Path, name: &str) -> PathBuf {
    let first = folder.join(name);
    if !first.exists() {
        return first;
    }
    let (stem, extension) = match name.rfind('.') {
        Some(dot) if dot > 0 => (&name[..dot], &name[dot..]),
        _ => (name, ""),
    };
    (1..)
        .map(|n| folder.join(format!("{stem} ({n}){extension}")))
        .find(|path| !path.exists())
        .expect("some number is free")
}

const RESERVED_NAMES: &[&str] = &[
    "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
];
const LONGEST_NAME: usize = 150;

/// A file name that's safe to save: no folders, no characters Windows refuses, no
/// reserved names (as the server's safe_name in agent/natro_agent/files.py).
pub fn safe_name(name: &str) -> String {
    let last = name.rsplit(['/', '\\']).next().unwrap_or_default();
    let cleaned: String = last
        .chars()
        .map(|c| if c.is_control() || "<>:\"/\\|?*".contains(c) { '_' } else { c })
        .collect();
    let mut safe = cleaned.trim_matches([' ', '.']).to_string();
    if RESERVED_NAMES.contains(&safe.split('.').next().unwrap_or_default().to_lowercase().as_str()) {
        safe = format!("_{safe}");
    }
    if safe.chars().count() > LONGEST_NAME {
        let extension = safe.rsplit_once('.').map(|(_, e)| e.to_string()).filter(|e| e.chars().count() <= 10);
        safe = match extension {
            Some(extension) => {
                let stem: String = safe.chars().take(LONGEST_NAME - extension.chars().count() - 1).collect();
                format!("{stem}.{extension}")
            }
            None => safe.chars().take(LONGEST_NAME).collect(),
        };
    }
    if safe.is_empty() { "file".into() } else { safe }
}

pub fn human_size(size: u64) -> String {
    let mut value = size as f64;
    for unit in ["bytes", "KB", "MB"] {
        if value < 1024.0 {
            return if unit == "bytes" { format!("{size} bytes") } else { format!("{value:.1} {unit}") };
        }
        value /= 1024.0;
    }
    format!("{value:.2} GB")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The same cases as the server's (agent/tests/test_files.py) and the phone's.
    #[test]
    fn names_are_safe_to_save() {
        let cases = [
            ("report.pdf", "report.pdf".to_string()),
            ("..\\..\\Windows\\evil.exe", "evil.exe".into()),
            ("/etc/passwd", "passwd".into()),
            ("a<b>c:d|e?.txt", "a_b_c_d_e_.txt".into()),
            ("CON.txt", "_CON.txt".into()),
            ("  .hidden. ", "hidden".into()),
            ("", "file".into()),
            (&("x".repeat(300) + ".jpg"), "x".repeat(146) + ".jpg"),
        ];
        for (name, safe) in cases {
            assert_eq!(safe_name(name), safe, "{name}");
        }
    }

    #[test]
    fn sizes_read_well() {
        let sizes: Vec<String> = [8, 2048, 5 * (1 << 20) + (1 << 19), 3 << 30].into_iter().map(human_size).collect();
        assert_eq!(sizes, ["8 bytes", "2.0 KB", "5.5 MB", "3.00 GB"]);
    }

    #[test]
    fn a_taken_name_gets_a_number() {
        let folder = std::env::temp_dir().join(format!("natro-files-test-{}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        fs::write(folder.join("a.pdf"), b"").unwrap();
        fs::write(folder.join("a (1).pdf"), b"").unwrap();
        assert_eq!(unique(&folder, "a.pdf"), folder.join("a (2).pdf"));
        assert_eq!(unique(&folder, "b.pdf"), folder.join("b.pdf"));
        fs::write(folder.join("README"), b"").unwrap();
        assert_eq!(unique(&folder, "README"), folder.join("README (1)"));
        fs::remove_dir_all(&folder).unwrap();
    }

    #[test]
    fn the_command_line_from_send_to() {
        let args = |list: &[&str]| list.iter().map(|arg| arg.to_string()).collect::<Vec<_>>();
        let (to, paths) = send_to(&args(&["natro.exe", "--send-to", "phone", "C:\\a b.pdf", "C:\\c.jpg"])).unwrap();
        assert_eq!(to, "phone");
        assert_eq!(paths, [PathBuf::from("C:\\a b.pdf"), PathBuf::from("C:\\c.jpg")]);
        assert!(send_to(&args(&["natro.exe"])).is_none());
        assert!(send_to(&args(&["natro.exe", "--send-to", "phone"])).is_none());
    }

    #[test]
    fn programs_are_never_opened() {
        assert!(is_program(Path::new("C:\\x\\setup.EXE")));
        assert!(!is_program(Path::new("C:\\x\\photo.jpg")));
    }
}
