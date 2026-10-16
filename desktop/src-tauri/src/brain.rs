//! The connection to Natro's brain (agent/natro_agent/server.py, on the VPS over
//! Tailscale), as the device "pc", with the same messages as the Python PC app,
//! and files to and from the phone (files.rs). It reconnects whenever the
//! connection is lost.
//!
//! What goes out is queued in bundles sent whole, so a message and the bytes that
//! belong to it (a recording, a piece of a file) are never split by another.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tauri::{AppHandle, Manager};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;

use crate::chat::Chat;
use crate::config::Config;
use crate::dock::Dock;
use crate::files::Files;
use crate::helper::Helper;
use crate::icons::Icons;
use crate::reply::Reply;
use crate::tray;
use crate::verbose::Verbose;

const DEFAULT_SERVER: &str = "ws://127.0.0.1:8700";
const RETRY: Duration = Duration::from_secs(5);
/// After the brain refused the device token, try again less often.
const RETRY_REFUSED: Duration = Duration::from_secs(60);
const WELCOME_WAIT: Duration = Duration::from_secs(15);
/// Recordings are sent whole; the brain takes up to 16 MB.
const MAX_MESSAGE_BYTES: usize = 16 << 20;

/// Messages sent one after the other, with nothing in between.
type Bundle = Vec<Message>;

pub struct Brain {
    outgoing: mpsc::UnboundedSender<Bundle>,
    online: Arc<AtomicBool>,
    ids: AtomicU64,
    verbose: Arc<Verbose>,
}

impl Brain {
    pub fn start(
        app: AppHandle,
        config: &Config,
        helper: Arc<Helper>,
        verbose: Arc<Verbose>,
    ) -> Brain {
        let (outgoing, mut to_send) = mpsc::unbounded_channel();
        let online = Arc::new(AtomicBool::new(false));
        let url = config
            .get("NATRO_SERVER")
            .unwrap_or_else(|| DEFAULT_SERVER.into());
        let token = config.get("NATRO_DEVICE_TOKEN").unwrap_or_default();
        let (flag, replies) = (online.clone(), outgoing.clone());
        tauri::async_runtime::spawn(async move {
            loop {
                let ended =
                    connect(&app, &url, &token, &helper, &flag, &replies, &mut to_send).await;
                flag.store(false, Ordering::SeqCst);
                tray::show_connection(&app, false);
                app.state::<Arc<Files>>().lost();
                app.state::<Chat>().lost(&app);
                let wait = match ended {
                    Ended::Refused => {
                        log::error!("Natro refused this device: check NATRO_DEVICE_TOKEN in .env");
                        RETRY_REFUSED
                    }
                    Ended::Lost(why) => {
                        log::warn!("no connection to Natro at {url}: {why}");
                        RETRY
                    }
                };
                tokio::time::sleep(wait).await;
                while to_send.try_recv().is_ok() {} // not for the next connection
            }
        });
        Brain {
            outgoing,
            online,
            ids: AtomicU64::new(1),
            verbose,
        }
    }

    pub fn online(&self) -> bool {
        self.online.load(Ordering::SeqCst)
    }

    /// Send a recording: a request, or the spoken answer to the brain's question.
    /// The request's id, or None when offline.
    pub fn send_audio(&self, pcm: &[i16], answer_to: Option<&str>) -> Option<String> {
        if !self.online() {
            return None;
        }
        let id = format!("r{}", self.ids.fetch_add(1, Ordering::SeqCst));
        let mut message = json!({"type": "audio", "id": id});
        if let Some(question) = answer_to {
            message["answer_to"] = json!(question);
        }
        self.verbose.sent(&id, Some(pcm), None, answer_to);
        let bytes: Vec<u8> = pcm.iter().flat_map(|s| s.to_le_bytes()).collect();
        let _ = self
            .outgoing
            .send(vec![json_message(message), Message::Binary(bytes.into())]);
        Some(id)
    }

    /// A typed request (English). The request's id, or None when offline.
    pub fn send_text(&self, text: &str) -> Option<String> {
        if !self.online() {
            return None;
        }
        let id = format!("r{}", self.ids.fetch_add(1, Ordering::SeqCst));
        self.verbose.sent(&id, None, Some(text), None);
        let _ = self.outgoing.send(vec![json_message(
            json!({"type": "text", "id": id, "text": text}),
        )]);
        Some(id)
    }

    /// A yes or no to the brain's question, by key.
    pub fn send_answer(&self, question: &str, yes: bool) {
        let _ = self.outgoing.send(vec![json_message(
            json!({"type": "answer", "id": question, "yes": yes}),
        )]);
    }

    /// Any other message (files.rs). False when offline.
    pub fn send_json(&self, message: Value) -> bool {
        self.online() && self.outgoing.send(vec![json_message(message)]).is_ok()
    }

    /// A message and the bytes after it (a file_part and its piece). False when offline.
    pub fn send_piece(&self, message: Value, bytes: Vec<u8>) -> bool {
        self.online()
            && self
                .outgoing
                .send(vec![json_message(message), Message::Binary(bytes.into())])
                .is_ok()
    }
}

enum Ended {
    Lost(String),
    Refused,
}

fn json_message(value: Value) -> Message {
    Message::Text(value.to_string().into())
}

async fn connect(
    app: &AppHandle,
    url: &str,
    token: &str,
    helper: &Arc<Helper>,
    online: &AtomicBool,
    replies: &mpsc::UnboundedSender<Bundle>,
    to_send: &mut mpsc::UnboundedReceiver<Bundle>,
) -> Ended {
    let config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig::default()
        .max_message_size(Some(MAX_MESSAGE_BYTES))
        .max_frame_size(Some(MAX_MESSAGE_BYTES));
    let (mut ws, _) =
        match tokio_tungstenite::connect_async_with_config(url, Some(config), false).await {
            Ok(connected) => connected,
            Err(e) => return Ended::Lost(e.to_string()),
        };
    let tools = helper.tools().await;
    let hello = json!({"type": "hello", "device": "pc", "label": "PC", "token": token, "tools": tools,
                       "files": true});
    if let Err(e) = ws.send(json_message(hello)).await {
        return Ended::Lost(e.to_string());
    }
    match tokio::time::timeout(WELCOME_WAIT, ws.next()).await {
        Ok(Some(Ok(Message::Text(text)))) => {
            let welcome: Value = serde_json::from_str(&text).unwrap_or_default();
            if welcome["type"] != "welcome" {
                return Ended::Lost("Natro didn't welcome this device".into());
            }
            online.store(true, Ordering::SeqCst);
            log::info!("connected to Natro at {url} with {} PC tools", tools.len());
            tray::show_connection(app, true);
            tray::show_spending(app, &welcome);
            app.state::<Arc<Files>>().online(app);
        }
        Ok(Some(Ok(Message::Close(Some(frame))))) if frame.code == CloseCode::Policy => {
            return Ended::Refused;
        }
        Ok(Some(Err(e))) => return Ended::Lost(e.to_string()),
        _ => return Ended::Lost("no welcome".into()),
    }
    loop {
        tokio::select! {
            incoming = ws.next() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    if let Ok(message) = serde_json::from_str::<Value>(&text) {
                        handle(app, helper, replies, message);
                    }
                }
                Some(Ok(Message::Binary(bytes))) => app.state::<Arc<Files>>().piece(app, &bytes),
                Some(Ok(Message::Close(frame))) => {
                    if frame.is_some_and(|f| f.code == CloseCode::Policy) {
                        return Ended::Refused;
                    }
                    return Ended::Lost("closed".into());
                }
                Some(Ok(_)) => {}
                Some(Err(e)) => return Ended::Lost(e.to_string()),
                None => return Ended::Lost("closed".into()),
            },
            Some(bundle) = to_send.recv() => {
                for message in bundle {
                    if let Err(e) = ws.send(message).await {
                        return Ended::Lost(e.to_string());
                    }
                }
            }
        }
    }
}

/// A message from the brain (see the protocol in agent/natro_agent/server.py).
fn handle(
    app: &AppHandle,
    helper: &Arc<Helper>,
    replies: &mpsc::UnboundedSender<Bundle>,
    message: Value,
) {
    let id = message["id"].as_str().unwrap_or_default().to_string();
    let text = |key: &str| message[key].as_str().unwrap_or_default().to_string();
    let dock = app.state::<Dock>();
    let chat = app.state::<Chat>();
    let verbose = app.state::<Arc<Verbose>>();
    match message["type"].as_str().unwrap_or_default() {
        "heard" => {
            log::info!("heard: {} / {}", text("georgian"), text("english"));
            verbose.event(
                &id,
                "heard",
                json!({"georgian": text("georgian"), "english": text("english")}),
            );
            chat.heard(app, &id, &text("georgian"), &text("english"));
        }
        "reply" => {
            log::info!(
                "reply ({}, ${}): {}",
                text("model"),
                message["cost"],
                text("text")
            );
            verbose.event(
                &id,
                "reply",
                json!({"text": text("text"), "model": text("model"), "cost": message["cost"],
                       "seconds": message["seconds"], "tools": message["tools"]}),
            );
            // The chat's own requests are answered in writing only.
            if !chat.mine(&id) {
                helper.say(&text("text"));
            }
            tray::show_spending(app, &message);
            dock.replied(app, &id);
            // While the chat is open, it shows her replies; else the reply box does.
            if !chat.is_open() {
                app.state::<Reply>().replied(app, &id, &text("text"));
            }
            chat.replied(app, &id, &text("text"));
        }
        "step" => {
            let (label, icon) = (text("text"), text("icon"));
            log::info!("step: {label}");
            verbose.event(&id, "step", json!({"text": label, "app": message["app"]}));
            // The app's own icon: at once if it's known, else once it's found.
            let app_name = message["app"].as_str().map(str::to_string);
            let known = app_name
                .as_deref()
                .and_then(|name| app.state::<Icons>().known(name));
            let image = known.clone().flatten();
            let docked = dock.step(app, &id, &label, &icon, image.clone());
            let chatted = chat.step(app, &id, &label, &icon, image);
            if let (true, None, Some(name)) = (docked || chatted, known, app_name) {
                let app = app.clone();
                thread::spawn(move || {
                    if let Some(image) = app.state::<Icons>().find(&name) {
                        app.state::<Dock>().step_image(&app, &id, &label, image.clone());
                        app.state::<Chat>().step_image(&app, &id, &label, image);
                    }
                });
            }
        }
        "confirm" => {
            log::info!("asking: {} ({})", text("question"), text("details"));
            verbose.event(
                &id,
                "asked",
                json!({"question": text("question"), "details": text("details")}),
            );
            // About one of the chat's requests, while it's open: a bubble there. Else the dock asks, out loud.
            if !chat.asked(app, &id, &text("question"), dock.waiting()) {
                helper.say(&text("question"));
                dock.asked(app, &id, &text("question"));
            }
        }
        "tool_call" => {
            let (helper, replies) = (helper.clone(), replies.clone());
            let (name, args) = (text("name"), message["args"].clone());
            let verbose = verbose.inner().clone();
            tauri::async_runtime::spawn(async move {
                let (ok, result) = helper.run_tool(&id, &name, &args).await;
                log::info!(
                    "PC tool {name}: {}",
                    result.lines().next().unwrap_or_default()
                );
                verbose.event(
                    &id,
                    "tool",
                    json!({"name": name, "args": args, "ok": ok, "result": result}),
                );
                let _ = replies.send(vec![json_message(
                    json!({"type": "tool_result", "id": id, "ok": ok, "result": result}),
                )]);
            });
        }
        "error" => {
            log::warn!("Natro: {}", text("message"));
            verbose.event(&id, "error", json!({"message": text("message")}));
            dock.failed(app, &id);
            chat.failed(app, &id, &text("message"));
        }
        "reply_text" => {
            if !chat.is_open() {
                app.state::<Reply>().piece(app, &id, &text("text"));
            }
            chat.piece(app, &id, &text("text"));
        }
        // The translation of what was said, in pieces as it's written.
        "english" => chat.english(app, &id, &text("text")),
        kind if kind.starts_with("file_") => app.state::<Arc<Files>>().handle(app, &message),
        _ => {}
    }
}
