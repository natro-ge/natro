//! The dock: a small glass pill that drops down from the top of the screen while Natro listens.
//!
//! The talk key (Ctrl+Alt+Space) opens it and starts listening: tap it and talk
//! (it stops when you stop talking, or tap again), or hold it and talk, release
//! to send. Then it shows what Natro is doing ("Thinking…", then each step the
//! brain reports, like "Opening Spotify" with Spotify's own icon, then "Done")
//! and slides back up; Natro speaks her reply. When she asks before something
//! risky it shows her question: click it for yes (its ✕ or Esc for no), or
//! Ctrl+Alt+Y or Ctrl+Alt+N, or the talk key and say yes or no. Esc closes it
//! without sending. (Typing to Natro is the chat's, Ctrl+Space: chat.rs.) These
//! keys are the first ones; Settings changes them (settings.rs's keybinds).
//!
//! The dock never takes the keyboard from the app you're in, and clicks go
//! through it, except on the pill: while Natro asks a click answers, otherwise
//! it brings back her last reply (reply.rs). Its glass is a light tint over whatever is behind it.
//!
//! After a reply to something he said here, once she's quiet, it listens a few seconds more, so he can
//! go on by just talking (Settings' "Keep listening after a reply"; the reply's "listen" says how long, and
//! whether: server/natro_agent/follow_ups.py). Silence, Esc or its ✕ ends it, and nothing's sent. It doesn't
//! open with the chat open, as he types there, or with anything playing on this PC, which it would hear;
//! either of those happening before he says anything ends it. The talk key makes it a recording like any other.

mod window;

use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use windows::Win32::UI::WindowsAndMessaging::SWP_SHOWWINDOW;

use crate::audio::{MIN_SAMPLES, has_speech};
use crate::brain::Brain;
use crate::chat::Chat;
use crate::playing::Speakers;
use crate::recorder::Recorder;
use crate::settings::{Keybinds, Settings};
use crate::talk_key::{PressAndHold, TalkEvent};
use crate::voice::Voice;
use window::{STAGE, hide_window, place_on_screen_with_mouse, pointer_over_pill, window};
pub(crate) use window::{
    TemporaryKeys, hide, prepare_overlay, set_click_through, set_window_pos, solid_hit_surface,
    work_area_with_mouse,
};

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Debug)]
#[serde(rename_all = "lowercase")]
enum Status {
    Listening,
    Thinking,
    Done,
    /// What Natro is doing, from the brain ("Opening Spotify").
    Step,
    /// Natro's question, waiting for a yes or no.
    Asking,
    /// Something to say before closing ("Natro is offline").
    Problem,
}

#[derive(Serialize, Clone)]
pub struct StatusEvent {
    status: Status,
    label: String,
    /// A step's kind of picture ("music", "calendar"; see server/natro_agent/steps.py)...
    icon: Option<String>,
    /// ...or the app's own icon, as a data URL (icons.rs).
    image: Option<String>,
    /// Listening for him to go on after a reply: it has a ✕ to end it.
    follow_up: bool,
}

impl StatusEvent {
    fn new(status: Status, label: &str) -> StatusEvent {
        StatusEvent {
            status,
            label: label.into(),
            icon: None,
            image: None,
            follow_up: false,
        }
    }
}

/// While Natro asks, how often the dock checks whether the mouse is over the pill.
const POINTER_EVERY: Duration = Duration::from_millis(30);
/// The page's slide out takes this long (Dock.svelte); the window hides after.
const SLIDE_OUT: Duration = Duration::from_millis(320);
/// How long "Done" shows before the dock slides up.
const SHOW_DONE: Duration = Duration::from_millis(1100);
/// How long a problem ("Natro is offline") shows.
const SHOW_PROBLEM: Duration = Duration::from_millis(2200);
/// How long "Done" shows before it listens for him to go on.
const DONE_BEFORE_LISTENING: Duration = Duration::from_millis(700);
/// While it listens for him to go on, how often it checks that nothing's started playing.
const WATCH_EVERY: Duration = Duration::from_millis(150);

#[derive(Serialize, Clone)]
#[serde(tag = "action", rename_all = "lowercase")]
enum DockEvent {
    Show,
    Hide,
}

pub struct Dock {
    state: Mutex<State>,
    /// Moves on each time the dock opens, closes or listens again; timers from before stop.
    turn: AtomicU64,
    keys: TemporaryKeys,
    /// For testing (debug builds): a recorded clip instead of the microphone
    /// (see recorder.rs), or a typed request sent instead of the recording.
    test_clip: Option<PathBuf>,
    test_text: Option<String>,
    /// For testing (debug builds): a pretend question from Natro, shown
    /// instead of sending anything (the answer goes nowhere).
    test_ask: Option<String>,
    /// The pill's width (logical pixels), as the page lays it out.
    pill_width: Mutex<f64>,
    /// Whether the mouse is being watched (while Natro asks).
    watching: AtomicBool,
    /// Moves on with each reply it may listen after, and when he stops or starts something: what was waiting stops.
    follow_up_due: AtomicU64,
}

#[derive(Default)]
struct State {
    /// None while hidden.
    status: Option<Status>,
    keys: PressAndHold,
    recorder: Option<Recorder>,
    /// The request whose reply the dock is waiting for.
    request: Option<String>,
    /// Natro's question (its id), waiting for the owner's yes or no...
    question: Option<String>,
    /// ...and what she asked ("I'll force close Spotify.").
    asking: String,
    /// The recording is the spoken answer to `question`.
    answering: bool,
    /// The recording is listening for him to go on after a reply.
    following: bool,
    /// What the dock shows, for a page that (re)loads while it's open.
    showing: Option<StatusEvent>,
}

impl Dock {
    pub fn new(
        app: &AppHandle,
        test_clip: Option<PathBuf>,
        test_text: Option<String>,
        test_ask: Option<String>,
    ) -> Dock {
        Dock {
            state: Mutex::default(),
            turn: AtomicU64::new(0),
            keys: TemporaryKeys::new(app.clone()),
            test_clip,
            test_text,
            test_ask,
            pill_width: Mutex::new(150.0),
            watching: AtomicBool::new(false),
            follow_up_due: AtomicU64::new(0),
        }
    }

    /// The window as the dock needs it: clicks go through to whatever is under it.
    pub fn prepare(&self, app: &AppHandle) -> tauri::Result<()> {
        prepare_overlay(&window(app), true, (STAGE.0 as i32, STAGE.1 as i32))?;
        if PAGE_LOADED.load(Ordering::SeqCst) {
            hide_window(app); // the page is there already: no need to show it off the screen
        }
        Ok(())
    }

    /// The talk key went down (pressed) or up.
    pub fn talk_key(&self, app: &AppHandle, pressed: bool) {
        let now = Instant::now();
        let event = {
            let mut state = self.state.lock().unwrap();
            if pressed {
                state.keys.press(now)
            } else {
                state.keys.release(now)
            }
        };
        match event {
            Some(TalkEvent::Start) => self.listen(app),
            Some(TalkEvent::HandsFree) => {
                let mut state = self.state.lock().unwrap();
                match &state.recorder {
                    Some(recorder) => recorder.hands_free(),
                    // It ended on the press, sending what he'd started to say: the next press starts afresh.
                    None => state.keys.finished(),
                }
            }
            Some(TalkEvent::Send | TalkEvent::Stop) => self.finish(app),
            None => {}
        }
    }

    /// The keys it takes from other apps for a while, for the reply box to share (Esc).
    pub fn keys(&self) -> TemporaryKeys {
        self.keys.clone()
    }

    /// Whether it's on the screen (Esc closes it first, then the reply box).
    pub fn is_shown(&self) -> bool {
        self.state.lock().unwrap().status.is_some()
    }

    /// From the tray: listen hands-free, or send if it's listening.
    pub fn toggle(&self, app: &AppHandle) {
        self.listen(app);
        if let Some(recorder) = &self.state.lock().unwrap().recorder {
            recorder.hands_free();
        }
    }

    /// Esc: close without sending; a question waiting gets a no.
    pub fn cancel(&self, app: &AppHandle) {
        self.follow_up_due.fetch_add(1, Ordering::SeqCst);
        let question = self.state.lock().unwrap().question.take();
        if let Some(question) = question {
            log::info!("answered no (Esc or the dock's ✕)");
            app.state::<Brain>().send_answer(&question, false);
            self.keys.answering(app, false);
        }
        self.close(app);
    }

    /// The yes or no keybind changed (Settings): while Natro asks, the new keys are taken instead.
    pub fn answer_keys_changed(&self, app: &AppHandle, old: Keybinds) {
        let new = app.state::<Settings>().keybinds();
        if (old.yes, old.no) == (new.yes, new.no) || self.state.lock().unwrap().question.is_none() {
            return;
        }
        for key in [old.yes, old.no] {
            let _ = self.keys.0.send((key, "dock", false));
        }
        self.keys.answering(app, true);
    }

    /// Yes or no while Natro asks: the yes and no keybinds, or a click on the dock.
    pub fn answer(&self, app: &AppHandle, yes: bool, how: &str) {
        let question = {
            let mut state = self.state.lock().unwrap();
            if state.status == Some(Status::Listening) {
                state.recorder = None; // a spoken answer was starting; the key wins
                state.answering = false;
            }
            state.question.take()
        };
        let Some(question) = question else { return };
        log::info!("answered {} ({how})", if yes { "yes" } else { "no" });
        app.state::<std::sync::Arc<crate::verbose::Verbose>>()
            .event(&question, "answered", serde_json::json!({"yes": yes, "how": how}));
        app.state::<Brain>().send_answer(&question, yes);
        self.keys.answering(app, false);
        self.show_status(app, Status::Thinking);
    }

    /// Start listening: a new request, or the spoken answer to Natro's question.
    fn listen(&self, app: &AppHandle) {
        self.follow_up_due.fetch_add(1, Ordering::SeqCst);
        let mut state = self.state.lock().unwrap();
        if state.status == Some(Status::Listening) {
            if !nothing_said_yet(&state) {
                drop(state);
                return self.finish(app);
            }
            // Listening for him to go on, and he pressed the key: a recording like any other, from now.
            state.following = false;
            state.recorder = None;
        }
        let shown = state.status.is_some();
        let turn = self.turn.fetch_add(1, Ordering::SeqCst) + 1;
        state.answering = state.question.is_some();
        state.recorder = Some(Recorder::start(
            app.clone(),
            self.test_clip.clone(),
            "dock",
            when_finished(app, turn),
        ));
        state.status = Some(Status::Listening);
        drop(state);
        if !shown {
            self.show_window(app);
        }
        self.show_status(app, Status::Listening);
    }

    /// Stop listening and hand over what was said.
    fn finish(&self, app: &AppHandle) {
        let (recorder, answering, following) = {
            let mut state = self.state.lock().unwrap();
            if state.status != Some(Status::Listening) {
                return;
            }
            state.keys.finished();
            state.status = Some(Status::Thinking);
            (
                state.recorder.take(),
                std::mem::take(&mut state.answering),
                std::mem::take(&mut state.following),
            )
        };
        let pcm = recorder.map(Recorder::stop).unwrap_or_default();
        let heard = pcm.len() >= MIN_SAMPLES && has_speech(&pcm);
        if following && !heard {
            log::info!("nothing said after the reply: nothing sent");
            return self.close(app);
        }
        let brain = app.state::<Brain>();
        let mut state = self.state.lock().unwrap();
        if answering {
            match state.question.clone() {
                // Nothing heard: still asking.
                Some(_) if !heard => {
                    state.status = Some(Status::Asking);
                    drop(state);
                    self.show_status(app, Status::Asking);
                }
                Some(question) => {
                    if brain.send_audio(&pcm, Some(&question), &[]).is_some() {
                        state.question = None;
                        drop(state);
                        self.keys.answering(app, false);
                        self.show_status(app, Status::Thinking);
                    } else {
                        drop(state);
                        self.problem(app, "Natro is offline");
                    }
                }
                None => {
                    drop(state);
                    self.close(app);
                }
            }
            return;
        }
        if let Some(question) = &self.test_ask {
            drop(state);
            return self.asked(app, "test", question);
        }
        let sent = match &self.test_text {
            Some(text) => brain.send_text(text, &[]),
            None if !heard => {
                drop(state);
                return self.problem(app, "Didn't hear anything");
            }
            None if following => brain.send_follow_up(&pcm),
            None => brain.send_audio(&pcm, None, &[]),
        };
        match sent {
            Some(id) => {
                log::info!("sent {:.1} s as {id}", pcm.len() as f64 / 16_000.0);
                state.request = Some(id);
                drop(state);
                self.show_status(app, Status::Thinking);
            }
            None => {
                drop(state);
                self.problem(app, "Natro is offline");
            }
        }
    }

    /// Whether the dock is waiting for the reply to a request of its own (a question
    /// from Natro now is about that one, not the chat's).
    pub fn waiting(&self) -> bool {
        self.state.lock().unwrap().request.is_some()
    }

    /// Natro's reply arrived (she speaks it): "Done", if it's the one the dock is waiting for and it's still
    /// showing that request. Whether it did.
    pub fn replied(&self, app: &AppHandle, id: &str) -> bool {
        let mut state = self.state.lock().unwrap();
        if state.request.as_deref() != Some(id) {
            return false;
        }
        state.request = None;
        if !matches!(state.status, Some(Status::Thinking | Status::Step)) {
            return false;
        }
        state.status = Some(Status::Done);
        drop(state);
        self.show_status(app, Status::Done);
        self.close_after(app, SHOW_DONE);
        true
    }

    /// The request the dock is waiting for was a follow-up with no words she can use: it ends with no reply.
    pub fn nothing_heard(&self, app: &AppHandle, id: &str) {
        let mut state = self.state.lock().unwrap();
        if state.request.as_deref() != Some(id) {
            return;
        }
        state.request = None;
        let showing = matches!(state.status, Some(Status::Thinking | Status::Step));
        drop(state);
        if showing {
            self.close(app);
        }
    }

    /// The reply it just said "Done" to says he can go on by just talking, for this many seconds: it listens
    /// once she's quiet. Always with the microphone, even in a test with a clip for the request itself.
    pub fn follow_up_after(&self, app: &AppHandle, voice: &Voice, seconds: f64) {
        let due = self.follow_up_due.fetch_add(1, Ordering::SeqCst) + 1;
        let app = app.clone();
        voice.when_quiet(move || {
            // From the voice's thread, which has more to say.
            thread::spawn(move || {
                thread::sleep(DONE_BEFORE_LISTENING);
                app.state::<Dock>().follow_up(&app, due, seconds);
            });
        });
    }

    /// Listen for him to go on, unless he's moved on, or something's playing that it would hear.
    fn follow_up(&self, app: &AppHandle, due: u64, seconds: f64) {
        let chat_open = app.state::<Chat>().is_open();
        let online = app.state::<Brain>().online();
        let on = app.state::<Settings>().switches().keep_listening;
        if self.follow_up_due.load(Ordering::SeqCst) != due || chat_open || !online || !on {
            return;
        }
        if Speakers::open().is_some_and(|speakers| speakers.playing()) {
            log::info!("not listening after the reply: something's playing");
            return;
        }
        let mut state = self.state.lock().unwrap();
        if !free_to_listen(state.status, state.request.is_some(), state.question.is_some()) {
            return;
        }
        let shown = state.status.is_some();
        let turn = self.turn.fetch_add(1, Ordering::SeqCst) + 1;
        state.following = true;
        state.recorder = Some(Recorder::follow_up(app.clone(), "dock", seconds, when_finished(app, turn)));
        state.status = Some(Status::Listening);
        drop(state);
        if !shown {
            self.show_window(app);
        }
        self.show_status(app, Status::Listening);
        log::info!("listening {seconds} s after the reply");
        self.watch_follow_up(app, turn);
    }

    /// Before he says anything: a sound starting or the chat opening ends it.
    fn watch_follow_up(&self, app: &AppHandle, turn: u64) {
        let app = app.clone();
        thread::spawn(move || {
            let speakers = Speakers::open();
            let dock = app.state::<Dock>();
            loop {
                thread::sleep(WATCH_EVERY);
                let waiting = {
                    let state = dock.state.lock().unwrap();
                    dock.turn.load(Ordering::SeqCst) == turn && nothing_said_yet(&state)
                };
                if !waiting {
                    return;
                }
                if speakers.as_ref().is_some_and(Speakers::playing) || app.state::<Chat>().is_open() {
                    log::info!("stopped listening after the reply: something started playing, or the chat opened");
                    return dock.close(&app);
                }
            }
        });
    }

    /// Her question was answered on the phone: the dock stops asking.
    pub fn answered_elsewhere(&self, app: &AppHandle, question: &str) {
        let mut state = self.state.lock().unwrap();
        if state.question.as_deref() != Some(question) {
            return;
        }
        state.question = None;
        drop(state);
        self.keys.answering(app, false);
        self.show_status(app, Status::Thinking);
    }

    /// Natro asks before something risky (she speaks the question).
    pub fn asked(&self, app: &AppHandle, question: &str, text: &str) {
        let mut state = self.state.lock().unwrap();
        state.question = Some(question.to_string());
        state.asking = text.trim().to_string();
        let shown = state.status.is_some();
        self.follow_up_due.fetch_add(1, Ordering::SeqCst);
        if std::mem::take(&mut state.following) {
            state.recorder = None; // what it would hear now isn't an answer to this
            state.status = None;
        }
        if state.status != Some(Status::Listening) {
            state.status = Some(Status::Asking);
        }
        self.turn.fetch_add(1, Ordering::SeqCst); // no closing while she asks
        drop(state);
        self.keys.answering(app, true);
        if !shown {
            self.show_window(app);
        }
        self.show_status(app, Status::Asking);
    }

    /// The request failed on the brain.
    pub fn failed(&self, app: &AppHandle, id: &str) {
        let mut state = self.state.lock().unwrap();
        if state.request.as_deref() != Some(id) {
            return;
        }
        state.request = None;
        let showing = matches!(state.status, Some(Status::Thinking | Status::Step));
        drop(state);
        if showing {
            self.problem(app, "Something went wrong");
        }
    }

    /// What Natro is doing for the request the dock is waiting on ("Opening Spotify").
    /// False if the dock isn't showing that request.
    pub fn step(
        &self,
        app: &AppHandle,
        id: &str,
        label: &str,
        icon: &str,
        image: Option<String>,
    ) -> bool {
        let mut state = self.state.lock().unwrap();
        let working = matches!(state.status, Some(Status::Thinking | Status::Step));
        if state.request.as_deref() != Some(id) || !working {
            return false;
        }
        state.status = Some(Status::Step);
        drop(state);
        let mut event = StatusEvent::new(Status::Step, label);
        (event.icon, event.image) = (Some(icon.into()), image);
        self.emit_status(app, event);
        true
    }

    /// The app's icon for a step already showing, once it's found.
    pub fn step_image(&self, app: &AppHandle, id: &str, label: &str, image: String) {
        let state = self.state.lock().unwrap();
        let Some(mut event) = state.showing.clone() else {
            return;
        };
        let same = state.request.as_deref() == Some(id)
            && event.status == Status::Step
            && event.label == label;
        drop(state);
        if same {
            event.image = Some(image);
            self.emit_status(app, event);
        }
    }

    fn problem(&self, app: &AppHandle, label: &'static str) {
        self.state.lock().unwrap().status = Some(Status::Problem);
        self.emit_status(app, StatusEvent::new(Status::Problem, label));
        self.close_after(app, SHOW_PROBLEM);
    }

    fn show_status(&self, app: &AppHandle, status: Status) {
        let (asking, following) = {
            let state = self.state.lock().unwrap();
            (state.asking.clone(), state.following)
        };
        let label = match status {
            Status::Listening => "Listening…",
            Status::Thinking => "Thinking…",
            Status::Done => "Done",
            Status::Asking if asking.is_empty() => "Waiting for your OK",
            Status::Asking => &asking,
            Status::Step | Status::Problem => "",
        };
        let mut event = StatusEvent::new(status, label);
        event.follow_up = following && status == Status::Listening;
        self.emit_status(app, event);
        self.watch_pointer(app);
    }

    /// While the dock shows, the pill takes clicks when the mouse is over it (while
    /// Natro asks, yes, or no on its ✕; else her last reply comes back); the
    /// see-through rest of the window keeps letting clicks through to what's under it.
    fn watch_pointer(&self, app: &AppHandle) {
        if self.watching.swap(true, Ordering::SeqCst) {
            return;
        }
        let app = app.clone();
        thread::spawn(move || {
            let dock = app.state::<Dock>();
            if let Ok(hwnd) = window(&app).hwnd() {
                let mut clickable = false;
                while dock.state.lock().unwrap().status.is_some() {
                    let over = pointer_over_pill(hwnd, *dock.pill_width.lock().unwrap());
                    if over != clickable {
                        set_click_through(hwnd, !over);
                        clickable = over;
                    }
                    thread::sleep(POINTER_EVERY);
                }
                set_click_through(hwnd, true);
            }
            dock.watching.store(false, Ordering::SeqCst);
        });
    }

    fn emit_status(&self, app: &AppHandle, event: StatusEvent) {
        self.state.lock().unwrap().showing = Some(event.clone());
        let _ = app.emit_to("dock", "status", event);
    }

    fn show_window(&self, app: &AppHandle) {
        let Ok(hwnd) = window(app).hwnd() else { return };
        let place = place_on_screen_with_mouse();
        set_window_pos(hwnd, place.x, place.y, place.w, place.h, SWP_SHOWWINDOW);
        self.keys.escape("dock", true);
        let _ = app.emit_to("dock", "dock", DockEvent::Show);
    }

    /// Close after a while, unless something happens on the dock first.
    fn close_after(&self, app: &AppHandle, wait: Duration) {
        let (app, turn) = (app.clone(), self.turn.load(Ordering::SeqCst));
        thread::spawn(move || {
            thread::sleep(wait);
            let dock = app.state::<Dock>();
            if dock.turn.load(Ordering::SeqCst) == turn {
                dock.close(&app);
            }
        });
    }

    /// Slide up and hide. A request on its way still gets its reply spoken.
    fn close(&self, app: &AppHandle) {
        let mut state = self.state.lock().unwrap();
        if state.status.is_none() {
            return;
        }
        state.status = None;
        state.showing = None;
        state.recorder = None;
        state.answering = false;
        state.following = false;
        state.keys.finished();
        let turn = self.turn.fetch_add(1, Ordering::SeqCst) + 1;
        drop(state);
        self.keys.escape("dock", false);
        let _ = app.emit_to("dock", "dock", DockEvent::Hide);
        // The page slides the dock out; hide the (by then empty) window after.
        let app = app.clone();
        thread::spawn(move || {
            thread::sleep(SLIDE_OUT + Duration::from_millis(60));
            if app.state::<Dock>().turn.load(Ordering::SeqCst) == turn {
                hide_window(&app);
            }
        });
    }
}

/// What to do when the recorder hears him finish: from its thread, which finish() waits for, so on another.
fn when_finished(app: &AppHandle, turn: u64) -> impl FnOnce() + Send + 'static {
    let app = app.clone();
    move || {
        thread::spawn(move || {
            let dock = app.state::<Dock>();
            if dock.turn.load(Ordering::SeqCst) == turn {
                dock.finish(&app);
            }
        });
    }
}

/// Whether it's listening for him to go on, and he hasn't started.
fn nothing_said_yet(state: &State) -> bool {
    state.following && !state.recorder.as_ref().is_some_and(Recorder::heard)
}

/// Whether the dock can listen for him to go on: only hidden, or just saying "Done", with nothing under way.
fn free_to_listen(status: Option<Status>, waiting_for_reply: bool, she_asks: bool) -> bool {
    matches!(status, None | Some(Status::Done)) && !waiting_for_reply && !she_asks
}

/// The dock's page has loaded. A built page (the release app's) can load while the
/// windows are still being made, before the dock exists.
static PAGE_LOADED: AtomicBool = AtomicBool::new(false);

/// What the dock shows right now (None while hidden): the page asks when it
/// loads, since a page still loading misses the events.
#[tauri::command]
pub fn dock_state(app: AppHandle) -> Option<StatusEvent> {
    log::info!("the dock's page loaded");
    PAGE_LOADED.store(true, Ordering::SeqCst);
    let showing = app
        .try_state::<Dock>()
        .and_then(|dock| dock.state.lock().unwrap().showing.clone());
    if showing.is_none() {
        hide_window(&app); // it was shown off the screen only so the page would load
    }
    showing
}

/// The page laid the pill out this wide (logical pixels). The page can say so before
/// the dock exists; it says again when the width changes.
#[tauri::command]
pub fn dock_size(app: AppHandle, width: f64) {
    if let Some(dock) = app.try_state::<Dock>() {
        *dock.pill_width.lock().unwrap() = width;
    }
}

/// A click on the dock while Natro asks: yes, or no (its ✕, as Esc).
#[tauri::command]
pub fn dock_answer(app: AppHandle, yes: bool) {
    let dock = app.state::<Dock>();
    if yes {
        dock.answer(&app, true, "click");
    } else {
        dock.cancel(&app);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_listens_after_a_reply_only_with_nothing_else_going_on() {
        assert!(free_to_listen(None, false, false), "hidden");
        assert!(free_to_listen(Some(Status::Done), false, false), "just said Done");
        for busy in [Status::Listening, Status::Thinking, Status::Step, Status::Asking, Status::Problem] {
            assert!(!free_to_listen(Some(busy), false, false), "{busy:?}");
        }
        assert!(!free_to_listen(None, true, false), "another request is on its way");
        assert!(!free_to_listen(None, false, true), "she's asking");
    }

    #[test]
    fn only_listening_after_a_reply_can_be_ended_before_he_starts() {
        assert!(!nothing_said_yet(&State::default()));
        let following = State { following: true, ..State::default() };
        assert!(nothing_said_yet(&following), "not recording any more counts as nothing said");
    }
}
