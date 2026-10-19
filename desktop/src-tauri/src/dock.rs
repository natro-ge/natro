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

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Shortcut};
use windows::Win32::Foundation::{HWND, POINT, RECT};
use windows::Win32::Graphics::Dwm::{
    DWM_WINDOW_CORNER_PREFERENCE, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND,
    DwmSetWindowAttribute,
};
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint,
};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::WindowsAndMessaging::{
    GWL_EXSTYLE, GetCursorPos, GetWindowLongPtrW, GetWindowRect, HWND_TOPMOST,
    SET_WINDOW_POS_FLAGS, SWP_ASYNCWINDOWPOS, SWP_FRAMECHANGED, SWP_HIDEWINDOW, SWP_NOACTIVATE,
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_SHOWWINDOW, SetWindowLongPtrW, SetWindowPos,
    WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT,
};

use crate::audio::{MIN_SAMPLES, has_speech};
use crate::brain::Brain;
use crate::recorder::Recorder;
use crate::settings::{Keybinds, Settings};
use crate::talk_key::{PressAndHold, TalkEvent};

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
    /// A step's kind of picture ("music", "calendar"; see agent/natro_agent/steps.py)...
    icon: Option<String>,
    /// ...or the app's own icon, as a data URL (icons.rs).
    image: Option<String>,
}

impl StatusEvent {
    fn new(status: Status, label: &str) -> StatusEvent {
        StatusEvent {
            status,
            label: label.into(),
            icon: None,
            image: None,
        }
    }
}

/// The window, in logical pixels (the page's CSS pixels): room for the dock to
/// slide in, and for its shadow. Keep in step with Dock.svelte.
const STAGE: (f64, f64) = (360.0, 120.0);
/// The pill in the window (logical pixels): centered, this far down, this high.
const PILL_TOP: f64 = 20.0;
const PILL_HEIGHT: f64 = 45.0;
/// While Natro asks, how often the dock checks whether the mouse is over the pill.
const POINTER_EVERY: Duration = Duration::from_millis(30);
/// The page's slide out takes this long (Dock.svelte); the window hides after.
const SLIDE_OUT: Duration = Duration::from_millis(320);
/// How long "Done" shows before the dock slides up.
const SHOW_DONE: Duration = Duration::from_millis(1100);
/// How long a problem ("Natro is offline") shows.
const SHOW_PROBLEM: Duration = Duration::from_millis(2200);

#[derive(Serialize, Clone)]
#[serde(tag = "action", rename_all = "lowercase")]
enum DockEvent {
    Show,
    Hide,
}

/// Where the dock goes on the screen the mouse is on (physical pixels).
#[derive(Clone, Copy, Debug)]
struct Place {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
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
                if let Some(recorder) = &self.state.lock().unwrap().recorder {
                    recorder.hands_free();
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
        let mut state = self.state.lock().unwrap();
        if state.status == Some(Status::Listening) {
            drop(state);
            return self.finish(app);
        }
        let shown = state.status.is_some();
        let turn = self.turn.fetch_add(1, Ordering::SeqCst) + 1;
        state.answering = state.question.is_some();
        let finished = {
            let app = app.clone();
            // From the recorder's thread, which finish() waits for: do it on another.
            move || {
                thread::spawn(move || {
                    let dock = app.state::<Dock>();
                    if dock.turn.load(Ordering::SeqCst) == turn {
                        dock.finish(&app);
                    }
                });
            }
        };
        state.recorder = Some(Recorder::start(
            app.clone(),
            self.test_clip.clone(),
            "dock",
            finished,
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
        let (recorder, answering) = {
            let mut state = self.state.lock().unwrap();
            if state.status != Some(Status::Listening) {
                return;
            }
            state.keys.finished();
            state.status = Some(Status::Thinking);
            (state.recorder.take(), std::mem::take(&mut state.answering))
        };
        let pcm = recorder.map(Recorder::stop).unwrap_or_default();
        let heard = pcm.len() >= MIN_SAMPLES && has_speech(&pcm);
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
                    if brain.send_audio(&pcm, Some(&question)).is_some() {
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
            Some(text) => brain.send_text(text),
            None if !heard => {
                drop(state);
                return self.problem(app, "Didn't hear anything");
            }
            None => brain.send_audio(&pcm, None),
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

    /// Natro's reply arrived (she speaks it): "Done", if it's the one the dock is waiting for.
    pub fn replied(&self, app: &AppHandle, id: &str) {
        let mut state = self.state.lock().unwrap();
        if state.request.as_deref() != Some(id) {
            return;
        }
        state.request = None;
        if !matches!(state.status, Some(Status::Thinking | Status::Step)) {
            return;
        }
        state.status = Some(Status::Done);
        drop(state);
        self.show_status(app, Status::Done);
        self.close_after(app, SHOW_DONE);
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
        let asking = self.state.lock().unwrap().asking.clone();
        let label = match status {
            Status::Listening => "Listening…",
            Status::Thinking => "Thinking…",
            Status::Done => "Done",
            Status::Asking if asking.is_empty() => "Waiting for your OK",
            Status::Asking => &asking,
            Status::Step | Status::Problem => "",
        };
        self.emit_status(app, StatusEvent::new(status, label));
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

fn window(app: &AppHandle) -> WebviewWindow {
    app.get_webview_window("dock")
        .expect("the dock window is in tauri.conf.json")
}

/// Top middle of the work area (the screen minus the taskbar) of the screen the mouse is on.
fn place_on_screen_with_mouse() -> Place {
    let (work, scale) = work_area_with_mouse();
    let (w, h) = (
        (STAGE.0 * scale).round() as i32,
        (STAGE.1 * scale).round() as i32,
    );
    Place {
        x: work.left + (work.right - work.left - w) / 2,
        y: work.top,
        w,
        h,
    }
}

/// The work area (the screen minus the taskbar, physical pixels) of the screen the
/// mouse is on, and that screen's scale (physical pixels per logical one).
pub(crate) fn work_area_with_mouse() -> (RECT, f64) {
    unsafe {
        let mut mouse = POINT::default();
        let _ = GetCursorPos(&mut mouse);
        let monitor = MonitorFromPoint(mouse, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        let _ = GetMonitorInfoW(monitor, &mut info);
        let (mut dpi, mut _dpi_y) = (96, 96);
        let _ = GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dpi, &mut _dpi_y);
        (info.rcWork, dpi as f64 / 96.0)
    }
}

/// One of Natro's windows over the other apps (the dock, the reply box), set up
/// once: never activated (tauri.conf.json), not in Alt+Tab, see-through to clicks
/// if asked, and shown off the screen so its page loads (the page then says so).
pub(crate) fn prepare_overlay(
    window: &WebviewWindow,
    click_through: bool,
    (w, h): (i32, i32),
) -> tauri::Result<()> {
    // Before the style changes below: tao rewrites the window's styles when it sets this.
    window.set_ignore_cursor_events(click_through)?;
    let hwnd = window.hwnd()?;
    // A tool window: not an app in Alt+Tab, nor in Natro's list of open apps.
    unsafe {
        let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW.0 as isize);
    }
    // Tao removes an undecorated window's frame when it first shows the window;
    // these are shown with SetWindowPos instead, so ask for the frame to be
    // worked out again now (else it has a title bar).
    set_window_pos(
        hwnd,
        0,
        0,
        0,
        0,
        SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
    );
    // Square corners: Windows 11 would round the (see-through) window and draw an edge.
    let corners = DWMWCP_DONOTROUND;
    unsafe {
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            (&corners as *const DWM_WINDOW_CORNER_PREFERENCE).cast(),
            size_of::<DWM_WINDOW_CORNER_PREFERENCE>() as u32,
        );
    }
    // The page only loads once its window has been shown: show it off the screen
    // until the page says it's there, so the first opening isn't blank.
    set_window_pos(hwnd, -32000, -32000, w, h, SWP_SHOWWINDOW);
    Ok(())
}

/// Moves the window without activating it. Async, so a thread other than the
/// window's never waits on the main thread (which may be waiting on the dock).
pub(crate) fn set_window_pos(hwnd: HWND, x: i32, y: i32, w: i32, h: i32, flags: SET_WINDOW_POS_FLAGS) {
    unsafe {
        let _ = SetWindowPos(
            hwnd,
            Some(HWND_TOPMOST),
            x,
            y,
            w,
            h,
            flags | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS,
        );
    }
}

/// Whether the mouse is over the pill (the window's middle, PILL_TOP down).
fn pointer_over_pill(hwnd: HWND, width: f64) -> bool {
    unsafe {
        let (mut mouse, mut window) = (POINT::default(), RECT::default());
        if GetCursorPos(&mut mouse).is_err() || GetWindowRect(hwnd, &mut window).is_err() {
            return false;
        }
        let scale = GetDpiForWindow(hwnd) as f64 / 96.0;
        let left = window.left as f64 + (STAGE.0 - width) / 2.0 * scale;
        let top = window.top as f64 + PILL_TOP * scale;
        let (x, y) = (mouse.x as f64, mouse.y as f64);
        x >= left && x < left + width * scale && y >= top && y < top + PILL_HEIGHT * scale
    }
}

/// Clicks go through the window (to what's under it), or the window takes them.
pub(crate) fn set_click_through(hwnd: HWND, through: bool) {
    unsafe {
        let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let transparent = WS_EX_TRANSPARENT.0 as isize;
        let wanted = if through {
            style | transparent
        } else {
            style & !transparent
        };
        if wanted != style {
            SetWindowLongPtrW(hwnd, GWL_EXSTYLE, wanted);
        }
    }
}

fn hide_window(app: &AppHandle) {
    hide(&window(app));
}

/// Hides one of Natro's windows over the other apps.
pub(crate) fn hide(window: &WebviewWindow) {
    if let Ok(hwnd) = window.hwnd() {
        set_window_pos(
            hwnd,
            0,
            0,
            0,
            0,
            SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
        );
    }
}

/// Keys taken from other apps only while one of Natro's windows needs them: Esc
/// while the dock or the reply box is open, the yes and no keybinds (Ctrl+Alt+Y
/// and Ctrl+Alt+N at first) while Natro asks. A key is held while anyone wants it
/// (the dock, "reply").
///
/// Registering a key from inside the shortcut handler would deadlock (the plugin
/// holds its lock while calling it), so a worker thread does it, in order.
#[derive(Clone)]
pub struct TemporaryKeys(mpsc::Sender<(Shortcut, &'static str, bool)>);

impl TemporaryKeys {
    fn new(app: AppHandle) -> TemporaryKeys {
        let (wanted, changes) = mpsc::channel::<(Shortcut, &'static str, bool)>();
        thread::spawn(move || {
            let mut holders = Holders::default();
            while let Ok((key, holder, take)) = changes.recv() {
                let Some(take) = holders.want(key, holder, take) else { continue };
                let shortcuts = app.global_shortcut();
                let result = if take {
                    shortcuts.register(key)
                } else {
                    shortcuts.unregister(key)
                };
                if let Err(e) = result {
                    log::warn!("{key}: {e}");
                    holders.failed(key, take);
                }
            }
        });
        TemporaryKeys(wanted)
    }

    /// Esc, for the dock or the reply box ("dock", "reply").
    pub fn escape(&self, holder: &'static str, take: bool) {
        let _ = self.0.send((Shortcut::new(None, Code::Escape), holder, take));
    }

    /// Yes and no, while Natro asks: the keybinds' keys (settings.rs).
    fn answering(&self, app: &AppHandle, take: bool) {
        let keybinds = app.state::<Settings>().keybinds();
        let _ = self.0.send((keybinds.yes, "dock", take));
        let _ = self.0.send((keybinds.no, "dock", take));
    }
}

/// Who wants which temporary key: it's taken when the first wants it, given back when the last lets go.
#[derive(Default)]
struct Holders(Vec<(Shortcut, Vec<&'static str>)>);

impl Holders {
    /// Some(true): take the key now; Some(false): give it back; None: nothing to do.
    fn want(&mut self, key: Shortcut, holder: &'static str, take: bool) -> Option<bool> {
        let at = match self.0.iter().position(|(k, _)| *k == key) {
            Some(at) => at,
            None => {
                self.0.push((key, Vec::new()));
                self.0.len() - 1
            }
        };
        let holders = &mut self.0[at].1;
        let held = !holders.is_empty();
        if take && !holders.contains(&holder) {
            holders.push(holder);
        } else if !take {
            holders.retain(|h| *h != holder);
        }
        match (held, !holders.is_empty()) {
            (false, true) => Some(true),
            (true, false) => Some(false),
            _ => None,
        }
    }

    /// Taking (or giving back) the key failed: as if it hadn't been asked for, so the next try tries again.
    fn failed(&mut self, key: Shortcut, take: bool) {
        if take {
            self.0.retain(|(k, _)| *k != key);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_held_while_anyone_wants_it() {
        let esc = Shortcut::new(None, Code::Escape);
        let mut holders = Holders::default();
        assert_eq!(holders.want(esc, "dock", true), Some(true)); // taken
        assert_eq!(holders.want(esc, "reply", true), None); // already
        assert_eq!(holders.want(esc, "dock", true), None);
        assert_eq!(holders.want(esc, "dock", false), None); // the reply box still wants it
        assert_eq!(holders.want(esc, "reply", false), Some(false)); // given back
        assert_eq!(holders.want(esc, "reply", false), None);
        // Other keys are their own.
        assert_eq!(holders.want(Keybinds::default().yes, "dock", true), Some(true));
        assert_eq!(holders.want(esc, "reply", true), Some(true));
    }

    #[test]
    fn a_key_that_couldnt_be_taken_is_tried_again() {
        let esc = Shortcut::new(None, Code::Escape);
        let mut holders = Holders::default();
        assert_eq!(holders.want(esc, "reply", true), Some(true));
        holders.failed(esc, true);
        assert_eq!(holders.want(esc, "dock", true), Some(true));
    }
}
