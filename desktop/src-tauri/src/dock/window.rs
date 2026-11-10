//! The dock's window, and the keys it takes from other apps for a while: where it goes on the screen, how
//! it stays out of the way (never activated, clicks going through it), and Esc, yes and no. The reply box and
//! the chat use the same (reply.rs, chat.rs). What the dock shows and when is dock.rs's.

use std::sync::mpsc;
use std::thread;

use tauri::{AppHandle, Manager, WebviewWindow};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Shortcut};
use windows::Win32::Foundation::{HWND, POINT, RECT};
use windows::Win32::Graphics::Dwm::{
    DWM_WINDOW_CORNER_PREFERENCE, DWMWA_CLOAK, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND,
    DwmSetWindowAttribute,
};
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint, RDW_ALLCHILDREN,
    RDW_ERASE, RDW_INVALIDATE, RDW_UPDATENOW, RedrawWindow,
};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::WindowsAndMessaging::{
    GWL_EXSTYLE, GetCursorPos, GetWindowLongPtrW, GetWindowRect, HWND_TOPMOST,
    SET_WINDOW_POS_FLAGS, SWP_ASYNCWINDOWPOS, SWP_FRAMECHANGED, SWP_HIDEWINDOW, SWP_NOACTIVATE,
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_SHOWWINDOW, SetWindowLongPtrW, SetWindowPos,
    WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT,
};

use crate::settings::Settings;

/// The window, in logical pixels (the page's CSS pixels): room for the dock to
/// slide in, and for its shadow. Keep in step with Dock.svelte.
pub(super) const STAGE: (f64, f64) = (360.0, 120.0);
/// The pill in the window (logical pixels): centered, this far down, this high.
const PILL_TOP: f64 = 20.0;
const PILL_HEIGHT: f64 = 45.0;

/// Where the dock goes on the screen the mouse is on (physical pixels).
#[derive(Clone, Copy, Debug)]
pub(super) struct Place {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

pub(super) fn window(app: &AppHandle) -> WebviewWindow {
    app.get_webview_window("dock")
        .expect("the dock window is in tauri.conf.json")
}

/// Top middle of the work area (the screen minus the taskbar) of the screen the mouse is on.
pub(super) fn place_on_screen_with_mouse() -> Place {
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
    // until the page says it's there, so the first opening isn't blank. There the
    // page takes the scale of the nearest screen (with two, maybe the other one's,
    // 125%), and it only looks again when its window changes size, which a move to
    // a screen at the same size doesn't do: the dock showed at 125% on a 100%
    // screen, its ✕ cut off. So it waits a pixel bigger than it ever shows.
    set_window_pos(hwnd, -32000, -32000, w + 1, h + 1, SWP_SHOWWINDOW);
    Ok(())
}

/// Moves the window without activating it. Async, so a thread other than the
/// window's never waits on the main thread (which may be waiting on the dock).
///
/// A move to a screen at another scale makes Windows (through tao) resize the window
/// to look the same size there: the size given here, which is for that screen already,
/// would be scaled twice (the dock too big on a 125% screen, too small back on a 100%
/// one). So the move goes first, then the size, in that order on the window's thread.
pub(crate) fn set_window_pos(hwnd: HWND, x: i32, y: i32, w: i32, h: i32, flags: SET_WINDOW_POS_FLAGS) {
    unsafe {
        if !flags.contains(SWP_NOMOVE) && !flags.contains(SWP_NOSIZE) {
            let _ = SetWindowPos(
                hwnd,
                Some(HWND_TOPMOST),
                x,
                y,
                0,
                0,
                SET_WINDOW_POS_FLAGS(flags.0 & !SWP_SHOWWINDOW.0)
                    | SWP_NOSIZE
                    | SWP_NOACTIVATE
                    | SWP_ASYNCWINDOWPOS,
            );
        }
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
pub(super) fn pointer_over_pill(hwnd: HWND, width: f64) -> bool {
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

/// A click-through window is layered (prepare_overlay), and Windows lets a click through a layered
/// window wherever its own surface is see-through. Nothing draws on that surface (WebView2 draws the
/// page apart from it), so left alone it's see-through nearly everywhere, and clicks on the chat went
/// to the app behind: its Yes closed it, which answered no. Shown where it is but cloaked (so it can't
/// be seen), the window stops being layered for a moment, is painted, and is layered again: its
/// surface is then solid, and only WS_EX_TRANSPARENT (set_click_through) decides. It ends hidden
/// (shown again at once, it would be white). In place: off the screen it could take another
/// monitor's scale. On the window's own thread, the main one: that's where it paints.
pub(crate) fn solid_hit_surface(hwnd: HWND) {
    unsafe {
        let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let layered = WS_EX_LAYERED.0 as isize;
        if style & layered == 0 {
            return;
        }
        cloak(hwnd, true);
        let flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE;
        let _ = SetWindowPos(hwnd, None, 0, 0, 0, 0, flags | SWP_SHOWWINDOW);
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style & !layered);
        let repaint = RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW | RDW_ALLCHILDREN;
        let _ = RedrawWindow(Some(hwnd), None, None, repaint);
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style);
        let _ = RedrawWindow(Some(hwnd), None, None, repaint);
        let _ = SetWindowPos(hwnd, None, 0, 0, 0, 0, flags | SWP_HIDEWINDOW);
        cloak(hwnd, false);
    }
}

/// Whether the window is drawn: cloaked, it's there for Windows but can't be seen.
fn cloak(hwnd: HWND, cloaked: bool) {
    let value = windows::core::BOOL::from(cloaked);
    unsafe {
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_CLOAK,
            (&value as *const windows::core::BOOL).cast(),
            size_of::<windows::core::BOOL>() as u32,
        );
    }
}

pub(super) fn hide_window(app: &AppHandle) {
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
pub struct TemporaryKeys(pub(super) mpsc::Sender<(Shortcut, &'static str, bool)>);

impl TemporaryKeys {
    pub(super) fn new(app: AppHandle) -> TemporaryKeys {
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
    pub(super) fn answering(&self, app: &AppHandle, take: bool) {
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
    use crate::settings::Keybinds;

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
