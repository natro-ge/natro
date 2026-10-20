//! A modifier tapped twice quickly, alone (Alt, Alt): it opens the chat, or closes it, as
//! Ctrl+Space does. Which modifier (Ctrl, Alt or Shift), or none, is a keybind (settings.rs).
//!
//! The shortcut plugin can't take a modifier alone (Windows' RegisterHotKey needs another key
//! with it), so while a double tap is wanted a thread of its own watches the keys with a
//! low-level hook, and the mouse's buttons. A tap is the key pressed and let go within TAP with
//! no other key or click meanwhile (so Alt+Tab, AltGr and an Alt+click aren't taps), and the
//! second press comes within BETWEEN of the first release. Keys other programs press (Natro's
//! own PC control among them) don't count, except in a test (count_simulated). The hook only
//! watches: every key still reaches the app in front, so the first Alt may underline its menu's
//! letters, and the second takes them away.
//!
//! While one of Natro's own windows has the keyboard (the chat, Settings), Windows doesn't call
//! the hook, so their pages watch for the double tap themselves (twice.ts): the chat's closes it,
//! and Settings' is how a new modifier is chosen. Taps the hook sees then are left to them.

use std::cell::RefCell;
use std::str::FromStr;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::thread;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};
use windows::Win32::Foundation::{HINSTANCE, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    VIRTUAL_KEY, VK_CONTROL, VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_MENU, VK_RCONTROL, VK_RMENU, VK_RSHIFT,
    VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetForegroundWindow, GetMessageW, GetWindowThreadProcessId, HC_ACTION, KBDLLHOOKSTRUCT,
    LLKHF_INJECTED, LLMHF_INJECTED, MSG, MSLLHOOKSTRUCT, PM_NOREMOVE, PeekMessageW, PostThreadMessageW,
    SetWindowsHookExW, UnhookWindowsHookEx, WH_KEYBOARD_LL, WH_MOUSE_LL, WM_KEYDOWN, WM_MOUSEMOVE, WM_QUIT,
    WM_SYSKEYDOWN,
};

use crate::chat::Chat;

/// A press shorter than this (ms) is a tap. Keep in step with twice.ts.
const TAP: u32 = 300;
/// The second tap's press comes at most this long (ms) after the first one's release.
const BETWEEN: u32 = 400;

/// The modifiers that can be tapped twice, as settings.json says them ("alt").
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Tap {
    Control,
    Alt,
    Shift,
}

impl Tap {
    /// As he'd say it.
    pub fn label(self) -> &'static str {
        match self {
            Tap::Control => "Ctrl",
            Tap::Alt => "Alt",
            Tap::Shift => "Shift",
        }
    }

    /// The modifier a key is, on either side.
    fn of(key: VIRTUAL_KEY) -> Option<Tap> {
        match key {
            VK_CONTROL | VK_LCONTROL | VK_RCONTROL => Some(Tap::Control),
            VK_MENU | VK_LMENU | VK_RMENU => Some(Tap::Alt),
            VK_SHIFT | VK_LSHIFT | VK_RSHIFT => Some(Tap::Shift),
            _ => None,
        }
    }
}

impl FromStr for Tap {
    type Err = ();

    fn from_str(text: &str) -> Result<Tap, ()> {
        match text {
            "control" => Ok(Tap::Control),
            "alt" => Ok(Tap::Alt),
            "shift" => Ok(Tap::Shift),
            _ => Err(()),
        }
    }
}

/// Key presses and releases (and clicks) as double taps. Times are the hook's, in ms; they wrap.
#[derive(Default)]
pub struct DoubleTap {
    /// The modifier down alone, and since when.
    down: Option<(Tap, u32)>,
    /// The last tap, and when it was let go.
    tapped: Option<(Tap, u32)>,
}

impl DoubleTap {
    /// A key pressed or let go (`tap`: the modifier it is, if it's one): the modifier, if that
    /// was its second tap.
    pub fn key(&mut self, tap: Option<Tap>, pressed: bool, time: u32) -> Option<Tap> {
        if pressed {
            match (tap, self.down) {
                (Some(tap), Some((held, _))) if tap == held => {} // it repeats while held
                (Some(tap), None) => self.down = Some((tap, time)),
                _ => self.interrupt(), // another key with it, or alone
            }
            return None;
        }
        let (held, since) = self.down?;
        if tap != Some(held) {
            return None; // one pressed before it
        }
        self.down = None;
        let first = self.tapped.take();
        if time.wrapping_sub(since) >= TAP {
            return None;
        }
        match first {
            Some((key, released)) if key == held && since.wrapping_sub(released) <= BETWEEN => Some(held),
            _ => {
                self.tapped = Some((held, time));
                None
            }
        }
    }

    /// Another key, or a click: no tap until the next press.
    pub fn interrupt(&mut self) {
        self.down = None;
        self.tapped = None;
    }
}

struct Watcher {
    /// The modifier that opens the chat when it's tapped twice, if any.
    tap: Option<Tap>,
    /// The watching thread's id, while it runs.
    thread: Option<u32>,
    app: Option<AppHandle>,
}

static WATCHER: Mutex<Watcher> = Mutex::new(Watcher {
    tap: None,
    thread: None,
    app: None,
});

/// Keys and clicks other programs make count too: for trying it without hands (debug builds).
static SIMULATED: AtomicBool = AtomicBool::new(false);

thread_local! {
    /// The watching thread's: the hooks are called on it.
    static TAPS: RefCell<DoubleTap> = RefCell::default();
}

/// The modifier tapped twice that opens the chat from now on: the watch starts if it wasn't on,
/// and stops with none.
pub fn watch(app: &AppHandle, tap: Option<Tap>) -> Result<(), String> {
    let mut watcher = WATCHER.lock().unwrap();
    watcher.app.get_or_insert_with(|| app.clone());
    watcher.tap = tap;
    match (tap, watcher.thread) {
        (None, Some(thread)) => {
            watcher.thread = None;
            unsafe { PostThreadMessageW(thread, WM_QUIT, WPARAM(0), LPARAM(0)) }.map_err(|e| e.to_string())?;
        }
        (None, None) | (Some(_), Some(_)) => {}
        (Some(_), None) => watcher.thread = Some(start()?),
    }
    Ok(())
}

/// From now on, keys and clicks other programs make count too (NATRO_TEST_TAPS).
pub fn count_simulated() {
    SIMULATED.store(true, Ordering::SeqCst);
}

/// The watching thread, until it's told to stop: its id.
fn start() -> Result<u32, String> {
    let (sender, started) = mpsc::channel();
    thread::Builder::new()
        .name("double tap".into())
        .spawn(move || unsafe {
            let mut message = MSG::default();
            // Its message queue, so that it can be told to stop before it waits.
            let _ = PeekMessageW(&mut message, None, 0, 0, PM_NOREMOVE);
            let module = GetModuleHandleW(None).ok().map(|module| HINSTANCE(module.0));
            let keys = match SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), module, 0) {
                Ok(hook) => hook,
                Err(e) => return drop(sender.send(Err(format!("the keys can't be watched: {e}")))),
            };
            let clicks = match SetWindowsHookExW(WH_MOUSE_LL, Some(mouse), module, 0) {
                Ok(hook) => hook,
                Err(e) => {
                    let _ = UnhookWindowsHookEx(keys);
                    return drop(sender.send(Err(format!("the mouse can't be watched: {e}"))));
                }
            };
            let _ = sender.send(Ok(GetCurrentThreadId()));
            while GetMessageW(&mut message, None, 0, 0).0 > 0 {}
            let _ = UnhookWindowsHookEx(keys);
            let _ = UnhookWindowsHookEx(clicks);
        })
        .map_err(|e| e.to_string())?;
    started.recv().map_err(|e| e.to_string())?
}

/// Quick, as Windows wants (a slow hook is dropped): what a tap does is done on the main thread.
unsafe extern "system" fn keyboard(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code == HC_ACTION as i32 {
        let key = unsafe { &*(lparam.0 as *const KBDLLHOOKSTRUCT) };
        if !key.flags.contains(LLKHF_INJECTED) || SIMULATED.load(Ordering::Relaxed) {
            let pressed = matches!(wparam.0 as u32, WM_KEYDOWN | WM_SYSKEYDOWN);
            let tap = Tap::of(VIRTUAL_KEY(key.vkCode as u16));
            if let Some(tap) = TAPS.with_borrow_mut(|taps| taps.key(tap, pressed, key.time)) {
                tapped(tap);
            }
        }
    }
    unsafe { CallNextHookEx(None, code, wparam, lparam) }
}

/// A click or the wheel (not a move) between taps.
unsafe extern "system" fn mouse(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code == HC_ACTION as i32 && wparam.0 as u32 != WM_MOUSEMOVE {
        let event = unsafe { &*(lparam.0 as *const MSLLHOOKSTRUCT) };
        if event.flags & LLMHF_INJECTED == 0 || SIMULATED.load(Ordering::Relaxed) {
            TAPS.with_borrow_mut(DoubleTap::interrupt);
        }
    }
    unsafe { CallNextHookEx(None, code, wparam, lparam) }
}

fn tapped(tap: Tap) {
    let (want, app) = {
        let watcher = WATCHER.lock().unwrap();
        (watcher.tap, watcher.app.clone())
    };
    let Some(app) = app.filter(|_| want == Some(tap)) else { return };
    if natros_in_front() {
        return; // her page has the keys (twice.ts)
    }
    log::info!("{} twice: the chat", tap.label());
    let main = app.clone();
    let _ = main.run_on_main_thread(move || app.state::<Chat>().toggle(&app));
}

/// Whether one of Natro's own windows has the keyboard.
fn natros_in_front() -> bool {
    let mut process = 0;
    unsafe { GetWindowThreadProcessId(GetForegroundWindow(), Some(&mut process)) };
    process == std::process::id()
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALT: Option<Tap> = Some(Tap::Alt);
    const CTRL: Option<Tap> = Some(Tap::Control);
    const TAB: Option<Tap> = None;

    /// Each (key, pressed, ms): what came of it.
    fn play(events: &[(Option<Tap>, bool, u32)]) -> Vec<Option<Tap>> {
        let mut taps = DoubleTap::default();
        events.iter().map(|&(key, pressed, at)| taps.key(key, pressed, at)).collect()
    }

    #[test]
    fn twice_quickly_is_a_double_tap() {
        let got = play(&[(ALT, true, 0), (ALT, false, 90), (ALT, true, 250), (ALT, false, 330)]);
        assert_eq!(got, [None, None, None, ALT]);
        // Left Ctrl, then right Ctrl: the same modifier.
        let got = play(&[(CTRL, true, 0), (CTRL, false, 80), (CTRL, true, 200), (CTRL, false, 260)]);
        assert_eq!(got.last(), Some(&CTRL));
    }

    #[test]
    fn a_third_tap_starts_again() {
        let mut taps = DoubleTap::default();
        let mut tap = |pressed_at: u32| {
            taps.key(ALT, true, pressed_at);
            taps.key(ALT, false, pressed_at + 60)
        };
        assert_eq!([tap(0), tap(200), tap(400), tap(600)], [None, ALT, None, ALT]);
    }

    #[test]
    fn too_slow_or_held_is_not() {
        let slow = play(&[(ALT, true, 0), (ALT, false, 90), (ALT, true, 600), (ALT, false, 680)]);
        assert_eq!(slow.last(), Some(&None), "too long between");
        let held = play(&[(ALT, true, 0), (ALT, false, 90), (ALT, true, 200), (ALT, false, 700)]);
        assert_eq!(held.last(), Some(&None), "the second held");
        // Held first, it repeats: no tap, and the next two count afresh.
        let got = play(&[
            (ALT, true, 0),
            (ALT, true, 500),
            (ALT, false, 900),
            (ALT, true, 1000),
            (ALT, false, 1080),
            (ALT, true, 1200),
            (ALT, false, 1260),
        ]);
        assert_eq!(got, [None, None, None, None, None, None, ALT]);
    }

    #[test]
    fn with_another_key_or_a_click_is_not() {
        // Alt, then Alt+Tab.
        let alt_tab = play(&[(ALT, true, 0), (ALT, false, 90), (ALT, true, 200), (TAB, true, 230), (ALT, false, 300)]);
        assert!(alt_tab.iter().all(Option::is_none));
        // AltGr: left Ctrl and right Alt together, twice.
        let alt_gr = play(&[
            (CTRL, true, 0),
            (ALT, true, 0),
            (ALT, false, 80),
            (CTRL, false, 80),
            (CTRL, true, 200),
            (ALT, true, 200),
            (ALT, false, 280),
            (CTRL, false, 280),
        ]);
        assert!(alt_gr.iter().all(Option::is_none));
        // Ctrl, then Alt.
        let mixed = play(&[(CTRL, true, 0), (CTRL, false, 80), (ALT, true, 200), (ALT, false, 260)]);
        assert!(mixed.iter().all(Option::is_none));
        // A key let go that was pressed before doesn't spoil a tap.
        let typing = play(&[(TAB, true, 0), (ALT, true, 20), (TAB, false, 40), (ALT, false, 90), (ALT, true, 200), (ALT, false, 260)]);
        assert_eq!(typing.last(), Some(&ALT));
        // Alt+click, twice.
        let mut taps = DoubleTap::default();
        taps.key(ALT, true, 0);
        taps.interrupt();
        assert_eq!(taps.key(ALT, false, 90), None);
        taps.key(ALT, true, 200);
        assert_eq!(taps.key(ALT, false, 260), None);
    }

    #[test]
    fn the_clock_may_wrap() {
        let start = u32::MAX - 100;
        let got = play(&[
            (ALT, true, start),
            (ALT, false, start.wrapping_add(80)),
            (ALT, true, start.wrapping_add(250)),
            (ALT, false, start.wrapping_add(320)),
        ]);
        assert_eq!(got.last(), Some(&ALT));
    }

    #[test]
    fn taps_are_said_as_settings_json_says_them() {
        assert_eq!(serde_json::to_string(&Tap::Control).unwrap(), r#""control""#);
        assert_eq!("alt".parse::<Tap>(), Ok(Tap::Alt));
        assert!("Alt".parse::<Tap>().is_err() && "super".parse::<Tap>().is_err());
    }
}
