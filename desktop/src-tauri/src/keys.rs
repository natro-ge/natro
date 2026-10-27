//! The keys in the Settings window, behind a PIN: Natro's API keys and settings in the
//! server's .env (the "Server" tab, through the brain: server/natro_agent/keys.py), and
//! this PC's own in .env here (the "This PC" tab).
//!
//! One PIN for both. Like the server, the PC keeps only a salted scrypt hash of it, in
//! tokens/settings-pin.json, so its own tab opens while Natro is offline; the server checks
//! the PIN again for hers. The first unlock chooses it (on both); if only one has it (the
//! other's file was deleted), unlocking with it gives it to the other. After FREE_TRIES
//! wrong tries each try has to wait, twice as long each time. Unlocked, the keys stay open
//! until UNLOCKED_FOR after they were last used, or until the window closes.
//!
//! A key is never shown whole: the server sends only its last four characters, and the
//! PC's are shown the same way. This PC's fields are few (PC_FIELDS): where Natro is, the
//! voice, the browser extension's token. The device token isn't one of them (the phone has
//! it built in). Saving them restarts the app, which reads .env when it starts. A key saved
//! on the server is saved here too if this PC's .env has it (the copy for the brain's tests).

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tauri::{AppHandle, Manager};
use tokio::sync::oneshot;

use crate::brain::Brain;

const FREE_TRIES: u32 = 5;
const FIRST_WAIT: Duration = Duration::from_secs(30);
const LONGEST_WAIT: Duration = Duration::from_secs(3600);
const UNLOCKED_FOR: Duration = Duration::from_secs(300);
/// The server's answer to a status, an unlock or a new PIN.
const ANSWER_WAIT: Duration = Duration::from_secs(15);
/// Saving: the server asks each provider about its new key first (each check takes up to 15 s).
const SAVE_WAIT: Duration = Duration::from_secs(45);
/// The page shows "Saved" before the app starts again.
const RESTART_AFTER: Duration = Duration::from_millis(1200);

/// Kokoro's speakers (models/kokoro-en-v0_19), by NATRO_VOICE.
const VOICES: [&str; 11] = [
    "American woman (af)",
    "American woman: Bella",
    "American woman: Nicole",
    "American woman: Sarah",
    "American woman: Sky",
    "American man: Adam",
    "American man: Michael",
    "British woman: Emma",
    "British woman: Isabella",
    "British man: George",
    "British man: Lewis",
];

struct PcField {
    key: &'static str,
    label: &'static str,
    secret: bool,
    note: &'static str,
    /// Why a value can't be saved, if it can't.
    check: fn(&str) -> Option<String>,
}

const PC_FIELDS: [PcField; 3] = [
    PcField {
        key: "NATRO_SERVER",
        label: "Natro's address",
        secret: false,
        note: "ws:// and the server's Tailscale address, port 8700",
        check: check_server,
    },
    PcField {
        key: "NATRO_VOICE",
        label: "Voice",
        secret: false,
        note: "Kokoro's voice for spoken replies",
        check: check_voice,
    },
    PcField {
        key: "PLAYWRIGHT_MCP_EXTENSION_TOKEN",
        label: "Browser extension token",
        secret: true,
        note: "Playwright Extension in Edge: Natro uses the browser with it",
        check: any_value,
    },
];

fn any_value(_: &str) -> Option<String> {
    None
}

fn check_server(value: &str) -> Option<String> {
    let rest = value.strip_prefix("ws://").or_else(|| value.strip_prefix("wss://"));
    match rest {
        Some(rest) if !rest.is_empty() => None,
        _ => Some("An address like ws://100.101.107.5:8700.".into()),
    }
}

fn check_voice(value: &str) -> Option<String> {
    match value.parse::<usize>() {
        Ok(n) if n < VOICES.len() => None,
        _ => Some("Not one of Kokoro's voices.".into()),
    }
}

pub struct Keys {
    home: PathBuf,
    state: Mutex<State>,
    /// The server's answers awaited, by id.
    waiting: Mutex<HashMap<String, oneshot::Sender<Value>>>,
    ids: AtomicU64,
}

#[derive(Default)]
struct State {
    failures: u32,
    not_before: Option<Instant>,
    /// When the keys were last used, while they're unlocked.
    used: Option<Instant>,
    /// The server's session, while hers are unlocked.
    session: Option<String>,
}

impl Keys {
    pub fn new(home: &Path) -> Keys {
        Keys {
            home: home.to_path_buf(),
            state: Mutex::default(),
            waiting: Mutex::default(),
            ids: AtomicU64::new(1),
        }
    }

    fn pin_file(&self) -> PathBuf {
        self.home.join("tokens").join("settings-pin.json")
    }

    fn env_file(&self) -> PathBuf {
        self.home.join(".env")
    }

    /// The brain's answer to one of ours (its "keys" message).
    pub fn answered(&self, message: &Value) {
        let id = message["id"].as_str().unwrap_or_default();
        if let Some(waiter) = self.waiting.lock().unwrap().remove(id) {
            let _ = waiter.send(message.clone());
        }
    }

    /// Asks the server; None if Natro is offline or doesn't answer in time.
    async fn ask(&self, app: &AppHandle, mut message: Value, wait: Duration) -> Option<Value> {
        let id = format!("k{}", self.ids.fetch_add(1, Ordering::SeqCst));
        message["type"] = json!("keys");
        message["id"] = json!(id);
        let (sender, answer) = oneshot::channel();
        self.waiting.lock().unwrap().insert(id.clone(), sender);
        let answer = if app.state::<Brain>().send_json(message) {
            tokio::time::timeout(wait, answer).await.ok().and_then(Result::ok)
        } else {
            None
        };
        self.waiting.lock().unwrap().remove(&id);
        answer
    }

    /// Whether the keys are unlocked (using them keeps them so a while longer).
    fn in_use(&self) -> bool {
        let mut state = self.state.lock().unwrap();
        match state.used {
            Some(used) if used.elapsed() <= UNLOCKED_FOR => {
                state.used = Some(Instant::now());
                true
            }
            _ => {
                state.used = None;
                state.session = None;
                false
            }
        }
    }

    /// The window closed (or the Lock link): here, and on the server.
    pub fn lock(&self, app: &AppHandle) {
        let session = {
            let mut state = self.state.lock().unwrap();
            state.used = None;
            state.session.take()
        };
        if let Some(session) = session {
            app.state::<Brain>().send_json(json!({"type": "keys", "id": "lock", "do": "lock", "session": session}));
        }
    }

    async fn status(&self, app: &AppHandle) -> Value {
        let here = self.pin_file().is_file();
        let there = match self.ask(app, json!({"do": "status"}), ANSWER_WAIT).await {
            Some(answer) => answer["pin"].as_bool().unwrap_or(false),
            None => false,
        };
        json!({"pin": here || there})
    }

    async fn unlock(&self, app: &AppHandle, pin: &str) -> Value {
        if let Some(wait) = self.waiting_for() {
            return json!({"ok": false, "message": format!("Too many wrong tries: wait {}.", duration(wait)),
                          "wait": wait.as_secs()});
        }
        if !is_pin(pin) {
            return json!({"ok": false, "message": "A PIN is 4 to 12 digits."});
        }
        let kept = read_pin(&self.pin_file());
        let right_here = kept.as_ref().map(|(salt, hash)| same(&hash_pin(pin, salt), hash));
        if right_here == Some(false) {
            return self.wrong();
        }
        let server = self.ask(app, json!({"do": "unlock", "pin": pin}), ANSWER_WAIT).await;
        let server_ok = server.as_ref().is_some_and(|answer| answer["ok"] == true);
        // No PIN here yet: the server's says whether this one is right; offline, it becomes the PIN.
        if kept.is_none() {
            if server.is_some() && !server_ok {
                return server.unwrap_or_default();
            }
            if let Err(e) = write_pin(&self.pin_file(), pin) {
                return json!({"ok": false, "message": format!("Couldn't keep the PIN: {e}")});
            }
        }
        {
            let mut state = self.state.lock().unwrap();
            state.failures = 0;
            state.used = Some(Instant::now());
            state.session = server
                .as_ref()
                .filter(|_| server_ok)
                .and_then(|answer| answer["session"].as_str().map(str::to_string));
        }
        let server_new = server.as_ref().is_some_and(|answer| answer["new_pin"] == true);
        let server = match server {
            Some(answer) if server_ok => json!({"ok": true, "fields": answer["fields"]}),
            Some(answer) => json!({"ok": false, "message": answer["message"]}),
            None => json!({"ok": false, "offline": true,
                           "message": "Natro is offline: her keys can be changed once she's back."}),
        };
        // Chosen now: here while Natro is offline, or on both.
        let new_pin = kept.is_none() && (server["offline"] == true || server_new);
        log::info!("the keys are unlocked");
        json!({"ok": true, "new_pin": new_pin, "pc": self.pc_fields(), "server": server})
    }

    fn waiting_for(&self) -> Option<Duration> {
        let state = self.state.lock().unwrap();
        state
            .not_before
            .and_then(|until| until.checked_duration_since(Instant::now()))
            .filter(|wait| !wait.is_zero())
    }

    fn wrong(&self) -> Value {
        let mut state = self.state.lock().unwrap();
        state.failures += 1;
        log::warn!("a wrong PIN for the keys ({} in a row)", state.failures);
        if state.failures >= FREE_TRIES {
            let wait = FIRST_WAIT
                .saturating_mul(1 << (state.failures - FREE_TRIES).min(10))
                .min(LONGEST_WAIT);
            state.not_before = Some(Instant::now() + wait);
            return json!({"ok": false, "message": format!("Wrong PIN. Wait {} before the next try.", duration(wait)),
                          "wait": wait.as_secs()});
        }
        let left = FREE_TRIES - state.failures;
        json!({"ok": false,
               "message": format!("Wrong PIN ({left} {} before a wait).", if left == 1 { "try" } else { "tries" })})
    }

    fn pc_fields(&self) -> Vec<Value> {
        let saved = parse_env(&fs::read_to_string(self.env_file()).unwrap_or_default());
        PC_FIELDS
            .iter()
            .map(|field| {
                let value = saved.get(field.key).cloned().unwrap_or_default();
                let mut shown = json!({"key": field.key, "label": field.label, "secret": field.secret,
                                       "set": !value.is_empty(), "note": field.note,
                                       "value": if field.secret { "" } else { value.as_str() },
                                       "hint": if field.secret { hint(&value) } else { String::new() }});
                if field.key == "NATRO_VOICE" {
                    shown["choices"] = json!(VOICES.iter().enumerate()
                        .map(|(n, name)| json!([n.to_string(), name])).collect::<Vec<_>>());
                    if value.is_empty() {
                        shown["value"] = json!("0");
                    }
                }
                shown
            })
            .collect()
    }

    /// "server": through the brain, then here too for keys this PC's .env has. "pc": here, then the app restarts.
    async fn save(&self, app: &AppHandle, target: &str, values: HashMap<String, String>) -> Value {
        if !self.in_use() {
            return json!({"ok": false, "locked": true, "message": "Locked: enter the PIN again."});
        }
        let values: HashMap<String, String> = values.into_iter().map(|(k, v)| (k, clean(&v))).collect();
        if target == "server" {
            let Some(session) = self.state.lock().unwrap().session.clone() else {
                return json!({"ok": false, "message": "Natro's keys are locked: enter the PIN again."});
            };
            let Some(answer) = self
                .ask(app, json!({"do": "save", "session": session, "values": values}), SAVE_WAIT)
                .await
            else {
                return json!({"ok": false, "message": "Natro didn't answer: nothing may have been saved."});
            };
            if answer["ok"] == true && answer["restarting"] == true {
                self.state.lock().unwrap().session = None; // she starts again without it
                let here = parse_env(&fs::read_to_string(self.env_file()).unwrap_or_default());
                let mirrored: HashMap<String, String> =
                    values.into_iter().filter(|(key, _)| here.contains_key(key)).collect();
                if !mirrored.is_empty() {
                    match save_env(&self.env_file(), &mirrored) {
                        Ok(()) => log::info!("also saved here: {:?}", mirrored.keys().collect::<Vec<_>>()),
                        Err(e) => log::warn!("couldn't save the new keys in this PC's .env too: {e}"),
                    }
                }
            }
            return answer;
        }
        let saved = parse_env(&fs::read_to_string(self.env_file()).unwrap_or_default());
        let mut errors = serde_json::Map::new();
        let mut changes = HashMap::new();
        for (key, value) in values {
            let problem = match PC_FIELDS.iter().find(|field| field.key == key) {
                None => Some("This can't be changed from the app.".to_string()),
                Some(_) if value.is_empty() => Some("Empty.".into()),
                Some(_) if value.contains(char::is_whitespace) => Some("No spaces.".into()),
                Some(field) => (field.check)(&value),
            };
            match problem {
                Some(problem) => {
                    errors.insert(key, json!(problem));
                }
                None if saved.get(&key) != Some(&value) => {
                    changes.insert(key, value);
                }
                None => {}
            }
        }
        if !errors.is_empty() {
            return json!({"ok": false, "message": "Nothing was saved.", "errors": errors});
        }
        if changes.is_empty() {
            return json!({"ok": true, "restarting": false, "fields": self.pc_fields()});
        }
        if let Err(e) = save_env(&self.env_file(), &changes) {
            return json!({"ok": false, "message": format!("Couldn't save .env: {e}")});
        }
        log::info!("this PC's .env changed ({:?}): starting again", changes.keys().collect::<Vec<_>>());
        let app = app.clone();
        thread::spawn(move || {
            thread::sleep(RESTART_AFTER);
            app.request_restart();
        });
        json!({"ok": true, "restarting": true, "fields": self.pc_fields()})
    }

    async fn new_pin(&self, app: &AppHandle, pin: &str) -> Value {
        if !self.in_use() {
            return json!({"ok": false, "locked": true, "message": "Locked: enter the PIN again."});
        }
        if !is_pin(pin) {
            return json!({"ok": false, "message": "A PIN is 4 to 12 digits."});
        }
        if let Err(e) = write_pin(&self.pin_file(), pin) {
            return json!({"ok": false, "message": format!("Couldn't keep the PIN: {e}")});
        }
        let session = self.state.lock().unwrap().session.clone();
        let there = match session {
            Some(session) => self.ask(app, json!({"do": "pin", "session": session, "pin": pin}), ANSWER_WAIT).await,
            None => None,
        };
        match there {
            Some(answer) if answer["ok"] == true => json!({"ok": true, "message": "The new PIN is set."}),
            _ => json!({"ok": true,
                        "message": "The new PIN is set on this PC; Natro keeps the old one until she's unlocked with it."}),
        }
    }
}

fn is_pin(pin: &str) -> bool {
    (4..=12).contains(&pin.len()) && pin.bytes().all(|b| b.is_ascii_digit())
}

/// Python's hashlib.scrypt(pin, salt=salt, n=2**14, r=8, p=1, dklen=32), as on the server.
fn hash_pin(pin: &str, salt: &[u8]) -> Vec<u8> {
    let params = scrypt::Params::new(14, 8, 1, 32).expect("valid scrypt parameters");
    let mut hash = vec![0; 32];
    scrypt::scrypt(pin.as_bytes(), salt, &params, &mut hash).expect("a valid length");
    hash
}

/// In the same time whatever the bytes.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0, |differ, (x, y)| differ | (x ^ y)) == 0
}

fn read_pin(path: &Path) -> Option<(Vec<u8>, Vec<u8>)> {
    let kept: Value = serde_json::from_str(&fs::read_to_string(path).ok()?).ok()?;
    Some((from_hex(kept["salt"].as_str()?)?, from_hex(kept["hash"].as_str()?)?))
}

fn write_pin(path: &Path, pin: &str) -> std::io::Result<()> {
    let mut salt = [0u8; 16];
    getrandom::fill(&mut salt).map_err(|e| std::io::Error::other(e.to_string()))?;
    if let Some(folder) = path.parent() {
        fs::create_dir_all(folder)?;
    }
    let kept = json!({"salt": to_hex(&salt), "hash": to_hex(&hash_pin(pin, &salt)),
                      "n": 16384, "r": 8, "p": 1, "dklen": 32});
    fs::write(path, kept.to_string())
}

fn to_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn from_hex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 {
        return None;
    }
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok()).collect()
}

/// A key as the window shows it: its last four characters, if it's long enough for that to say little.
fn hint(value: &str) -> String {
    match value.chars().count() {
        0 => String::new(),
        n if n >= 12 => format!("••••{}", value.chars().skip(n - 4).collect::<String>()),
        _ => "••••".into(),
    }
}

/// A pasted value: no spaces or quotes around it.
fn clean(value: &str) -> String {
    value.trim().trim_matches(|c| c == '"' || c == '\'').trim().to_string()
}

/// KEY=value lines, as config.rs reads them (the first of a key counts).
fn parse_env(text: &str) -> HashMap<String, String> {
    let mut values = HashMap::new();
    for line in text.lines().map(str::trim) {
        if line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            values.entry(key.trim().to_string()).or_insert_with(|| value.trim().to_string());
        }
    }
    values
}

/// The key a line sets, and whether it's commented out ("# GEMINI_API_KEY=").
fn line_key(line: &str) -> Option<(&str, bool)> {
    let line = line.trim();
    let (line, commented) = match line.strip_prefix('#') {
        Some(rest) => (rest.trim_start(), true),
        None => (line, false),
    };
    let key = line.split_once('=')?.0.trim();
    let is_key = key.starts_with(|c: char| c.is_ascii_alphabetic() || c == '_')
        && key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_');
    is_key.then_some((key, commented))
}

/// The .env text with these keys set, as the server does it (keys.py's rewrite_env): each line
/// changed in place (or a commented-out one, if that's all there is), others added at the end.
/// Windows line ends stay Windows line ends.
fn rewrite_env(text: &str, values: &HashMap<String, String>) -> String {
    let newline = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    let mut found: Vec<String> = Vec::new();
    for want_commented in [false, true] {
        for line in lines.iter_mut() {
            let Some((key, commented)) = line_key(line) else { continue };
            let Some(value) = values.get(key) else { continue };
            if commented == want_commented && !(commented && found.iter().any(|k| k == key)) {
                let key = key.to_string();
                *line = format!("{key}={value}");
                found.push(key);
            }
        }
    }
    let mut added: Vec<_> = values.iter().filter(|(key, _)| !found.contains(key)).collect();
    added.sort();
    lines.extend(added.into_iter().map(|(key, value)| format!("{key}={value}")));
    lines.join(newline) + newline
}

/// Sets these keys in the .env file; the old one stays as .env.bak.
fn save_env(path: &Path, values: &HashMap<String, String>) -> std::io::Result<()> {
    let old = fs::read_to_string(path).unwrap_or_default();
    let beside = |end: &str| {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        path.with_file_name(format!("{name}{end}"))
    };
    if !old.is_empty() {
        fs::write(beside(".bak"), &old)?;
    }
    let new = beside(".new");
    fs::write(&new, rewrite_env(&old, values))?;
    fs::rename(new, path)
}

fn duration(wait: Duration) -> String {
    let seconds = wait.as_secs().max(1);
    if seconds < 90 {
        format!("{seconds} seconds")
    } else {
        format!("{} minutes", (seconds + 30) / 60)
    }
}

#[tauri::command]
pub async fn keys_status(app: AppHandle) -> Value {
    app.state::<Keys>().status(&app).await
}

#[tauri::command]
pub async fn keys_unlock(app: AppHandle, pin: String) -> Value {
    app.state::<Keys>().unlock(&app, &pin).await
}

#[tauri::command]
pub async fn keys_save(app: AppHandle, target: String, values: HashMap<String, String>) -> Value {
    app.state::<Keys>().save(&app, &target, values).await
}

#[tauri::command]
pub async fn keys_pin(app: AppHandle, pin: String) -> Value {
    app.state::<Keys>().new_pin(&app, &pin).await
}

#[tauri::command]
pub fn keys_lock(app: AppHandle) {
    app.state::<Keys>().lock(&app);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn values(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    #[test]
    fn the_hash_is_the_servers() {
        // From the server: hash_pin("2468", bytes(16)).hex()
        assert_eq!(
            to_hex(&hash_pin("2468", &[0; 16])),
            "3d60e42f4e3d6bcf9a12a25ae7ea4648e2e913c7041a9bef24f4dd8b2bab7a87"
        );
    }

    #[test]
    fn a_kept_pin_is_checked() {
        let dir = std::env::temp_dir().join(format!("natro-keys-{}", std::process::id()));
        let file = dir.join("tokens").join("settings-pin.json");
        write_pin(&file, "13579").unwrap();
        let (salt, hash) = read_pin(&file).unwrap();
        assert!(same(&hash_pin("13579", &salt), &hash));
        assert!(!same(&hash_pin("13578", &salt), &hash));
        assert!(!fs::read_to_string(&file).unwrap().contains("13579"));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn pins_and_values() {
        assert!(is_pin("0000") && is_pin("123456789012"));
        assert!(!is_pin("123") && !is_pin("12a4") && !is_pin("1234567890123") && !is_pin("١٢٣٤"));
        assert_eq!(hint("tvly-dev-0123456789abcd"), "••••abcd");
        assert_eq!(hint("short"), "••••");
        assert_eq!(clean(" 'tvly-x' \n"), "tvly-x");
        assert_eq!(check_server("ws://100.1.2.3:8700"), None);
        assert!(check_server("http://x").is_some() && check_server("ws://").is_some());
        assert_eq!(check_voice("10"), None);
        assert!(check_voice("11").is_some() && check_voice("af").is_some());
    }

    #[test]
    fn lines_are_changed_in_place() {
        let text = "# PC\r\nNATRO_SERVER=ws://old:8700\r\n# PLAYWRIGHT_MCP_EXTENSION_TOKEN=\r\nNATRO_VOICE=0\r\n";
        let new = rewrite_env(
            text,
            &values(&[
                ("NATRO_SERVER", "ws://new:8700"),
                ("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "abc"),
                ("TAVILY_API_KEY", "tvly"),
            ]),
        );
        assert_eq!(
            new,
            "# PC\r\nNATRO_SERVER=ws://new:8700\r\nPLAYWRIGHT_MCP_EXTENSION_TOKEN=abc\r\nNATRO_VOICE=0\r\nTAVILY_API_KEY=tvly\r\n"
        );
        assert_eq!(parse_env(&new)["PLAYWRIGHT_MCP_EXTENSION_TOKEN"], "abc");
        assert_eq!(rewrite_env("A=1\n", &values(&[("A", "2")])), "A=2\n");
        // Prose in a comment isn't a setting.
        assert_eq!(line_key("# Optional: Gemini's free tier (aistudio.google.com/apikey)."), None);
    }

    #[test]
    fn saving_keeps_the_old_file_beside_it() {
        let dir = std::env::temp_dir().join(format!("natro-env-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let env = dir.join(".env");
        fs::write(&env, "NATRO_VOICE=0\n").unwrap();
        save_env(&env, &values(&[("NATRO_VOICE", "7")])).unwrap();
        assert_eq!(fs::read_to_string(&env).unwrap(), "NATRO_VOICE=7\n");
        assert_eq!(fs::read_to_string(dir.join(".env.bak")).unwrap(), "NATRO_VOICE=0\n");
        assert!(!dir.join(".env.new").exists());
        let _ = fs::remove_dir_all(dir);
    }
}
