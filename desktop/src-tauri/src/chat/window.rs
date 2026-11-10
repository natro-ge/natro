//! The chat's window and its keyboard: over the screen the mouse is on (above the taskbar), taking the
//! keyboard when it opens and giving it back when it closes, taking the mouse only where the page says the box
//! and the bubbles are, and closing when another app takes the keyboard. What it shows is chat.rs's.

use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::Duration;

use tauri::{AppHandle, Manager, WebviewWindow};
use windows::Win32::Foundation::{HWND, POINT, RECT};
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::Input::KeyboardAndMouse::{INPUT, INPUT_MOUSE, SendInput};
use windows::Win32::UI::WindowsAndMessaging::{
    GetCursorPos, GetForegroundWindow, GetWindowRect, GetWindowThreadProcessId, IsWindow,
    SWP_SHOWWINDOW, SetForegroundWindow,
};

use super::{Chat, ChatEvent};
use crate::dock::{
    prepare_overlay, set_click_through, set_window_pos, solid_hit_surface, work_area_with_mouse,
};
use crate::reply::Reply;

/// The window's size (physical pixels) while it waits off the screen for its page to load: it
/// covers the screen above the taskbar once it opens (Chat.svelte sizes the chat in it).
const LOADING: (i32, i32) = (784, 600);
/// The page's closing (the box shrinks back into the blob and drops) takes this long; the window hides after.
const SLIDE_OUT: Duration = Duration::from_millis(640);
/// How often the chat checks where the mouse is and which app has the keyboard.
const WATCH_EVERY: Duration = Duration::from_millis(30);

impl Chat {
    /// The window as the chat needs it: clicks go through it until the page says where the box is.
    pub fn prepare(&self, app: &AppHandle) -> tauri::Result<()> {
        prepare_overlay(&window(app), true, LOADING)?;
        if PAGE_LOADED.load(Ordering::SeqCst) {
            hide_ready(app); // the page is there already: no need to show it off the screen
        }
        Ok(())
    }

    /// Over the screen the mouse is on (above the taskbar), with the keyboard.
    pub(super) fn open(&self, app: &AppHandle) {
        let Ok(hwnd) = window(app).hwnd() else { return };
        let (work, _) = work_area_with_mouse();
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
        let (w, h) = (work.right - work.left, work.bottom - work.top);
        set_window_pos(hwnd, work.left, work.top, w, h, SWP_SHOWWINDOW);
        // Allowed: Natro has just had the key press (or the tray's click). Not after a double
        // tap, whose keys went to the app in front: then input of her own (a mouse event that
        // moves nothing) makes her the last to have had some, and Windows lets her.
        let mut took = unsafe { SetForegroundWindow(hwnd) }.as_bool();
        if !took {
            log::info!("the chat asks again for the keyboard, after a nudge");
            nudge();
            took = unsafe { SetForegroundWindow(hwnd) }.as_bool();
        }
        if took {
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
        state.replying = None;
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
                hide_ready(&app);
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
}

/// The chat's page has loaded. A built page (the release app's) can load while the
/// windows are still being made, before the chat exists.
pub(super) static PAGE_LOADED: AtomicBool = AtomicBool::new(false);

/// Hidden, and ready to take clicks where the page shows (solid_hit_surface), on the main thread;
/// not if the chat has opened again meanwhile.
pub(super) fn hide_ready(app: &AppHandle) {
    let turn = app.try_state::<Chat>().map(|chat| chat.turn.load(Ordering::SeqCst));
    let main = app.clone();
    let _ = app.run_on_main_thread(move || {
        let chat = main.try_state::<Chat>();
        let now = chat.as_ref().map(|chat| chat.turn.load(Ordering::SeqCst));
        if now != turn || chat.is_some_and(|chat| chat.is_open()) {
            return;
        }
        if let Ok(hwnd) = window(&main).hwnd() {
            solid_hit_surface(hwnd);
        }
    });
}

pub(super) fn window(app: &AppHandle) -> WebviewWindow {
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

/// An input event that does nothing, from Natro.
fn nudge() {
    let input = [INPUT {
        r#type: INPUT_MOUSE,
        ..Default::default()
    }];
    unsafe { SendInput(&input, size_of::<INPUT>() as i32) };
}
