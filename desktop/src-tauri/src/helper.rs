//! The Python helper (pc/natro_pc/helper.py): this PC's tools and Natro's voice
//! (Kokoro), from the Python PC app, until they're ported to Rust.
//!
//! It runs as `.venv\Scripts\python.exe -m natro_pc.helper` in NATRO_HOME, with
//! no console window, and talks JSON lines over stdin/stdout. Without it Natro
//! still works, only without the PC's tools and without speaking.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::process::{ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::{Value, json};
use tokio::sync::{oneshot, watch};

use crate::config::Config;

/// How long the brain waits for the tools before connecting without them.
const TOOLS_WAIT: Duration = Duration::from_secs(20);
/// A tool that takes longer than this has failed (file searches take up to 20 s).
const TOOL_WAIT: Duration = Duration::from_secs(60);
/// Finding an app by name (the first time, the Start menu is read: a second or two).
const APP_WAIT: Duration = Duration::from_secs(10);
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Tool calls waiting for their result, by id: whether it worked, the text, and a picture (or null).
type Waiting = Arc<Mutex<HashMap<String, oneshot::Sender<(bool, String, Value)>>>>;

pub struct Helper {
    stdin: Mutex<Option<ChildStdin>>,
    tools: watch::Receiver<Option<Vec<Value>>>,
    waiting: Waiting,
    ids: AtomicU64,
    /// The tray's "Speak replies".
    pub speaking: AtomicBool,
}

impl Helper {
    pub fn start(config: &Config) -> Helper {
        let (found_tools, tools) = watch::channel(None);
        let waiting: Waiting = Arc::default();
        let python = config.home.join(".venv").join("Scripts").join("python.exe");
        let child = Command::new(&python)
            .args(["-m", "natro_pc.helper"])
            .current_dir(&config.home)
            .env("NATRO_HOME", &config.home)
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
        let stdin = match child {
            Ok(mut child) => {
                let (stdout, stderr) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
                let pending = waiting.clone();
                thread::spawn(move || {
                    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                        let Ok(message) = serde_json::from_str::<Value>(&line) else {
                            continue;
                        };
                        match message["type"].as_str() {
                            Some("tools") => {
                                let _ = found_tools.send(message["tools"].as_array().cloned());
                            }
                            Some("tool_result" | "app") => {
                                let id = message["id"].as_str().unwrap_or_default();
                                if let Some(waiter) = pending.lock().unwrap().remove(id) {
                                    let result =
                                        message["result"].as_str().unwrap_or_default().to_string();
                                    let ok = message["ok"].as_bool().unwrap_or(false);
                                    let _ = waiter.send((ok, result, message["picture"].clone()));
                                }
                            }
                            _ => {}
                        }
                    }
                    log::warn!("the Python helper stopped");
                    let _ = found_tools.send(Some(Vec::new()));
                });
                thread::spawn(move || {
                    for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                        log::info!("helper: {line}");
                    }
                });
                child.stdin.take()
            }
            Err(e) => {
                log::warn!(
                    "the Python helper couldn't start ({}): {e}",
                    python.display()
                );
                None
            }
        };
        Helper {
            stdin: Mutex::new(stdin),
            tools,
            waiting,
            ids: AtomicU64::new(1),
            speaking: AtomicBool::new(true),
        }
    }

    /// The PC's tools for the brain's hello (none if the helper isn't there).
    pub async fn tools(&self) -> Vec<Value> {
        let mut tools = self.tools.clone();
        let found = tokio::time::timeout(TOOLS_WAIT, tools.wait_for(Option::is_some)).await;
        match found {
            Ok(Ok(tools)) => tools.clone().unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    /// Run one of the PC's tools: (ok, result for the brain, a picture for the chat or null).
    pub async fn run_tool(&self, id: &str, name: &str, args: &Value) -> (bool, String, Value) {
        let (done, result) = oneshot::channel();
        self.waiting.lock().unwrap().insert(id.to_string(), done);
        if !self.write(json!({"type": "tool", "id": id, "name": name, "args": args})) {
            self.waiting.lock().unwrap().remove(id);
            return (false, "The PC's tools aren't running.".into(), Value::Null);
        }
        match tokio::time::timeout(TOOL_WAIT, result).await {
            Ok(Ok(result)) => result,
            _ => {
                self.waiting.lock().unwrap().remove(id);
                (false, format!("{name} took too long on the PC."), Value::Null)
            }
        }
    }

    /// A Start menu app by name (as open_app finds it): Some(its AppID), Some(None) if
    /// it isn't installed, None if the helper couldn't be asked.
    pub async fn find_app(&self, name: &str) -> Option<Option<String>> {
        let id = format!("a{}", self.ids.fetch_add(1, Ordering::SeqCst));
        let (done, result) = oneshot::channel();
        self.waiting.lock().unwrap().insert(id.clone(), done);
        if !self.write(json!({"type": "app", "id": id, "name": name})) {
            self.waiting.lock().unwrap().remove(&id);
            return None;
        }
        match tokio::time::timeout(APP_WAIT, result).await {
            Ok(Ok((found, app_id, _))) => Some(found.then_some(app_id)),
            _ => {
                self.waiting.lock().unwrap().remove(&id);
                None
            }
        }
    }

    /// Speak, after anything already being said (unless "Speak replies" is off).
    pub fn say(&self, text: &str) {
        if self.speaking.load(Ordering::SeqCst) && !text.trim().is_empty() {
            self.write(json!({"type": "say", "text": text}));
        }
    }

    fn write(&self, message: Value) -> bool {
        let mut stdin = self.stdin.lock().unwrap();
        let Some(pipe) = stdin.as_mut() else {
            return false;
        };
        let ok = writeln!(pipe, "{message}")
            .and_then(|_| pipe.flush())
            .is_ok();
        if !ok {
            *stdin = None;
        }
        ok
    }
}
