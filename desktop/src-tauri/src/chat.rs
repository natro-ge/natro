//! The chat: type to Natro in a wide glass box at the bottom of the screen, with
//! the conversation above it as bubbles (Chat.svelte).
//!
//! Ctrl+Space opens it: a blob rises from the bottom of the screen and widens
//! into the box. Enter sends (Shift+Enter starts a new line), and her reply is
//! written into a bubble as it comes, not spoken. Beside the text: a + whose
//! menu has a new chat (the bubbles go), files to the phone, a picture and
//! Settings; the microphone (tap it and talk; it sends when you stop, or tap
//! it again); send. What's asked of the dock
//! (Ctrl+Alt+Space) shows here too, as she speaks it. The bubbles stay until a
//! new chat or Natro ends.
//!
//! When Natro asks before something risky for one of the chat's requests while
//! it's open, her question is a bubble: its Yes or No, or type (or say) the
//! answer. Closing the chat answers no, as Esc does on the dock. With the chat
//! closed the dock asks, out loud. A reply that arrives while the chat is closed
//! shows in the reply box (reply.rs), and in the chat when it opens again.
//!
//! The chat is shared with the phone (agent/natro_agent/chats.py): what he asks
//! there shows here too, with her reply, as the brain sends it (chat_entry), and a
//! new chat on either clears both. Pictures show in it: screenshots Natro takes for
//! him, and ones he sends (pasted, or the picture button); a click opens one. While
//! the PC was off he may have asked for something on the phone: when it connects,
//! the chat opens with those requests, to run or skip.
//!
//! Unlike the dock and the reply box, the chat takes the keyboard while it's open.
//! Esc or Ctrl+Space closes it and gives the keyboard back to the app you were in;
//! a click on another app closes it too. The window covers the bottom of the
//! screen but takes the mouse only over the box and the bubbles (the page says
//! where they are): elsewhere clicks go through to what's under it.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use base64::Engine;
use serde::Serialize;
use serde_json::{Value, json};
use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use windows::Win32::Foundation::{HWND, POINT, RECT};
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::WindowsAndMessaging::{
    GetCursorPos, GetForegroundWindow, GetWindowRect, GetWindowThreadProcessId, IsWindow,
    SWP_SHOWWINDOW, SetForegroundWindow,
};

use crate::audio::{MIN_SAMPLES, has_speech};
use crate::brain::Brain;
use crate::dock::{hide, prepare_overlay, set_click_through, set_window_pos, work_area_with_mouse};
use crate::files::Files;
use crate::recorder::Recorder;
use crate::reply::Reply;
use crate::verbose::Verbose;

/// The window's width (logical pixels): the box and the bubbles (720) and room
/// for their shadows. Keep in step with Chat.svelte.
const WIDTH: f64 = 784.0;
/// Its height: this share of the screen's (above the taskbar), at most TALLEST.
const SHARE: f64 = 0.75;
const TALLEST: f64 = 900.0;
/// The page's closing (the box shrinks back into the blob and drops) takes this long; the window hides after.
const SLIDE_OUT: Duration = Duration::from_millis(640);
/// How often the chat checks where the mouse is and which app has the keyboard.
const WATCH_EVERY: Duration = Duration::from_millis(30);
/// The most exchanges kept; the oldest go.
const MOST: usize = 200;
/// How many recordings heard but not (yet) known as requests are remembered.
const REMEMBERED: usize = 16;
/// The pictures kept (the brain keeps as many: older ones are gone from the chat).
const PICTURES: usize = 24;

/// One request and Natro's answer, as the bubbles show them.
#[derive(Serialize, Clone, Default, Debug)]
pub struct Exchange {
    /// The request's id ("r12").
    id: String,
    /// What the owner asked: typed, or her English for what was said (empty until she has it).
    asked: String,
    /// The Georgian she heard, for a spoken request.
    heard: String,
    spoken: bool,
    /// Her reply so far, and whether all of it is there.
    reply: String,
    done: bool,
    /// What she's doing ("Opening Spotify"), while working.
    step: Option<Step>,
    /// Her questions before something risky, with the answers.
    questions: Vec<Question>,
    /// Why it failed.
    problem: String,
    /// The device it was asked on ("phone"), for a request made on the other one; empty for the PC's.
    from: String,
    /// Its pictures, by id (each comes once, as ChatEvent::Picture).
    pictures: Vec<String>,
    /// A request he made on that device while the PC was off ("phone"), run on the PC after.
    queued: String,
    /// A picture he sent, not a request.
    sent: bool,
    /// A reminder that went off: Natro's words alone, with no request.
    reminder: bool,
    /// A background job's task (agent/natro_agent/jobs.py): Natro's work on it, then what she found.
    job: String,
    /// Something a watcher saw (agent/natro_agent/watchers.py): her words alone, as a reminder's.
    alert: bool,
}

#[derive(Serialize, Clone, Debug, PartialEq)]
struct Step {
    label: String,
    /// A kind of picture (glyphs.ts)...
    icon: String,
    /// ...or the app's own icon, as a data URL (icons.rs).
    image: Option<String>,
}

#[derive(Serialize, Clone, Debug, PartialEq)]
struct Question {
    /// The brain's id for it ("c4").
    id: String,
    text: String,
    /// Empty while she waits; then "Yes", "No", or the words typed or said ("…" until they're heard).
    answer: String,
    /// The device that asked, for a question about a request made on the other one ("phone").
    device: String,
}

/// A request he made on the phone while the PC was off, kept for it (pending.py).
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Queued {
    id: String,
    text: String,
    /// The device it was asked on.
    from: String,
    time: u64,
}

/// The conversation the bubbles show, as the owner and the brain's messages change
/// it. Each change gives the exchange that changed (for the page), or None.
#[derive(Default)]
struct Log {
    exchanges: Vec<Exchange>,
    /// The chat's own requests still waiting for their reply: hers to them isn't spoken.
    mine: HashSet<String>,
    /// What she heard of recordings made elsewhere (the dock): (id, Georgian, English),
    /// until one turns out to be a request (a spoken yes or no to a question never gets a reply).
    heard: Vec<(String, String, String)>,
    /// Spoken answers to the chat's questions: (the recording's id, the question's).
    answers: Vec<(String, String)>,
}

impl Log {
    /// A request from the chat: typed, or spoken (what was said comes later).
    fn asked(&mut self, id: &str, asked: &str, spoken: bool) -> Option<Exchange> {
        self.mine.insert(id.to_string());
        self.add(Exchange {
            id: id.into(),
            asked: asked.into(),
            spoken,
            ..Exchange::default()
        });
        self.exchanges.last().cloned()
    }

    fn add(&mut self, exchange: Exchange) {
        self.exchanges.push(exchange);
        if self.exchanges.len() > MOST {
            self.exchanges.remove(0);
        }
    }

    fn find(&mut self, id: &str) -> Option<&mut Exchange> {
        self.exchanges.iter_mut().rev().find(|e| e.id == id)
    }

    /// The exchange for a request; for one made elsewhere that she heard, a new one.
    fn claim(&mut self, id: &str) -> Option<&mut Exchange> {
        if self.exchanges.iter().any(|e| e.id == id) {
            return self.find(id);
        }
        let at = self.heard.iter().position(|(heard, ..)| heard == id)?;
        let (_, georgian, english) = self.heard.remove(at);
        self.add(Exchange {
            id: id.into(),
            asked: english,
            heard: georgian,
            spoken: true,
            ..Exchange::default()
        });
        self.exchanges.last_mut()
    }

    /// A piece of her English for what was said to the chat, as it's written.
    fn english(&mut self, id: &str, piece: &str) -> Option<Exchange> {
        let exchange = self.find(id).filter(|e| e.spoken && e.heard.is_empty())?;
        exchange.asked.push_str(piece);
        Some(exchange.clone())
    }

    /// What the voice pipeline heard of a recording.
    fn heard(&mut self, id: &str, georgian: &str, english: &str) -> Option<Exchange> {
        if let Some(at) = self.answers.iter().position(|(recording, _)| recording == id) {
            let (_, question) = self.answers.remove(at);
            let said = if english.trim().is_empty() { georgian } else { english };
            // Nothing understood counts as no (the brain's is_yes).
            let said = if said.trim().is_empty() { "No" } else { said.trim() };
            return self.answer(&question, said);
        }
        match self.find(id) {
            Some(exchange) => {
                exchange.heard = georgian.into();
                if !english.trim().is_empty() {
                    exchange.asked = english.into();
                }
                Some(exchange.clone())
            }
            None => {
                self.heard.push((id.into(), georgian.into(), english.into()));
                if self.heard.len() > REMEMBERED {
                    self.heard.remove(0);
                }
                None
            }
        }
    }

    /// What she's doing for a request.
    fn step(&mut self, id: &str, step: Step) -> Option<Exchange> {
        let exchange = self.claim(id).filter(|e| !e.done)?;
        exchange.step = Some(step);
        Some(exchange.clone())
    }

    /// The app's own icon for the step showing, once it's found.
    fn step_image(&mut self, id: &str, label: &str, image: String) -> Option<Exchange> {
        let exchange = self.find(id)?;
        let step = exchange.step.as_mut().filter(|step| step.label == label)?;
        step.image = Some(image);
        Some(exchange.clone())
    }

    /// A piece of her reply, as it's written.
    fn piece(&mut self, id: &str, piece: &str) -> Option<Exchange> {
        let exchange = self.claim(id)?;
        exchange.reply.push_str(piece);
        Some(exchange.clone())
    }

    /// Her whole reply (the pieces may have been only part of it).
    fn replied(&mut self, id: &str, text: &str) -> Option<Exchange> {
        self.mine.remove(id);
        let exchange = self.claim(id)?;
        if !text.trim().is_empty() {
            exchange.reply = text.into();
        }
        exchange.done = true;
        exchange.step = None;
        Some(exchange.clone())
    }

    fn failed(&mut self, id: &str, problem: &str) -> Option<Exchange> {
        self.mine.remove(id);
        let exchange = self.claim(id).filter(|e| !e.done)?;
        exchange.problem = problem.into();
        exchange.done = true;
        exchange.step = None;
        Some(exchange.clone())
    }

    /// Natro asks before something risky: about the chat's latest request still waiting, if any.
    fn ask(&mut self, question: &str, text: &str) -> Option<Exchange> {
        let mine = &self.mine;
        let exchange = self
            .exchanges
            .iter_mut()
            .rev()
            .find(|e| !e.done && mine.contains(&e.id))?;
        exchange.questions.push(Question {
            id: question.into(),
            text: text.trim().into(),
            answer: String::new(),
            device: String::new(),
        });
        Some(exchange.clone())
    }

    /// The PC's question still waiting for a yes or no.
    fn waiting(&self) -> Option<String> {
        self.exchanges
            .iter()
            .filter(|e| !e.done && e.from.is_empty())
            .flat_map(|e| &e.questions)
            .find(|q| q.answer.is_empty())
            .map(|q| q.id.clone())
    }

    /// The owner's answer, as it shows under the question (device: whose question, "" for the PC's).
    fn answer_for(&mut self, device: &str, question: &str, answer: &str) -> Option<Exchange> {
        let mine = |q: &Question| q.id == question && q.device == device;
        let exchange = self
            .exchanges
            .iter_mut()
            .rev()
            .find(|e| e.questions.iter().any(mine))?;
        let asked = exchange.questions.iter_mut().find(|q| mine(q))?;
        asked.answer = answer.into();
        Some(exchange.clone())
    }

    fn answer(&mut self, question: &str, answer: &str) -> Option<Exchange> {
        self.answer_for("", question, answer)
    }

    /// An entry of the shared chat, as the brain sends it (agent/natro_agent/chats.py). The PC's own
    /// requests show here as they happen, so from those only the pictures are taken; the rest (the
    /// phone's, and the PC's from before this app started) show as the brain has them.
    fn entry(&mut self, entry: &Value) -> Option<Exchange> {
        let text = |key: &str| entry[key].as_str().unwrap_or_default().to_string();
        let pictures: Vec<String> = entry["pictures"]
            .as_array()
            .map(|all| all.iter().filter_map(|p| p.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let ours = text("device") == "pc";
        if ours {
            if let Some(exchange) = self.find(&text("request")) {
                exchange.pictures = pictures;
                return Some(exchange.clone());
            }
        }
        let step = Some(text("step")).filter(|s| !s.is_empty()).map(|label| Step {
            label,
            icon: "work".into(),
            image: None,
        });
        let question = &entry["question"];
        let questions = question.is_object().then(|| Question {
            id: question["id"].as_str().unwrap_or_default().into(),
            text: question["text"].as_str().unwrap_or_default().into(),
            answer: question["answer"].as_str().unwrap_or_default().into(),
            device: if ours { String::new() } else { text("device") },
        });
        let exchange = Exchange {
            id: if ours { text("request") } else { text("id") },
            asked: text("asked"),
            heard: text("heard"),
            spoken: entry["spoken"].as_bool().unwrap_or(false),
            reply: text("reply"),
            done: entry["done"].as_bool().unwrap_or(false),
            step,
            questions: questions.into_iter().collect(),
            problem: text("problem"),
            from: if ours { String::new() } else { text("label") },
            pictures,
            queued: text("queued"),
            sent: entry["sent"].as_bool().unwrap_or(false),
            reminder: entry["reminder"].as_bool().unwrap_or(false),
            job: text("job"),
            alert: entry["alert"].as_bool().unwrap_or(false),
        };
        match self.exchanges.iter_mut().rev().find(|e| e.id == exchange.id) {
            Some(old) => *old = exchange.clone(),
            None => self.add(exchange.clone()),
        }
        Some(exchange)
    }

    /// The whole shared chat, as the brain has it (after connecting): the phone's anew, the PC's merged.
    fn all(&mut self, entries: &[Value]) -> Vec<Exchange> {
        self.exchanges.retain(|e| e.from.is_empty());
        for entry in entries {
            self.entry(entry);
        }
        self.exchanges.clone()
    }

    /// A spoken answer on its way: what was said shows once it's heard.
    fn answered_by_voice(&mut self, recording: &str, question: &str) -> Option<Exchange> {
        self.answers.push((recording.into(), question.into()));
        if self.answers.len() > REMEMBERED {
            self.answers.remove(0);
        }
        self.answer(question, "…")
    }

    /// A new chat: the bubbles go. Requests still on their way stay the chat's (and quiet).
    fn clear(&mut self) {
        self.exchanges.clear();
        self.heard.clear();
        self.answers.clear();
    }

    /// The connection to Natro is lost: what was still waiting won't get its reply.
    fn lost(&mut self) -> Vec<Exchange> {
        self.mine.clear();
        self.exchanges
            .iter_mut()
            .filter(|e| !e.done)
            .map(|e| {
                e.done = true;
                e.step = None;
                if e.reply.trim().is_empty() {
                    e.problem = "Lost the connection to Natro".into();
                }
                e.clone()
            })
            .collect()
    }
}

/// Whether a typed answer means yes; anything unclear is no. The brain's is_yes
/// (agent/natro_agent/server.py), with Georgian too.
fn is_yes(text: &str) -> bool {
    const YES: [&str; 15] = [
        "yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "correct", "right", "go", "do",
        "კი", "ხო", "ჰო", "დიახ",
    ];
    const NO: [&str; 11] = [
        "no", "nope", "not", "don't", "dont", "cancel", "stop", "wait", "never", "არა", "არ",
    ];
    let lower = text.to_lowercase();
    let words: Vec<&str> = lower
        .split(|c: char| !(c.is_alphanumeric() || c == '\''))
        .filter(|w| !w.is_empty())
        .collect();
    // "OK Natro, no" must not count as yes because of the "OK".
    let words = match words.as_slice() {
        ["ok" | "okay", "natro", rest @ ..] => rest,
        all => all,
    };
    !words.iter().any(|w| NO.contains(w)) && words.iter().any(|w| YES.contains(w))
}

#[derive(Serialize, Clone)]
#[serde(tag = "action", rename_all = "lowercase")]
pub enum ChatEvent {
    /// Open, with the conversation so far, its pictures (id -> data URL) and requests kept for the PC.
    Show {
        exchanges: Vec<Exchange>,
        listening: bool,
        pictures: HashMap<String, String>,
        queued: Vec<Queued>,
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
}

impl Chat {
    pub fn new(test_clip: Option<PathBuf>) -> Chat {
        Chat {
            state: Mutex::default(),
            turn: AtomicU64::new(0),
            takes: AtomicU64::new(0),
            hits: Mutex::default(),
            watching: AtomicBool::new(false),
            test_clip,
        }
    }

    /// The window as the chat needs it: clicks go through it until the page says where the box is.
    pub fn prepare(&self, app: &AppHandle) -> tauri::Result<()> {
        prepare_overlay(&window(app), true, (WIDTH as i32, 600))?;
        if PAGE_LOADED.load(Ordering::SeqCst) {
            hide(&window(app)); // the page is there already: no need to show it off the screen
        }
        Ok(())
    }

    pub fn is_open(&self) -> bool {
        self.state.lock().unwrap().open
    }

    /// Ctrl+Space, the tray: open, or close and give the keyboard back.
    pub fn toggle(&self, app: &AppHandle) {
        if self.is_open() {
            log::info!("the chat closed: Ctrl+Space or the tray");
            self.close(app, true);
        } else {
            self.open(app);
        }
    }

    /// At the bottom middle of the screen the mouse is on, with the keyboard.
    fn open(&self, app: &AppHandle) {
        let Ok(hwnd) = window(app).hwnd() else { return };
        let (work, scale) = work_area_with_mouse();
        let (work_w, work_h) = (work.right - work.left, work.bottom - work.top);
        let w = ((WIDTH * scale).round() as i32).min(work_w);
        let h = (work_h as f64 * SHARE).min(TALLEST * scale).round() as i32;
        let mut state = self.state.lock().unwrap();
        if state.open {
            return;
        }
        state.open = true;
        let front = unsafe { GetForegroundWindow() };
        state.before = if front == hwnd { 0 } else { front.0 as isize };
        let event = state.shown();
        self.turn.fetch_add(1, Ordering::SeqCst);
        drop(state);
        self.hits.lock().unwrap().clear();
        set_click_through(hwnd, true);
        let x = work.left + (work_w - w) / 2;
        set_window_pos(hwnd, x, work.bottom - h, w, h, SWP_SHOWWINDOW);
        // Allowed: Natro has just had the key press (or the tray's click).
        if unsafe { SetForegroundWindow(hwnd) }.as_bool() {
            log::info!("the chat opened");
        } else {
            log::warn!("the chat opened but couldn't take the keyboard");
        }
        app.state::<Reply>().close(app); // the chat shows her replies now
        self.emit(app, event);
        self.watch(app);
    }

    /// Slide away and hide. `give_back`: the keyboard goes back to the app that had it
    /// (Esc, Ctrl+Space); not after a click on another app, which has it already.
    /// A question waiting gets a no; a recording is thrown away.
    pub fn close(&self, app: &AppHandle, give_back: bool) {
        let mut state = self.state.lock().unwrap();
        if !state.open {
            return;
        }
        state.open = false;
        let listening = state.recorder.take().is_some();
        let before = std::mem::take(&mut state.before);
        let declined = state.log.waiting().and_then(|question| {
            self.answered(app, &question, false, "closing");
            state.log.answer(&question, "No")
        });
        let turn = self.turn.fetch_add(1, Ordering::SeqCst) + 1;
        drop(state);
        if listening {
            self.takes.fetch_add(1, Ordering::SeqCst);
            self.emit(app, ChatEvent::Listening { on: false });
        }
        self.changed(app, declined);
        self.emit(app, ChatEvent::Hide);
        if give_back && before != 0 {
            let before = HWND(before as *mut _);
            unsafe {
                if IsWindow(Some(before)).as_bool() {
                    let _ = SetForegroundWindow(before);
                }
            }
        }
        let app = app.clone();
        thread::spawn(move || {
            thread::sleep(SLIDE_OUT);
            if app.state::<Chat>().turn.load(Ordering::SeqCst) == turn {
                hide(&window(&app));
            }
        });
    }

    /// While the chat is open: the window takes the mouse only over the box and the
    /// bubbles, and another app taking the keyboard (a click on it) closes the chat.
    fn watch(&self, app: &AppHandle) {
        if self.watching.swap(true, Ordering::SeqCst) {
            return;
        }
        let app = app.clone();
        thread::spawn(move || {
            let chat = app.state::<Chat>();
            if let Ok(hwnd) = window(&app).hwnd() {
                // Whether the chat has had the keyboard since it opened (this time).
                let (mut turn, mut had_keyboard) = (0, false);
                while chat.is_open() {
                    let now = chat.turn.load(Ordering::SeqCst);
                    if now != turn {
                        (turn, had_keyboard) = (now, false);
                    }
                    let over = pointer_over(hwnd, &chat.hits.lock().unwrap());
                    set_click_through(hwnd, !over);
                    let front = unsafe { GetForegroundWindow() };
                    if front == hwnd {
                        had_keyboard = true;
                    } else if had_keyboard && !front.is_invalid() && !ours(front) {
                        log::info!("the chat closed: another app took the keyboard ({})", process_of(front));
                        chat.close(&app, false);
                    }
                    thread::sleep(WATCH_EVERY);
                }
                set_click_through(hwnd, true); // while it slides away
            }
            chat.watching.store(false, Ordering::SeqCst);
            if chat.is_open() {
                chat.watch(&app); // it opened again just as this stopped
            }
        });
    }

    /// Enter: the text goes to Natro, or answers her question. False if it couldn't go (offline).
    fn send(&self, app: &AppHandle, text: &str) -> bool {
        let text = text.trim();
        if text.is_empty() {
            return true;
        }
        let brain = app.state::<Brain>();
        let mut state = self.state.lock().unwrap();
        let changed = if let Some(question) = state.log.waiting() {
            self.answered(app, &question, is_yes(text), "typed");
            state.log.answer(&question, text)
        } else if let Some(id) = brain.send_text(text) {
            log::info!("typed in the chat as {id}");
            state.log.asked(&id, text, false)
        } else {
            drop(state);
            self.notice(app, "Natro is offline");
            return false;
        };
        drop(state);
        self.changed(app, changed);
        true
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
        let Some(id) = brain.send_audio(&pcm, question.as_deref()) else {
            drop(state);
            return self.notice(app, "Natro is offline");
        };
        log::info!("said {:.1} s to the chat as {id}", pcm.len() as f64 / 16_000.0);
        let changed = match question {
            Some(question) => state.log.answered_by_voice(&id, &question),
            None => state.log.asked(&id, "", true),
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
        drop(state);
        app.state::<Brain>().send_json(json!({"type": "chat_clear"}));
        self.emit(app, ChatEvent::Clear);
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

    /// The paper clip: files to the phone, as the tray's "Send files to the phone…".
    fn attach(&self, app: &AppHandle) {
        let sender = app.clone();
        app.dialog()
            .file()
            .set_parent(&window(app))
            .set_title("Send to your phone")
            .pick_files(move |picked| {
                let paths = picked
                    .unwrap_or_default()
                    .into_iter()
                    .filter_map(|path| path.into_path().ok())
                    .collect::<Vec<_>>();
                if !paths.is_empty() {
                    sender.state::<Arc<Files>>().send_paths(&sender, paths, "phone");
                }
            });
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

/// The chat's page has loaded. A built page (the release app's) can load while the
/// windows are still being made, before the chat exists.
static PAGE_LOADED: AtomicBool = AtomicBool::new(false);

impl State {
    /// The chat as it opens.
    fn shown(&self) -> ChatEvent {
        ChatEvent::Show {
            exchanges: self.log.exchanges.clone(),
            listening: self.recorder.is_some(),
            pictures: self.pictures.iter().cloned().collect(),
            queued: self.queued.clone(),
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
        hide(&window(&app)); // it was shown off the screen only so the page would load
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

/// Esc.
#[tauri::command]
pub fn chat_close(app: AppHandle) {
    log::info!("the chat closed: Esc");
    app.state::<Chat>().close(&app, true);
}

fn window(app: &AppHandle) -> WebviewWindow {
    app.get_webview_window("chat")
        .expect("the chat window is in tauri.conf.json")
}

/// Whether the mouse is over one of the rectangles (the window's logical pixels).
fn pointer_over(hwnd: HWND, hits: &[[f64; 4]]) -> bool {
    unsafe {
        let (mut mouse, mut window) = (POINT::default(), RECT::default());
        if GetCursorPos(&mut mouse).is_err() || GetWindowRect(hwnd, &mut window).is_err() {
            return false;
        }
        let scale = GetDpiForWindow(hwnd).max(1) as f64 / 96.0;
        let x = (mouse.x - window.left) as f64 / scale;
        let y = (mouse.y - window.top) as f64 / scale;
        hits.iter()
            .any(|&[left, top, w, h]| x >= left && x < left + w && y >= top && y < top + h)
    }
}

/// Whether a window is one of Natro's own (the file picker, say).
fn ours(window: HWND) -> bool {
    process_of(window) == std::process::id()
}

fn process_of(window: HWND) -> u32 {
    let mut process = 0;
    unsafe { GetWindowThreadProcessId(window, Some(&mut process)) };
    process
}

#[cfg(test)]
mod tests {
    use super::*;

    fn step(label: &str) -> Step {
        Step {
            label: label.into(),
            icon: "app".into(),
            image: None,
        }
    }

    #[test]
    fn a_typed_request_is_the_chats_until_its_reply() {
        let mut log = Log::default();
        log.asked("r1", "open Spotify", false);
        assert!(log.mine.contains("r1"));
        assert!(log.step("r1", step("Opening Spotify")).is_some());
        log.piece("r1", "Opened ");
        let e = log.piece("r1", "Spotify.").unwrap();
        assert_eq!(e.reply, "Opened Spotify.");
        assert!(!e.done);
        let e = log.replied("r1", "Opened Spotify for you.").unwrap();
        assert_eq!((e.reply.as_str(), e.done, e.step), ("Opened Spotify for you.", true, None));
        assert!(!log.mine.contains("r1"));
    }

    #[test]
    fn what_was_said_to_the_chat_fills_in_as_she_translates_it() {
        let mut log = Log::default();
        log.asked("r2", "", true);
        log.english("r2", "what's the ");
        assert_eq!(log.english("r2", "weather").unwrap().asked, "what's the weather");
        let e = log.heard("r2", "როგორი ამინდია", "What's the weather like?").unwrap();
        assert_eq!((e.asked.as_str(), e.heard.as_str()), ("What's the weather like?", "როგორი ამინდია"));
        // Once heard, a late piece doesn't add to it.
        assert!(log.english("r2", "!").is_none());
    }

    #[test]
    fn a_request_to_the_dock_shows_once_she_works_on_it() {
        let mut log = Log::default();
        assert!(log.english("r3", "pause").is_none());
        assert!(log.heard("r3", "დააპაუზე", "Pause the music").is_none());
        assert!(log.exchanges.is_empty());
        let e = log.step("r3", step("Pausing")).unwrap();
        assert_eq!((e.asked.as_str(), e.spoken), ("Pause the music", true));
        log.replied("r3", "Paused.");
        assert_eq!(log.exchanges.len(), 1);
        assert!(!log.mine.contains("r3"));
        // A spoken yes or no to the dock's question is heard but never gets a reply: no bubble.
        log.heard("r4", "კი", "Yes");
        assert_eq!(log.exchanges.len(), 1);
    }

    #[test]
    fn questions_go_to_the_chats_request_still_waiting_and_take_any_answer() {
        let mut log = Log::default();
        assert!(log.ask("c1", "I'll force close Spotify.").is_none());
        log.asked("r5", "force close Spotify", false);
        let e = log.ask("c1", " I'll force close Spotify. ").unwrap();
        assert_eq!(e.questions[0].text, "I'll force close Spotify.");
        assert_eq!(log.waiting().as_deref(), Some("c1"));
        log.answer("c1", "Yes");
        assert_eq!(log.waiting(), None);
        // Said to the microphone: "…" until it's heard.
        log.ask("c2", "And Chrome?");
        assert_eq!(log.answered_by_voice("r6", "c2").unwrap().questions[1].answer, "…");
        let e = log.heard("r6", "არა", "No").unwrap();
        assert_eq!(e.questions[1].answer, "No");
        assert_eq!(log.exchanges.len(), 1);
    }

    #[test]
    fn a_new_chat_drops_the_bubbles_but_keeps_requests_on_their_way_quiet() {
        let mut log = Log::default();
        log.asked("r7", "what time is it", false);
        log.clear();
        assert!(log.exchanges.is_empty());
        assert!(log.mine.contains("r7"));
        assert!(log.piece("r7", "It's noon.").is_none());
        assert!(log.replied("r7", "It's noon.").is_none());
        assert!(!log.mine.contains("r7"));
    }

    #[test]
    fn a_lost_connection_ends_what_was_waiting() {
        let mut log = Log::default();
        log.asked("r8", "hi", false);
        log.replied("r8", "Hi!");
        log.asked("r9", "open Chrome", false);
        let ended = log.lost();
        assert_eq!(ended.len(), 1);
        assert_eq!(ended[0].problem, "Lost the connection to Natro");
        assert!(log.failed("r9", "late").is_none());
    }

    fn entry(device: &str, request: &str, reply: &str, done: bool) -> Value {
        json!({"id": format!("{device}:{request}"), "device": device, "label": device, "request": request,
               "asked": "what's on my calendar", "heard": "", "spoken": false, "reply": reply, "step": "",
               "done": done, "problem": "", "pictures": [], "question": null, "queued": null, "sent": false})
    }

    #[test]
    fn the_phones_requests_show_as_the_brain_has_them() {
        let mut log = Log::default();
        let e = log.entry(&entry("phone", "r9", "", false)).unwrap();
        assert_eq!((e.id.as_str(), e.from.as_str(), e.done), ("phone:r9", "phone", false));
        let e = log.entry(&entry("phone", "r9", "Nothing today.", true)).unwrap();
        assert_eq!((e.reply.as_str(), e.done), ("Nothing today.", true));
        assert_eq!(log.exchanges.len(), 1);
        // Its question can't take what's typed here: that answers only the PC's own.
        let mut asking = entry("phone", "r10", "", false);
        asking["question"] = json!({"id": "c1", "text": "Text Nino?", "answer": ""});
        let e = log.entry(&asking).unwrap();
        assert_eq!(e.questions[0].device, "phone");
        assert_eq!(log.waiting(), None);
        assert_eq!(log.answer_for("phone", "c1", "Yes").unwrap().questions[0].answer, "Yes");
    }

    #[test]
    fn the_pcs_own_requests_take_only_their_pictures_from_the_brain() {
        let mut log = Log::default();
        log.asked("r1", "show me my phone screen", false);
        log.piece("r1", "Here");
        let mut shared = entry("pc", "r1", "Here it is.", true);
        shared["pictures"] = json!(["pic1"]);
        let e = log.entry(&shared).unwrap();
        assert_eq!((e.reply.as_str(), e.done, e.pictures.clone()), ("Here", false, vec!["pic1".to_string()]));
        assert!(log.mine.contains("r1"));
        // One from before the app started shows whole.
        let e = log.entry(&entry("pc", "r0", "Done.", true)).unwrap();
        assert_eq!((e.id.as_str(), e.from.as_str(), e.reply.as_str()), ("r0", "", "Done."));
    }

    #[test]
    fn after_connecting_the_phones_side_is_replaced_and_the_pcs_kept() {
        let mut log = Log::default();
        log.asked("r1", "hi", false);
        log.entry(&entry("phone", "r5", "Old.", true));
        let all = log.all(&[entry("phone", "r6", "New.", true)]);
        let ids: Vec<&str> = all.iter().map(|e| e.id.as_str()).collect();
        assert_eq!(ids, ["r1", "phone:r6"]);
    }

    #[test]
    fn a_reminder_is_her_words_alone() {
        let mut log = Log::default();
        let mut reminder = entry("natro", "rm1-17", "Time to call Nino.", true);
        reminder["asked"] = json!("");
        reminder["reminder"] = json!(true);
        let e = log.entry(&reminder).unwrap();
        assert!(e.reminder && e.asked.is_empty() && e.done);
        assert_eq!((e.id.as_str(), e.reply.as_str()), ("natro:rm1-17", "Time to call Nino."));
        assert!(!log.entry(&entry("phone", "r1", "Done.", true)).unwrap().reminder);
        let mut alert = entry("natro", "al1", "Nino emailed: Dinner.", true);
        alert["asked"] = json!("");
        alert["alert"] = json!(true);
        let e = log.entry(&alert).unwrap();
        assert!(e.alert && !e.reminder && e.asked.is_empty());
    }

    #[test]
    fn a_background_job_is_her_work_with_its_task() {
        let mut log = Log::default();
        let mut job = entry("natro", "jb1", "", false);
        job["asked"] = json!("");
        job["job"] = json!("Find three flats under $600.");
        job["question"] = json!({"id": "c4", "text": "I'll email Nino the flats.", "answer": "", "device": "pc"});
        let e = log.entry(&job).unwrap();
        assert_eq!(e.job, "Find three flats under $600.");
        assert!(e.asked.is_empty() && !e.done && !e.reminder);
        // Answered here, it goes to the brain as the device "natro"'s: the brain knows where it was asked.
        assert_eq!(e.questions[0].device, "natro");
    }

    #[test]
    fn pictures_come_as_data_urls() {
        assert_eq!(from_data_url("data:image/png;base64,iVBO"), Some(("image/png".into(), vec![0x89, 0x50, 0x4e])));
        assert_eq!(from_data_url("data:text/html;base64,PGI+"), None);
        assert_eq!(from_data_url("https://example.com/a.png"), None);
    }

    #[test]
    fn typed_answers() {
        for yes in ["yes", "Yes please", "ok", "OK, do it", "კი", "ხო", "sure!"] {
            assert!(is_yes(yes), "{yes}");
        }
        for no in ["no", "don't", "ok natro, no", "not now", "არა", "hmm", "", "yes, no, wait"] {
            assert!(!is_yes(no), "{no}");
        }
    }
}
