//! The chat: type to Natro in a wide glass box at the bottom of the screen, with
//! the conversation above it as bubbles (Chat.svelte).
//!
//! Ctrl+Space opens it: a blob rises from the bottom of the screen and widens
//! into the box. Enter sends (Shift+Enter starts a new line), and her reply is
//! written into a bubble as it comes, not spoken. Beside the text: a + whose
//! menu has a new chat (the bubbles go), files to the phone, files for Natro, a
//! picture and Settings; the microphone (tap it and talk; it sends when you stop,
//! or tap it again); send. What's asked of the dock
//! (Ctrl+Alt+Space) shows here too, as she speaks it. The bubbles stay until a
//! new chat or Natro ends. Alt tapped twice opens and closes it too
//! (double_tap.rs). Up and Down go through what he typed to her before, as in a
//! terminal (the newest MOST_TYPED, kept in `logs/chat-history.json`; not his
//! answers to her questions, nor to messages).
//!
//! When Natro asks before something risky for one of the chat's requests while
//! it's open, her question is a bubble: its Yes or No, or type (or say) the
//! answer. Closing the chat answers no, as Esc does on the dock. With the chat
//! closed the dock asks, out loud. A reply that arrives while the chat is closed
//! shows in the reply box (reply.rs), and in the chat when it opens again.
//!
//! The chat is shared with the phone (server/natro_agent/chats.py): what he asks
//! there shows here too, with her reply, as the brain sends it (chat_entry), and a
//! new chat on either clears both. Pictures show in it: screenshots Natro takes for
//! him, and ones he sends (pasted, or the picture button); a click opens one large over
//! the screen, to zoom and move (PictureViewer.svelte), or in Windows' own viewer. While
//! the PC was off he may have asked for something on the phone: when it connects,
//! the chat opens with those requests, to run or skip.
//!
//! Files for Natro are picked from the + and show over the box until the next request,
//! typed or said here, which takes them: she gets their paths (never what's in them
//! unless a tool brings them up) to convert or work on (server/natro_agent/convert.py).
//!
//! A message that came to the phone shows in the reply box as a card (reply.rs),
//! answered there or in the chat: then the message is quoted over the box (who,
//! the app, its words), and what's typed goes from the phone, in that message's
//! own app, with no model in between. Its ✕, Esc or closing the chat stops answering.
//!
//! Unlike the dock and the reply box, the chat takes the keyboard while it's open.
//! Esc or Ctrl+Space closes it and gives the keyboard back to the app you were in;
//! a click on another app closes it too. The window covers the screen above the
//! taskbar (the page draws the chat at its bottom middle, and a picture opened large
//! over all of it) but takes the mouse only over the box, the bubbles and an opened
//! picture (the page says where they are): elsewhere clicks go through to what's under it.
//!
//! Here: what the chat shows and does. Its window and the keyboard are chat/window.rs's, answering a
//! message from the phone chat/replying.rs's, the files from its + chat/files.rs's, and the
//! conversation itself chat/entries.rs's.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

use base64::Engine;
use serde::Serialize;
use serde_json::{Value, json};
use tauri::{AppHandle, Emitter, Manager};

use crate::audio::{MIN_SAMPLES, has_speech};
use crate::brain::Brain;
use crate::recorder::Recorder;
use crate::verbose::Verbose;

mod entries;
mod files;
mod replying;
mod window;

use entries::{Log, Step, is_yes, remember};
use files::{names_of, take_names};
use window::{PAGE_LOADED, hide_ready};
pub use entries::{Exchange, Queued, Replying};

/// The pictures kept (the brain keeps as many: older ones are gone from the chat).
const PICTURES: usize = 24;

#[derive(Serialize, Clone)]
#[serde(tag = "action", rename_all = "lowercase")]
pub enum ChatEvent {
    /// Open, with the conversation so far, its pictures (id -> data URL) and requests kept for the PC.
    Show {
        exchanges: Vec<Exchange>,
        listening: bool,
        pictures: HashMap<String, String>,
        queued: Vec<Queued>,
        replying: Option<Replying>,
        attached: Vec<String>,
    },
    Hide,
    /// One exchange changed (or is new).
    Exchange { exchange: Exchange },
    /// A new chat.
    Clear,
    /// Every exchange anew (the shared chat, after connecting).
    Replace { exchanges: Vec<Exchange> },
    /// A picture for the exchanges that name it: its id and a data URL.
    Picture { id: String, url: String },
    /// The requests kept for the PC while it was off, still to run or skip.
    Queued { items: Vec<Queued> },
    /// The microphone is on or off.
    Listening { on: bool },
    /// A word over the box for a moment ("Natro is offline").
    Notice { text: String },
    /// Answering a message from the phone, or (None) not any more.
    Replying { replying: Option<Replying> },
    /// The names of the files for Natro, waiting for his next request.
    Attached { names: Vec<String> },
}

pub struct Chat {
    state: Mutex<State>,
    /// Moves on each time the chat opens or closes; a late hide from before does nothing.
    turn: AtomicU64,
    /// Moves on with each recording; a recording that ends after another started does nothing.
    takes: AtomicU64,
    /// Where the box and the bubbles are in the window: x, y, width, height (logical pixels).
    hits: Mutex<Vec<[f64; 4]>>,
    /// Whether the mouse and the keyboard are being watched (while it's open).
    watching: AtomicBool,
    /// For testing (debug builds): a recorded clip instead of the microphone (recorder.rs).
    test_clip: Option<PathBuf>,
    /// Where what he typed is kept for Up and Down.
    history: PathBuf,
}

#[derive(Default)]
struct State {
    open: bool,
    log: Log,
    /// The chat's pictures: id -> data URL, oldest first.
    pictures: Vec<(String, String)>,
    queued: Vec<Queued>,
    recorder: Option<Recorder>,
    /// The window that had the keyboard before the chat opened (an HWND), to give it back.
    before: isize,
    /// The message from the phone that what's typed answers.
    replying: Option<Replying>,
    /// What he typed to her, oldest first (Up and Down).
    typed: Vec<String>,
    /// The files for Natro, waiting for his next request.
    attached: Vec<PathBuf>,
}

impl Chat {
    pub fn new(home: &Path, test_clip: Option<PathBuf>) -> Chat {
        let history = home.join("logs").join("chat-history.json");
        let typed = std::fs::read_to_string(&history)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        Chat {
            state: Mutex::new(State { typed, ..State::default() }),
            turn: AtomicU64::new(0),
            takes: AtomicU64::new(0),
            hits: Mutex::default(),
            watching: AtomicBool::new(false),
            test_clip,
            history,
        }
    }

    pub fn is_open(&self) -> bool {
        self.state.lock().unwrap().open
    }

    /// Ctrl+Space, the double tap, the tray: open, or close and give the keyboard back.
    pub fn toggle(&self, app: &AppHandle) {
        if self.is_open() {
            log::info!("the chat closed: Ctrl+Space, the double tap or the tray");
            self.close(app, true);
        } else {
            self.open(app);
        }
    }

    /// Enter: the text goes to Natro, or answers her question. False if it couldn't go (offline).
    fn send(&self, app: &AppHandle, text: &str) -> bool {
        let text = text.trim();
        if text.is_empty() {
            return true;
        }
        let brain = app.state::<Brain>();
        let mut state = self.state.lock().unwrap();
        if let Some(replying) = state.replying.clone() {
            let Some(id) = brain.send_message_reply(&replying.id, text) else {
                drop(state);
                self.notice(app, "Natro is offline");
                return false;
            };
            log::info!("answered a message from the phone in the chat as {id}");
            state.replying = None;
            let changed = state.log.asked(&id, &format!("To {}: {text}", replying.to()), false);
            drop(state);
            self.emit(app, ChatEvent::Replying { replying: None });
            self.changed(app, changed);
            return true;
        }
        let changed = if let Some(question) = state.log.waiting() {
            self.answered(app, &question, is_yes(text), "typed");
            state.log.answer(&question, text)
        } else if let Some(id) = brain.send_text(text, &state.attached) {
            log::info!("typed in the chat as {id}");
            if remember(&mut state.typed, text) {
                self.save_history(&state.typed);
            }
            let files = take_names(&mut state.attached);
            let attached = !files.is_empty();
            let changed = state.log.asked_with(&id, text, false, files);
            if attached {
                self.emit(app, ChatEvent::Attached { names: Vec::new() });
            }
            changed
        } else {
            drop(state);
            self.notice(app, "Natro is offline");
            return false;
        };
        drop(state);
        self.changed(app, changed);
        true
    }

    fn save_history(&self, typed: &[String]) {
        let saved = serde_json::to_string(typed)
            .map_err(std::io::Error::other)
            .and_then(|text| {
                if let Some(folder) = self.history.parent() {
                    std::fs::create_dir_all(folder)?;
                }
                std::fs::write(&self.history, text)
            });
        if let Err(e) = saved {
            log::warn!("couldn't keep what was typed in the chat: {e}");
        }
    }

    /// Her question's Yes or No.
    fn answer(&self, app: &AppHandle, question: &str, yes: bool) {
        let mut state = self.state.lock().unwrap();
        if state.log.waiting().as_deref() != Some(question) {
            return;
        }
        self.answered(app, question, yes, "click");
        let changed = state.log.answer(question, if yes { "Yes" } else { "No" });
        drop(state);
        self.changed(app, changed);
    }

    fn answered(&self, app: &AppHandle, question: &str, yes: bool, how: &str) {
        log::info!("answered {} ({how} in the chat)", if yes { "yes" } else { "no" });
        app.state::<Arc<Verbose>>()
            .event(question, "answered", json!({"yes": yes, "how": format!("{how} in the chat")}));
        app.state::<Brain>().send_answer(question, yes);
    }

    /// The microphone: start listening, hands-free (it sends when you stop talking),
    /// or, listening, send what's been said so far.
    fn mic(&self, app: &AppHandle) {
        let mut state = self.state.lock().unwrap();
        if state.recorder.is_some() {
            drop(state);
            return self.listened(app);
        }
        if !state.open {
            return;
        }
        let take = self.takes.fetch_add(1, Ordering::SeqCst) + 1;
        let finished = {
            let app = app.clone();
            // From the recorder's thread, which listened() waits for: do it on another.
            move || {
                thread::spawn(move || {
                    let chat = app.state::<Chat>();
                    if chat.takes.load(Ordering::SeqCst) == take {
                        chat.listened(&app);
                    }
                });
            }
        };
        let recorder = Recorder::start(app.clone(), self.test_clip.clone(), "chat", finished);
        recorder.hands_free();
        state.recorder = Some(recorder);
        drop(state);
        self.emit(app, ChatEvent::Listening { on: true });
    }

    /// Done listening: what was said goes to Natro, or answers her question.
    fn listened(&self, app: &AppHandle) {
        let Some(recorder) = self.state.lock().unwrap().recorder.take() else {
            return;
        };
        self.takes.fetch_add(1, Ordering::SeqCst);
        self.emit(app, ChatEvent::Listening { on: false });
        let pcm = recorder.stop();
        if pcm.len() < MIN_SAMPLES || !has_speech(&pcm) {
            return self.notice(app, "Didn't hear anything");
        }
        let brain = app.state::<Brain>();
        let mut state = self.state.lock().unwrap();
        let question = state.log.waiting();
        // An answer to her question leaves them for the request after it.
        let files = if question.is_none() { state.attached.clone() } else { Vec::new() };
        let Some(id) = brain.send_audio(&pcm, question.as_deref(), &files) else {
            drop(state);
            return self.notice(app, "Natro is offline");
        };
        log::info!("said {:.1} s to the chat as {id}", pcm.len() as f64 / 16_000.0);
        let changed = match question {
            Some(question) => state.log.answered_by_voice(&id, &question),
            None => {
                let files = take_names(&mut state.attached);
                if !files.is_empty() {
                    self.emit(app, ChatEvent::Attached { names: Vec::new() });
                }
                state.log.asked_with(&id, "", true, files)
            }
        };
        drop(state);
        self.changed(app, changed);
    }

    /// A new chat, on the phone too (and Natro starts a new conversation). A question waiting gets a no.
    fn clear(&self, app: &AppHandle) {
        let mut state = self.state.lock().unwrap();
        if let Some(question) = state.log.waiting() {
            self.answered(app, &question, false, "new chat");
        }
        state.log.clear();
        state.pictures.clear();
        state.attached.clear();
        drop(state);
        app.state::<Brain>().send_json(json!({"type": "chat_clear"}));
        self.emit(app, ChatEvent::Clear);
        self.emit(app, ChatEvent::Attached { names: Vec::new() });
    }

    /// A question about the phone's request, answered here.
    fn answer_for(&self, app: &AppHandle, device: &str, question: &str, yes: bool) {
        log::info!("answered {} for the {device} (click in the chat)", if yes { "yes" } else { "no" });
        app.state::<Brain>().send_answer_for(question, yes, device);
        let changed = self.state.lock().unwrap().log.answer_for(device, question, if yes { "Yes" } else { "No" });
        self.changed(app, changed);
    }

    /// A picture he pasted or picked, as a data URL: into the shared chat. False if it couldn't go.
    fn send_picture(&self, app: &AppHandle, url: &str, name: &str) -> bool {
        let Some((mime, bytes)) = from_data_url(url) else {
            self.notice(app, "That isn't a picture");
            return false;
        };
        if !app.state::<Brain>().send_picture(&mime, name, bytes) {
            self.notice(app, "Natro is offline");
            return false;
        }
        true
    }

    /// Open a picture full size, in Windows' own viewer.
    fn open_picture(&self, id: &str) {
        let url = {
            let state = self.state.lock().unwrap();
            state.pictures.iter().find(|(p, _)| p == id).map(|(_, url)| url.clone())
        };
        let Some((mime, bytes)) = url.as_deref().and_then(from_data_url) else { return };
        let extension = match mime.as_str() {
            "image/png" => "png",
            "image/webp" => "webp",
            "image/gif" => "gif",
            _ => "jpg",
        };
        let path = std::env::temp_dir().join(format!("natro-{id}.{extension}"));
        if std::fs::write(&path, bytes).is_ok() {
            let _ = std::process::Command::new("explorer.exe").arg(&path).spawn();
        }
    }

    /// Run (or skip) a request kept for the PC: run, it becomes the chat's own request.
    fn run_queued(&self, app: &AppHandle, id: &str, run: bool) {
        let mut state = self.state.lock().unwrap();
        let Some(at) = state.queued.iter().position(|q| q.id == id) else { return };
        let brain = app.state::<Brain>();
        if !brain.send_json(json!({"type": if run { "queue_run" } else { "queue_skip" }, "id": id})) {
            drop(state);
            return self.notice(app, "Natro is offline");
        }
        let item = state.queued.remove(at);
        let changed = if run {
            state.log.asked(&item.id, &item.text, false);
            state.log.find(&item.id).map(|e| {
                e.queued = item.from.clone();
                e.clone()
            })
        } else {
            None
        };
        let items = state.queued.clone();
        drop(state);
        log::info!("{} the request kept for the PC: {}", if run { "ran" } else { "skipped" }, item.text);
        self.changed(app, changed);
        self.emit(app, ChatEvent::Queued { items });
    }

    // From the brain (brain.rs)

    /// Whether a request is the chat's own (typed, or said to its microphone) and still
    /// waiting: her reply to it isn't spoken.
    pub fn mine(&self, id: &str) -> bool {
        self.state.lock().unwrap().log.mine.contains(id)
    }

    /// A piece of her English for what was said, as it's written.
    pub fn english(&self, app: &AppHandle, id: &str, piece: &str) {
        let changed = self.state.lock().unwrap().log.english(id, piece);
        self.changed(app, changed);
    }

    pub fn heard(&self, app: &AppHandle, id: &str, georgian: &str, english: &str) {
        let changed = self.state.lock().unwrap().log.heard(id, georgian, english);
        self.changed(app, changed);
    }

    /// What she's doing for a request ("Opening Spotify"). False if the chat doesn't show that request.
    pub fn step(&self, app: &AppHandle, id: &str, label: &str, icon: &str, image: Option<String>) -> bool {
        let step = Step {
            label: label.into(),
            icon: icon.into(),
            image,
        };
        let changed = self.state.lock().unwrap().log.step(id, step);
        let shown = changed.is_some();
        self.changed(app, changed);
        shown
    }

    /// The app's icon for a step already showing, once it's found.
    pub fn step_image(&self, app: &AppHandle, id: &str, label: &str, image: String) {
        let changed = self.state.lock().unwrap().log.step_image(id, label, image);
        self.changed(app, changed);
    }

    /// A piece of her reply, as she writes it.
    pub fn piece(&self, app: &AppHandle, id: &str, piece: &str) {
        let changed = self.state.lock().unwrap().log.piece(id, piece);
        self.changed(app, changed);
    }

    /// Her whole reply.
    pub fn replied(&self, app: &AppHandle, id: &str, text: &str) {
        let changed = self.state.lock().unwrap().log.replied(id, text);
        self.changed(app, changed);
    }

    /// The request failed on the brain.
    pub fn failed(&self, app: &AppHandle, id: &str, message: &str) {
        let problem = if message.trim().is_empty() { "Something went wrong" } else { message };
        let changed = self.state.lock().unwrap().log.failed(id, problem);
        self.changed(app, changed);
    }

    /// Natro asks before something risky. The chat takes the question while it's open
    /// and waiting for one of its own requests, unless the dock is waiting for one of
    /// its own (the brain does one request at a time). True if it took it.
    pub fn asked(&self, app: &AppHandle, question: &str, text: &str, dock_waiting: bool) -> bool {
        let mut state = self.state.lock().unwrap();
        if !state.open || dock_waiting {
            return false;
        }
        let changed = state.log.ask(question, text);
        drop(state);
        let taken = changed.is_some();
        self.changed(app, changed);
        taken
    }

    /// An entry of the shared chat changed (or is new).
    pub fn entry(&self, app: &AppHandle, entry: &Value) {
        let changed = self.state.lock().unwrap().log.entry(entry);
        self.changed(app, changed);
    }

    /// The whole shared chat, after connecting.
    pub fn all(&self, app: &AppHandle, entries: &[Value]) {
        let exchanges = self.state.lock().unwrap().log.all(entries);
        self.emit(app, ChatEvent::Replace { exchanges });
    }

    /// A picture for the shared chat (its message, then its bytes).
    pub fn picture(&self, app: &AppHandle, message: &Value, bytes: &[u8]) {
        let id = message["id"].as_str().unwrap_or_default().to_string();
        let mime = message["mime"].as_str().unwrap_or("image/jpeg");
        let url = format!(
            "data:{mime};base64,{}",
            base64::engine::general_purpose::STANDARD.encode(bytes)
        );
        let mut state = self.state.lock().unwrap();
        state.pictures.retain(|(p, _)| *p != id);
        state.pictures.push((id.clone(), url.clone()));
        if state.pictures.len() > PICTURES {
            state.pictures.remove(0);
        }
        drop(state);
        self.emit(app, ChatEvent::Picture { id, url });
    }

    /// New chat on the phone (or here, coming back): the bubbles go.
    pub fn cleared(&self, app: &AppHandle) {
        let mut state = self.state.lock().unwrap();
        if let Some(question) = state.log.waiting() {
            self.answered(app, &question, false, "new chat");
        }
        state.log.clear();
        state.pictures.clear();
        drop(state);
        self.emit(app, ChatEvent::Clear);
    }

    /// The PC's question was answered on the phone.
    pub fn answered_elsewhere(&self, app: &AppHandle, question: &str, yes: bool) {
        let changed = self.state.lock().unwrap().log.answer(question, if yes { "Yes" } else { "No" });
        self.changed(app, changed);
    }

    /// Requests he made on the phone while the PC was off: the chat opens with them, to run or skip.
    pub fn queued(&self, app: &AppHandle, items: &[Value]) {
        let items: Vec<Queued> = items
            .iter()
            .map(|item| Queued {
                id: item["id"].as_str().unwrap_or_default().into(),
                text: item["text"].as_str().unwrap_or_default().into(),
                from: match item["from"].as_str().unwrap_or_default() {
                    "pc" => "PC".into(),
                    other => other.to_string(),
                },
                time: item["time"].as_u64().unwrap_or_default(),
            })
            .filter(|item| !item.id.is_empty())
            .collect();
        if items.is_empty() {
            return;
        }
        log::info!("{} requests kept for the PC while it was off", items.len());
        self.state.lock().unwrap().queued = items.clone();
        self.emit(app, ChatEvent::Queued { items });
        if !self.is_open() {
            self.open(app);
        }
    }

    /// The connection to the brain is lost: requests still waiting won't get their replies.
    pub fn lost(&self, app: &AppHandle) {
        let changed = self.state.lock().unwrap().log.lost();
        for exchange in changed {
            self.emit(app, ChatEvent::Exchange { exchange });
        }
    }

    fn changed(&self, app: &AppHandle, exchange: Option<Exchange>) {
        if let Some(exchange) = exchange {
            self.emit(app, ChatEvent::Exchange { exchange });
        }
    }

    fn notice(&self, app: &AppHandle, text: &str) {
        self.emit(app, ChatEvent::Notice { text: text.into() });
    }

    fn emit(&self, app: &AppHandle, event: ChatEvent) {
        let _ = app.emit_to("chat", "chat", event);
    }
}

impl State {
    /// The chat as it opens.
    fn shown(&self) -> ChatEvent {
        ChatEvent::Show {
            exchanges: self.log.exchanges.clone(),
            listening: self.recorder.is_some(),
            pictures: self.pictures.iter().cloned().collect(),
            queued: self.queued.clone(),
            replying: self.replying.clone(),
            attached: names_of(&self.attached),
        }
    }
}

/// A data URL's type and bytes ("data:image/jpeg;base64,...").
fn from_data_url(url: &str) -> Option<(String, Vec<u8>)> {
    let (head, data) = url.strip_prefix("data:")?.split_once(',')?;
    let mime = head.strip_suffix(";base64")?;
    if !mime.starts_with("image/") {
        return None;
    }
    let bytes = base64::engine::general_purpose::STANDARD.decode(data).ok()?;
    Some((mime.to_string(), bytes))
}

/// What the chat shows (None while it's closed): the page asks when it loads.
#[tauri::command]
pub fn chat_state(app: AppHandle) -> Option<ChatEvent> {
    PAGE_LOADED.store(true, Ordering::SeqCst);
    let showing = app.try_state::<Chat>().and_then(|chat| {
        let state = chat.state.lock().unwrap();
        state.open.then(|| state.shown())
    });
    if showing.is_none() {
        hide_ready(&app); // it was shown off the screen only so the page would load
    }
    showing
}

/// Where the box and the bubbles are: x, y, width, height (logical pixels).
#[tauri::command]
pub fn chat_hits(app: AppHandle, rects: Vec<[f64; 4]>) {
    if let Some(chat) = app.try_state::<Chat>() {
        *chat.hits.lock().unwrap() = rects;
    }
}

/// Enter or the send button. False if it couldn't go: the page keeps the text.
#[tauri::command]
pub fn chat_send(app: AppHandle, text: String) -> bool {
    app.state::<Chat>().send(&app, &text)
}

/// What he typed to her before, oldest first, for Up and Down.
#[tauri::command]
pub fn chat_history(app: AppHandle) -> Vec<String> {
    app.state::<Chat>().state.lock().unwrap().typed.clone()
}

/// The ✕ on "Replying to ...": what's typed goes to Natro again.
#[tauri::command]
pub fn chat_stop_replying(app: AppHandle) {
    app.state::<Chat>().stop_replying(&app);
}

#[tauri::command]
pub fn chat_mic(app: AppHandle) {
    app.state::<Chat>().mic(&app);
}

/// A question's Yes or No; device: whose question, for one about the phone's request.
#[tauri::command]
pub fn chat_answer(app: AppHandle, question: String, yes: bool, device: Option<String>) {
    match device.filter(|d| !d.is_empty()) {
        Some(device) => app.state::<Chat>().answer_for(&app, &device, &question, yes),
        None => app.state::<Chat>().answer(&app, &question, yes),
    }
}

/// A picture pasted or picked in the chat, as a data URL. False if it couldn't go.
#[tauri::command]
pub fn chat_picture(app: AppHandle, data: String, name: String) -> bool {
    app.state::<Chat>().send_picture(&app, &data, &name)
}

#[tauri::command]
pub fn chat_open_picture(app: AppHandle, id: String) {
    app.state::<Chat>().open_picture(&id);
}

/// Run or skip a request kept for the PC.
#[tauri::command]
pub fn chat_queue(app: AppHandle, id: String, run: bool) {
    app.state::<Chat>().run_queued(&app, &id, run);
}

#[tauri::command]
pub fn chat_clear(app: AppHandle) {
    app.state::<Chat>().clear(&app);
}

#[tauri::command]
pub fn chat_attach(app: AppHandle) {
    app.state::<Chat>().attach(&app);
}

#[tauri::command]
pub fn chat_for_natro(app: AppHandle) {
    app.state::<Chat>().for_natro(&app);
}

/// A file's ✕ over the box: at, its place among them.
#[tauri::command]
pub fn chat_detach(app: AppHandle, at: usize) {
    app.state::<Chat>().detach(&app, at);
}

/// Esc, or the double tap (keys: "Alt twice").
#[tauri::command]
pub fn chat_close(app: AppHandle, keys: Option<String>) {
    log::info!("the chat closed: {}", keys.as_deref().unwrap_or("Esc"));
    app.state::<Chat>().close(&app, true);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pictures_come_as_data_urls() {
        assert_eq!(from_data_url("data:image/png;base64,iVBO"), Some(("image/png".into(), vec![0x89, 0x50, 0x4e])));
        assert_eq!(from_data_url("data:text/html;base64,PGI+"), None);
        assert_eq!(from_data_url("https://example.com/a.png"), None);
    }
}
