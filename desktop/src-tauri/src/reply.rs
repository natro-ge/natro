//! The reply box: Natro's answer as text in the top-right corner, in the dock's
//! glass (Reply.svelte).
//!
//! It opens when her reply starts arriving and fills in as it's written. A short
//! reply fades by itself (the page times it, and waits while the mouse is over it);
//! a longer one stays until its ✕ or the next reply. The tray's "Show replies as
//! text" turns it off (remembered as the file `logs/reply-text.off`); a click on
//! the tray icon, the tray's "Show last reply", or a click on the dock brings the
//! last reply back.
//!
//! The window takes clicks (the ✕, links, scrolling) but never the keyboard, and
//! it's only as big as the box: the page says how tall it is.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use windows::Win32::UI::Shell::ShellExecuteW;
use windows::Win32::UI::WindowsAndMessaging::{SW_SHOWNORMAL, SWP_SHOWWINDOW};
use windows::core::{HSTRING, w};

use crate::dock::{hide, prepare_overlay, set_window_pos, work_area_with_mouse};

/// The window's width (logical pixels): the box and, around it, room for its
/// shadow (MARGIN in Reply.svelte, 20 on each side). Keep in step with Reply.svelte.
const WIDTH: f64 = 440.0;
/// Its height until the page says (logical pixels).
const FIRST_HEIGHT: f64 = 120.0;
/// The box is at most this share of the screen's height; it scrolls inside.
const TALLEST: f64 = 0.6;
/// The page's slide out takes this long (Reply.svelte); the window hides after.
const SLIDE_OUT: Duration = Duration::from_millis(300);

#[derive(Serialize, Clone)]
pub struct ReplyEvent {
    /// "show", "text" or "hide".
    action: &'static str,
    text: String,
    /// The whole reply has arrived.
    done: bool,
    /// The tallest the box may be with its shadow (logical pixels).
    max_height: f64,
}

pub struct Reply {
    state: Mutex<State>,
    /// The tray's "Show replies as text".
    on: AtomicBool,
    off_marker: PathBuf,
    /// Moves on each time the box opens or closes; a late hide from before does nothing.
    turn: AtomicU64,
}

#[derive(Default)]
struct State {
    /// The request the text is the reply to.
    id: Option<String>,
    text: String,
    done: bool,
    shown: bool,
    /// Where the window's top-right corner is (physical pixels), the screen's
    /// scale, and the tallest it may be (logical pixels).
    corner: (i32, i32),
    scale: f64,
    max_height: f64,
    /// As the page laid the box out (logical pixels).
    height: f64,
}

impl State {
    /// A piece of the reply to `id` as it's written. True if it starts a new reply.
    fn add(&mut self, id: &str, piece: &str) -> bool {
        let new = self.id.as_deref() != Some(id);
        if new {
            self.id = Some(id.to_string());
            self.text.clear();
        }
        self.text.push_str(piece);
        self.done = false;
        new
    }

    /// The whole reply to `id` (what was written may have been only part of it). True if it's a new reply.
    fn finish(&mut self, id: &str, text: &str) -> bool {
        let new = self.id.as_deref() != Some(id);
        self.id = Some(id.to_string());
        if new || !text.trim().is_empty() {
            self.text = text.to_string();
        }
        self.done = true;
        new
    }

    fn event(&self, action: &'static str) -> ReplyEvent {
        ReplyEvent {
            action,
            text: self.text.clone(),
            done: self.done,
            max_height: self.max_height,
        }
    }
}

impl Reply {
    pub fn new(home: &Path) -> Reply {
        let off_marker = home.join("logs").join("reply-text.off");
        Reply {
            state: Mutex::default(),
            on: AtomicBool::new(!off_marker.is_file()),
            off_marker,
            turn: AtomicU64::new(0),
        }
    }

    pub fn prepare(&self, app: &AppHandle) -> tauri::Result<()> {
        prepare_overlay(&window(app), false, (WIDTH as i32, FIRST_HEIGHT as i32))?;
        if PAGE_LOADED.load(Ordering::SeqCst) {
            hide(&window(app)); // the page is there already: no need to show it off the screen
        }
        Ok(())
    }

    pub fn is_on(&self) -> bool {
        self.on.load(Ordering::SeqCst)
    }

    /// The tray's "Show replies as text". Off, the box closes and stays closed (until asked for).
    pub fn set_on(&self, app: &AppHandle, on: bool) {
        self.on.store(on, Ordering::SeqCst);
        let result = if on {
            fs::remove_file(&self.off_marker).or_else(|e| match e.kind() {
                std::io::ErrorKind::NotFound => Ok(()),
                _ => Err(e),
            })
        } else {
            self.off_marker
                .parent()
                .map_or(Ok(()), fs::create_dir_all)
                .and_then(|_| fs::write(&self.off_marker, b"off\n"))
        };
        if let Err(e) = result {
            log::warn!("couldn't remember \"Show replies as text\": {e}");
        }
        if !on {
            self.close(app);
        }
    }

    /// A piece of Natro's reply, as she writes it (the brain's reply_text).
    pub fn piece(&self, app: &AppHandle, id: &str, piece: &str) {
        let mut state = self.state.lock().unwrap();
        let new = state.add(id, piece);
        self.update(app, state, new);
    }

    /// Her whole reply (the brain's reply).
    pub fn replied(&self, app: &AppHandle, id: &str, text: &str) {
        let mut state = self.state.lock().unwrap();
        if text.trim().is_empty() && state.id.as_deref() != Some(id) {
            return; // nothing to show
        }
        let new = state.finish(id, text);
        self.update(app, state, new);
    }

    /// A word about a file going to or coming from the phone (files.rs), shown even with
    /// "Show replies as text" off: nothing says it out loud. The same key's next word replaces it.
    pub fn notice(&self, app: &AppHandle, key: &str, text: &str) {
        let mut state = self.state.lock().unwrap();
        state.finish(key, text);
        if state.shown {
            let _ = app.emit_to("reply", "reply", state.event("text"));
        } else {
            drop(state);
            self.show(app);
        }
    }

    fn update(&self, app: &AppHandle, state: std::sync::MutexGuard<'_, State>, new: bool) {
        if state.text.trim().is_empty() {
            return;
        }
        if state.shown {
            let _ = app.emit_to("reply", "reply", state.event("text"));
        } else if new && self.is_on() {
            drop(state);
            self.show(app);
        }
    }

    /// Bring the last reply back (the tray, the dock). False if there's none.
    pub fn show_last(&self, app: &AppHandle) -> bool {
        let has = !self.state.lock().unwrap().text.trim().is_empty();
        if has {
            self.show(app);
        }
        has
    }

    /// The tray icon's click: the last reply if it's closed, closed if it's open. False if there's none.
    pub fn toggle(&self, app: &AppHandle) -> bool {
        if self.state.lock().unwrap().shown {
            self.close(app);
            true
        } else {
            self.show_last(app)
        }
    }

    fn show(&self, app: &AppHandle) {
        let (work, scale) = work_area_with_mouse();
        let mut state = self.state.lock().unwrap();
        self.turn.fetch_add(1, Ordering::SeqCst);
        state.shown = true;
        state.corner = (work.right, work.top);
        state.scale = scale;
        state.max_height = ((work.bottom - work.top) as f64 / scale * TALLEST).max(200.0);
        if state.height <= 0.0 {
            state.height = FIRST_HEIGHT;
        }
        let event = state.event("show");
        self.place(app, &state);
        drop(state);
        let _ = app.emit_to("reply", "reply", event);
    }

    /// The window where the box is: its top-right corner at the work area's, as tall as the box.
    fn place(&self, app: &AppHandle, state: &State) {
        let Ok(hwnd) = window(app).hwnd() else { return };
        let width = (WIDTH * state.scale).round() as i32;
        let height = (state.height.min(state.max_height) * state.scale).round() as i32;
        let (right, top) = state.corner;
        set_window_pos(hwnd, right - width, top, width, height, SWP_SHOWWINDOW);
    }

    /// Slide out and hide (its ✕, a short reply's time, the tray).
    pub fn close(&self, app: &AppHandle) {
        let mut state = self.state.lock().unwrap();
        if !state.shown {
            return;
        }
        state.shown = false;
        let turn = self.turn.fetch_add(1, Ordering::SeqCst) + 1;
        drop(state);
        let _ = app.emit_to("reply", "reply", ReplyEvent { action: "hide", text: String::new(), done: true, max_height: 0.0 });
        let app = app.clone();
        thread::spawn(move || {
            thread::sleep(SLIDE_OUT);
            if app.state::<Reply>().turn.load(Ordering::SeqCst) == turn {
                hide(&window(&app));
            }
        });
    }
}

/// The box's page has loaded. A built page (the release app's) can load while the
/// windows are still being made, before the box exists.
static PAGE_LOADED: AtomicBool = AtomicBool::new(false);

/// What the box shows now (None while closed): the page asks when it loads.
#[tauri::command]
pub fn reply_state(app: AppHandle) -> Option<ReplyEvent> {
    PAGE_LOADED.store(true, Ordering::SeqCst);
    let showing = app.try_state::<Reply>().and_then(|reply| {
        let state = reply.state.lock().unwrap();
        state.shown.then(|| state.event("show"))
    });
    if showing.is_none() {
        hide(&window(&app)); // it was shown off the screen only so the page would load
    }
    showing
}

/// The page laid the box out this tall, with its shadow (logical pixels).
#[tauri::command]
pub fn reply_size(app: AppHandle, height: f64) {
    let Some(reply) = app.try_state::<Reply>() else { return };
    let mut state = reply.state.lock().unwrap();
    if (state.height - height).abs() < 0.5 {
        return;
    }
    state.height = height;
    if state.shown {
        reply.place(&app, &state);
    }
}

/// The ✕, or a short reply's time is up.
#[tauri::command]
pub fn reply_close(app: AppHandle) {
    app.state::<Reply>().close(&app);
}

/// A click on the dock (while Natro isn't asking).
#[tauri::command]
pub fn reply_show(app: AppHandle) {
    app.state::<Reply>().show_last(&app);
}

/// A link in a reply: opened in the browser (web links only), or a file that came
/// from the phone, opened or shown in its folder (natro:open/3, natro:show/3).
#[tauri::command]
pub fn reply_open(app: AppHandle, url: String) {
    if url.starts_with("natro:") {
        return app.state::<std::sync::Arc<crate::files::Files>>().open_received(&url);
    }
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return;
    }
    unsafe {
        ShellExecuteW(None, w!("open"), &HSTRING::from(url.as_str()), None, None, SW_SHOWNORMAL);
    }
}

fn window(app: &AppHandle) -> WebviewWindow {
    app.get_webview_window("reply")
        .expect("the reply window is in tauri.conf.json")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pieces_make_one_reply_and_the_next_request_starts_over() {
        let mut state = State::default();
        assert!(state.add("r1", "It's "));
        assert!(!state.add("r1", "sunny."));
        assert_eq!(state.text, "It's sunny.");
        assert!(!state.done);
        assert!(!state.finish("r1", "It's sunny, 24 degrees."));
        assert_eq!(state.text, "It's sunny, 24 degrees.");
        assert!(state.done);
        assert!(state.add("r2", "Done"));
        assert_eq!(state.text, "Done");
    }

    #[test]
    fn a_reply_without_pieces_shows_whole_and_an_empty_end_keeps_the_pieces() {
        let mut state = State::default();
        assert!(state.finish("r1", "Paused."));
        assert_eq!(state.text, "Paused.");
        state.add("r2", "Opening Spotify");
        state.finish("r2", " ");
        assert_eq!(state.text, "Opening Spotify");
        assert!(state.done);
    }
}
