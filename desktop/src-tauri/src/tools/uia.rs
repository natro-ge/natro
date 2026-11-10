//! The PC's screen through Windows UI Automation: read the window in front, click, type, press
//! keys, scroll, take a screenshot (for screen.rs).
//!
//! The window in front is read in one cached pass (about 0.2 s). Natro's own windows, the dock,
//! her reply and the chat he types to her in, are never the window in front: she works on the app
//! he was using, and brings it back to the front before acting. Clicks go where the element can
//! be seen; when one of her always-on-top windows covers it, the app's own action for it (UI
//! Automation's Invoke, Toggle or Select) is used instead.
//!
//! Everything runs on one thread of its own with COM in the multithreaded apartment, as UI
//! Automation clients should, aware of each monitor's DPI, so the pixels UI Automation gives are
//! the ones the mouse goes to. The elements read stay on that thread (COM's objects can't leave
//! it), by number. An app that stops answering holds up only that thread: after CALL, the next
//! request gets a new one.
//!
//! Ported from the Python helper's uia.py (cleanup step 5.4).

use std::collections::{HashMap, VecDeque};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{HWND, POINT, RECT};
use windows::Win32::Graphics::Dwm::{DWMWA_CLOAKED, DwmGetWindowAttribute};
use windows::Win32::System::Com::{CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx};
use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
use windows::Win32::UI::Accessibility::{
    CUIAutomation8, IUIAutomation, IUIAutomation2, IUIAutomationCacheRequest, IUIAutomationCondition,
    IUIAutomationElement, IUIAutomationInvokePattern, IUIAutomationLegacyIAccessiblePattern,
    IUIAutomationScrollPattern, IUIAutomationSelectionItemPattern, IUIAutomationTogglePattern,
    ScrollAmount_LargeDecrement, ScrollAmount_LargeIncrement, ScrollAmount_NoAmount, TreeScope_Descendants, TreeScope_Element,
    UIA_BoundingRectanglePropertyId, UIA_ControlTypePropertyId, UIA_ExpandCollapseExpandCollapseStatePropertyId,
    UIA_HasKeyboardFocusPropertyId, UIA_InvokePatternId, UIA_IsEnabledPropertyId,
    UIA_IsExpandCollapsePatternAvailablePropertyId, UIA_IsInvokePatternAvailablePropertyId,
    UIA_IsKeyboardFocusablePropertyId, UIA_IsOffscreenPropertyId, UIA_IsPasswordPropertyId,
    UIA_IsScrollPatternAvailablePropertyId, UIA_IsTogglePatternAvailablePropertyId,
    UIA_IsValuePatternAvailablePropertyId, UIA_LegacyIAccessiblePatternId, UIA_NamePropertyId, UIA_PROPERTY_ID,
    UIA_ScrollPatternId, UIA_ScrollVerticallyScrollablePropertyId, UIA_SelectionItemPatternId,
    UIA_ToggleToggleStatePropertyId, UIA_TogglePatternId, UIA_ValueValuePropertyId,
};
use windows::Win32::UI::HiDpi::{DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, SetThreadDpiAwarenessContext};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    INPUT, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_WHEEL,
};
use windows::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, EnumWindows, GA_ROOT, GWL_EXSTYLE, GetAncestor, GetClassNameW, GetForegroundWindow,
    GetWindowLongW, GetWindowRect, GetWindowThreadProcessId, InternalGetWindowText, IsIconic, IsWindowVisible,
    SW_RESTORE, SetCursorPos, SetForegroundWindow, ShowWindow, WS_EX_TOOLWINDOW, WindowFromPoint,
};
use windows::core::{BOOL, Interface};

use super::apps::processes;
use super::input::{
    VK_CONTROL, VK_MENU, VK_RETURN, WHEEL_DELTA, Typed, char_input, key_events, key_input, mouse_input, mouse_kept,
    parse_keys, send, typing,
};
use super::is_space;
use super::quoted;
use super::screen::{Desktop, Element, Screen};
use super::screenshot;
use crate::automation::{Owned, yes_no};

/// A call that takes longer than this has an app hanging.
const CALL: Duration = Duration::from_secs(20);
/// Starting: the thread, UI Automation and its cache.
const START: Duration = Duration::from_secs(60);
pub const MAX_ELEMENTS: usize = 150;
/// Reads whose elements can still be acted on: the shown one, a fresh one while acting, the next.
const READS_KEPT: usize = 4;
/// Natro's desktop app: its windows are hers, not his.
const OWN_PROCESSES: &[&str] = &["natro.exe"];
/// Windows that are never the app he's using.
const SHELL_CLASSES: &[&str] = &["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"];
const TASKBAR_CLASS: &str = "Shell_TrayWnd";
/// Chrome, Edge, and Electron apps such as Discord.
pub const CHROMIUM_CLASS: &str = "Chrome_WidgetWin_1";
/// Characters some apps put in titles ("Microsoft​ Edge") that would stop a name from matching.
const INVISIBLE: &[char] = &['\u{200B}', '\u{200C}', '\u{200D}', '\u{2060}', '\u{FEFF}'];

/// What can be clicked, typed into or read as a page: shown whenever they're on screen.
const ACTIONABLE: &[i32] = &[
    50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50015, 50016, 50019, 50024, 50029, 50030, 50031, 50035,
];
const TEXT: i32 = 50020;
/// The properties read with each element, in one pass.
const PROPERTIES: &[UIA_PROPERTY_ID] = &[
    UIA_NamePropertyId,
    UIA_ControlTypePropertyId,
    UIA_BoundingRectanglePropertyId,
    UIA_HasKeyboardFocusPropertyId,
    UIA_IsPasswordPropertyId,
    UIA_IsEnabledPropertyId,
    UIA_IsKeyboardFocusablePropertyId,
    UIA_IsInvokePatternAvailablePropertyId,
    UIA_IsValuePatternAvailablePropertyId,
    UIA_ValueValuePropertyId,
    UIA_IsTogglePatternAvailablePropertyId,
    UIA_ToggleToggleStatePropertyId,
    UIA_IsExpandCollapsePatternAvailablePropertyId,
    UIA_ExpandCollapseExpandCollapseStatePropertyId,
    UIA_IsScrollPatternAvailablePropertyId,
    UIA_ScrollVerticallyScrollablePropertyId,
];

/// UI Automation's control types, as she reads them.
fn kind(control_type: i32) -> &'static str {
    match control_type {
        50000 => "button",
        50001 => "calendar",
        50002 => "check box",
        50003 => "combo box",
        50004 => "field",
        50005 => "link",
        50006 => "image",
        50007 => "list item",
        50008 => "list",
        50009 => "menu",
        50011 => "menu item",
        50013 => "radio button",
        50015 => "slider",
        50016 => "spinner",
        50018 => "tab list",
        50019 => "tab",
        50020 => "text",
        50023 => "tree",
        50024 => "tree item",
        50025 => "custom",
        50026 => "group",
        50029 => "item",
        50030 => "document",
        50031 => "split button",
        50033 => "pane",
        50035 => "column header",
        _ => "element",
    }
}

fn toggle(state: i32) -> Option<&'static str> {
    ["off", "on", "mixed"].get(usize::try_from(state).ok()?).copied()
}

fn expand(state: i32) -> Option<&'static str> {
    ["collapsed", "expanded", "partly expanded"].get(usize::try_from(state).ok()?).copied()
}

/// A name or title on one line, without the invisible characters some apps put in.
pub fn plain(text: &str) -> String {
    let visible: String = text.chars().filter(|c| !INVISIBLE.contains(c)).collect();
    visible.split(is_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ")
}

// Windows

#[derive(Clone, Debug, Default)]
struct Window {
    hwnd: isize,
    title: String,
    pid: u32,
    /// Its program's file name, lower case ("msedge.exe"); "" if Windows won't say.
    process: String,
    class: String,
    /// A tool window: menus, tooltips, floating bars.
    tool: bool,
    minimized: bool,
}

fn handle(hwnd: isize) -> HWND {
    HWND(hwnd as *mut core::ffi::c_void)
}

/// The windows that could be his, front first: not Natro's own, not the desktop or the taskbar.
fn visible(windows: &[Window], own: &[u32]) -> Vec<Window> {
    windows
        .iter()
        .filter(|w| {
            !own.contains(&w.pid)
                && !OWN_PROCESSES.contains(&w.process.as_str())
                && !SHELL_CLASSES.contains(&w.class.as_str())
        })
        .cloned()
        .collect()
}

/// The window in front, the untitled popups of its app above it, and the other windows, from all
/// top-level windows in their order on screen, front first.
fn pick(windows: &[Window], own: &[u32]) -> (Option<Window>, Vec<Window>, Vec<Window>) {
    let his = visible(windows, own);
    let Some(at) = his.iter().position(|w| !w.title.is_empty() && !w.tool && !w.minimized) else {
        let others = his.into_iter().filter(|w| !w.title.is_empty() && !w.tool).collect();
        return (None, Vec::new(), others);
    };
    let front = his[at].clone();
    let popups = his[..at].iter().filter(|w| w.pid == front.pid && (w.tool || w.title.is_empty())).cloned().collect();
    let mut seen = vec![front.title.clone()];
    let mut others = Vec::new();
    for w in &his[at + 1..] {
        if !w.title.is_empty() && !w.tool && !seen.contains(&w.title) {
            seen.push(w.title.clone());
            others.push(w.clone());
        }
    }
    (Some(front), popups, others)
}

/// Every visible top-level window, front first.
fn windows() -> Vec<Window> {
    unsafe extern "system" fn visit(hwnd: HWND, found: windows::Win32::Foundation::LPARAM) -> BOOL {
        unsafe { (*(found.0 as *mut Vec<isize>)).push(hwnd.0 as isize) };
        true.into()
    }
    let mut hwnds: Vec<isize> = Vec::new();
    unsafe {
        let _ = EnumWindows(Some(visit), windows::Win32::Foundation::LPARAM(&mut hwnds as *mut Vec<isize> as isize));
    }
    let names: HashMap<u32, String> = processes().into_iter().map(|p| (p.pid, p.name.to_lowercase())).collect();
    let mut found = Vec::new();
    for hwnd in hwnds {
        let window = handle(hwnd);
        unsafe {
            if !IsWindowVisible(window).as_bool() {
                continue;
            }
            let mut cloaked = 0u32;
            let _ = DwmGetWindowAttribute(window, DWMWA_CLOAKED, (&mut cloaked as *mut u32).cast(), 4);
            let mut rect = RECT::default();
            let _ = GetWindowRect(window, &mut rect);
            if cloaked != 0 || rect.right <= rect.left || rect.bottom <= rect.top {
                continue; // on another virtual desktop, a suspended Store app, or nothing to see
            }
            let mut pid = 0;
            GetWindowThreadProcessId(window, Some(&mut pid));
            let mut class = [0u16; 256];
            let class_length = GetClassNameW(window, &mut class).max(0) as usize;
            // Unlike GetWindowText, never waits on a hung app.
            let mut title = [0u16; 512];
            let title_length = InternalGetWindowText(window, &mut title).max(0) as usize;
            found.push(Window {
                hwnd,
                title: plain(&String::from_utf16_lossy(&title[..title_length])),
                pid,
                process: names.get(&pid).cloned().unwrap_or_default(),
                class: String::from_utf16_lossy(&class[..class_length]),
                tool: (GetWindowLongW(window, GWL_EXSTYLE) as u32) & WS_EX_TOOLWINDOW.0 != 0,
                minimized: IsIconic(window).as_bool(),
            });
        }
    }
    found
}

// Elements

/// One element as UI Automation gives it.
#[derive(Default)]
struct Raw {
    kind: i32,
    name: String,
    /// Left, top, right, bottom.
    rect: (i32, i32, i32, i32),
    window: String,
    hwnd: isize,
    focused: bool,
    password: bool,
    enabled: bool,
    focusable: bool,
    invokable: bool,
    value: Option<String>,
    toggle: Option<i32>,
    expand: Option<i32>,
    scrolls: bool,
    element: Option<IUIAutomationElement>,
}

fn inside(rect: (i32, i32, i32, i32), outer: (i32, i32, i32, i32)) -> bool {
    let (x, y) = ((rect.0 + rect.2) as f64 / 2.0, (rect.1 + rect.3) as f64 / 2.0);
    outer.0 as f64 <= x && x <= outer.2 as f64 && outer.1 as f64 <= y && y <= outer.3 as f64
}

/// The elements worth numbering, in the order on screen, and whether some were left out.
///
/// What can be clicked or typed into always; text with something in it, unless it only repeats
/// the label of what it's in (a button's own text); other named containers only if they take
/// clicks or focus. With more than the limit, text goes first, so every button still has a number.
fn keep(raws: Vec<Raw>, limit: usize) -> (Vec<Raw>, bool) {
    let mut kept: Vec<Raw> = Vec::new();
    for raw in raws {
        let (left, top, right, bottom) = raw.rect;
        if right - left < 1 || bottom - top < 1 {
            continue;
        }
        let name = plain(&raw.name);
        if raw.kind == TEXT {
            if name.is_empty() {
                continue;
            }
            if let Some(last) = kept.last()
                && last.kind != TEXT
                && plain(&last.name).contains(&name)
                && inside(raw.rect, last.rect)
            {
                continue;
            }
        } else if !ACTIONABLE.contains(&raw.kind) && !(!name.is_empty() && (raw.focusable || raw.invokable)) {
            continue;
        }
        kept.push(raw);
    }
    let cut = kept.len() > limit;
    if cut {
        let actionable = kept.iter().filter(|raw| raw.kind != TEXT).count();
        let room = limit.saturating_sub(actionable);
        let mut texts = 0;
        kept.retain(|raw| {
            if raw.kind != TEXT {
                return true;
            }
            texts += 1;
            texts <= room
        });
        kept.truncate(limit);
    }
    (kept, cut)
}

/// An element as screen.rs shows it; handle: where the reader keeps it.
fn element(raw: &Raw, handle: u64) -> Element {
    let (left, top, right, bottom) = raw.rect;
    let state = if raw.enabled { raw.expand.and_then(expand).map(String::from) } else { Some("disabled".into()) };
    Element {
        x: (left + right).div_euclid(2),
        y: (top + bottom).div_euclid(2),
        kind: kind(raw.kind).into(),
        name: plain(&raw.name),
        window: raw.window.clone(),
        focused: raw.focused,
        password: raw.password,
        value: raw.value.as_deref().filter(|value| !value.is_empty()).map(plain),
        toggle: raw.toggle.and_then(toggle).map(String::from),
        state,
        scrolls: raw.scrolls,
        hwnd: raw.hwnd,
        handle,
    }
}

// The thread

/// UI Automation, on the screen's own thread.
struct Session {
    automation: IUIAutomation,
    cache: IUIAutomationCacheRequest,
    condition: IUIAutomationCondition,
    /// The elements read lately, by handle, and the handles of each read, the newest last.
    elements: HashMap<u64, IUIAutomationElement>,
    reads: VecDeque<Vec<u64>>,
    next: u64,
}

/// The window he's using, never Natro's own, as this.rs goes on from it.
pub struct Front {
    pub hwnd: isize,
    pub title: String,
    pub pid: u32,
    /// Its program's file name, lower case ("msedge.exe").
    pub process: String,
    pub class: String,
}

type Job = Box<dyn FnOnce(Result<&mut Session, &str>) + Send>;

/// The real screen (screen.rs's Desktop): each call runs on the screen's thread.
#[derive(Clone)]
pub struct Uia {
    worker: Arc<Mutex<Option<Sender<Job>>>>,
}

impl Uia {
    pub fn new() -> Uia {
        Uia { worker: Arc::default() }
    }

    /// Work on the window in front (None: none of his is open) on the screen's thread, which has
    /// COM and UI Automation. Blocking.
    pub fn on_front<R: Send + 'static>(
        &self,
        work: impl FnOnce(&IUIAutomation, Option<Front>) -> R + Send + 'static,
    ) -> Result<R, String> {
        self.run(CALL, move |session| {
            let (front, _, _) = pick(&windows(), &[std::process::id()]);
            let front = front.map(|w| Front { hwnd: w.hwnd, title: w.title, pid: w.pid, process: w.process, class: w.class });
            Ok(work(&session.automation, front))
        })
    }

    /// Start the thread ahead of the first screen request. Blocking.
    pub fn warm_up(&self) {
        if let Err(e) = self.run(START, |_| Ok(())) {
            log::warn!("screen control isn't ready: {e}");
        }
    }

    fn worker(&self) -> Sender<Job> {
        let mut worker = self.worker.lock().unwrap();
        if let Some(jobs) = worker.as_ref() {
            return jobs.clone();
        }
        let (jobs, queue) = mpsc::channel::<Job>();
        thread::Builder::new()
            .name("screen".into())
            .spawn(move || {
                let mut session = Session::start();
                for job in queue {
                    let session = session.as_mut().map_err(|why| why.as_str());
                    // A job that panics loses its answer (its caller hears so), not the thread.
                    let _ = catch_unwind(AssertUnwindSafe(|| job(session)));
                }
            })
            .expect("a thread");
        *worker = Some(jobs.clone());
        jobs
    }

    fn run<R: Send + 'static>(
        &self,
        wait: Duration,
        work: impl FnOnce(&mut Session) -> Result<R, String> + Send + 'static,
    ) -> Result<R, String> {
        let (done, answer) = mpsc::channel();
        let job: Job = Box::new(move |session| {
            let result = match session {
                Ok(session) => work(session),
                Err(why) => Err(format!("The screen couldn't be used: {why}")),
            };
            let _ = done.send(result);
        });
        if self.worker().send(job).is_err() {
            *self.worker.lock().unwrap() = None;
            return Err("The screen couldn't be used: its thread ended (it starts again for the next request).".into());
        }
        match answer.recv_timeout(wait) {
            Ok(result) => result,
            Err(RecvTimeoutError::Timeout) => {
                // That thread may be stuck on a hung app: the next call gets a new one.
                *self.worker.lock().unwrap() = None;
                Err(format!("The screen didn't answer within {} seconds: an app may be hanging.", wait.as_secs()))
            }
            Err(RecvTimeoutError::Disconnected) => Err("The screen couldn't be used: it failed unexpectedly.".into()),
        }
    }
}

impl Desktop for Uia {
    fn read(&self, everything: bool) -> Result<Screen, String> {
        self.run(CALL, move |session| Ok(session.read(everything)))
    }

    fn locate(&self, element: &Element) -> Result<Option<Element>, String> {
        let element = element.clone();
        self.run(CALL, move |session| Ok(session.locate(&element)))
    }

    fn click(&self, element: &Element, button: Button, clicks: u32) -> Result<(), String> {
        let element = element.clone();
        self.run(CALL, move |session| session.click(&element, button, clicks))
    }

    fn type_text(&self, element: Option<&Element>, text: &str, clear: bool, enter: bool, window: isize)
    -> Result<(), String> {
        let (element, text) = (element.cloned(), text.to_string());
        self.run(CALL, move |session| session.type_text(element.as_ref(), &text, clear, enter, window))
    }

    fn key(&self, keys: &str, window: isize) -> Result<(), String> {
        let keys = keys.to_string();
        self.run(CALL, move |session| session.key(&keys, window))
    }

    fn scroll(&self, element: Option<&Element>, down: bool, window: isize) -> Result<(), String> {
        let element = element.cloned();
        self.run(CALL, move |session| session.scroll(element.as_ref(), down, window))
    }

    fn switch(&self, name: &str) -> Result<String, String> {
        let name = name.to_string();
        self.run(CALL, move |session| session.switch(&name))
    }

    fn wait_for(&self, text: &str, seconds: u64) -> Result<(bool, Screen), String> {
        let wanted = plain(text).to_lowercase();
        let end = Instant::now() + Duration::from_secs(seconds);
        loop {
            let screen = self.read(false)?;
            let shows = |element: &Element| {
                let value = element.value.as_deref().unwrap_or_default();
                format!("{} {value}", element.name).to_lowercase().contains(&wanted)
            };
            let window = screen.window.as_deref().unwrap_or_default().to_lowercase();
            if screen.elements.iter().any(shows) || window.contains(&wanted) {
                return Ok((true, screen));
            }
            if Instant::now() >= end {
                return Ok((false, screen));
            }
            thread::sleep(Duration::from_millis(400));
        }
    }

    fn screenshot(&self) -> Result<Vec<u8>, String> {
        self.run(CALL, |_| {
            // The screen he's using: the monitor of the window in front.
            let (front, _, _) = pick(&windows(), &[std::process::id()]);
            let window = front.map_or_else(|| unsafe { GetForegroundWindow() }, |front| handle(front.hwnd));
            screenshot::of_monitor(window)
        })
    }
}

/// Which mouse button a click is.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Button {
    Left,
    Right,
}

impl Session {
    fn start() -> Result<Session, String> {
        unsafe {
            let _ = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            CoInitializeEx(None, COINIT_MULTITHREADED).ok().map_err(|e| e.message())?;
            let automation: IUIAutomation =
                CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER).map_err(|e| e.message())?;
            // Don't wait long on an app that has stopped answering.
            if let Ok(newer) = automation.cast::<IUIAutomation2>() {
                let _ = newer.SetConnectionTimeout(2000);
                let _ = newer.SetTransactionTimeout(5000);
            }
            let cache = automation.CreateCacheRequest().map_err(|e| e.message())?;
            for property in PROPERTIES {
                cache.AddProperty(*property).map_err(|e| e.message())?;
            }
            cache.SetTreeScope(TreeScope_Element).map_err(|e| e.message())?;
            let onscreen = automation
                .CreatePropertyCondition(UIA_IsOffscreenPropertyId, yes_no(false).variant())
                .map_err(|e| e.message())?;
            let control_view = automation.ControlViewCondition().map_err(|e| e.message())?;
            let condition = automation.CreateAndCondition(&control_view, &onscreen).map_err(|e| e.message())?;
            Ok(Session { automation, cache, condition, elements: HashMap::new(), reads: VecDeque::new(), next: 1 })
        }
    }

    // Reading

    fn read(&mut self, everything: bool) -> Screen {
        let all = windows();
        let own = [std::process::id()];
        let (front, popups, others) = pick(&all, &own);
        let others: Vec<String> = others.into_iter().map(|w| w.title).collect();
        let Some(front) = front else {
            return Screen { other_windows: others, ..Default::default() };
        };
        let mut targets = popups;
        targets.push(front.clone());
        if everything {
            targets.extend(all.iter().filter(|w| w.class == TASKBAR_CLASS).cloned());
        }
        let mut raws: Vec<Raw> = targets.iter().flat_map(|w| self.raws(w, &front.title)).collect();
        let (mut kept, mut cut) = keep(raws, MAX_ELEMENTS);
        if front.class == CHROMIUM_CLASS && kept.len() < 5 {
            // Chromium (Edge, Chrome, Discord) shows a page to UI Automation only once it has been asked for it,
            // and not while its window is hidden behind others: in front, it comes on a second look.
            bring_to_front(front.hwnd);
            thread::sleep(Duration::from_millis(300));
            raws = targets.iter().flat_map(|w| self.raws(w, &front.title)).collect();
            (kept, cut) = keep(raws, MAX_ELEMENTS);
        }
        let mut handles = Vec::new();
        let elements: Vec<Element> = kept
            .into_iter()
            .map(|raw| {
                let handle = raw.element.clone().map_or(0, |found| {
                    let handle = self.next;
                    self.next += 1;
                    self.elements.insert(handle, found);
                    handles.push(handle);
                    handle
                });
                element(&raw, handle)
            })
            .collect();
        self.reads.push_back(handles);
        while self.reads.len() > READS_KEPT {
            for handle in self.reads.pop_front().unwrap_or_default() {
                self.elements.remove(&handle);
            }
        }
        let note = (elements.is_empty() && front.process.is_empty())
            .then(|| "this window runs as administrator, and Natro can't use those".to_string());
        Screen { window: Some(front.title), elements, other_windows: others, cut, hwnd: front.hwnd, note }
    }

    fn raws(&self, window: &Window, front_title: &str) -> Vec<Raw> {
        let found = unsafe {
            self.automation
                .ElementFromHandle(handle(window.hwnd))
                .and_then(|root| root.FindAllBuildCache(TreeScope_Descendants, &self.condition, &self.cache))
        };
        let Ok(found) = found else {
            return Vec::new(); // it closed while being read
        };
        let length = unsafe { found.Length() }.unwrap_or(0);
        let title = if window.title.is_empty() { front_title } else { &window.title };
        (0..length)
            .filter_map(|i| unsafe { found.GetElement(i) }.ok())
            .filter_map(|e| raw(e, title, window.hwnd).ok())
            .collect()
    }

    /// The element where it is now, or None when it has gone or scrolled out of sight (a few milliseconds).
    fn locate(&self, element: &Element) -> Option<Element> {
        let found = self.elements.get(&element.handle)?;
        unsafe {
            if found.CurrentIsOffscreen().map_or(true, |offscreen| offscreen.as_bool()) {
                return None;
            }
            let r = found.CurrentBoundingRectangle().ok()?;
            if r.right - r.left < 1 || r.bottom - r.top < 1 {
                return None;
            }
            Some(Element {
                x: (r.left + r.right).div_euclid(2),
                y: (r.top + r.bottom).div_euclid(2),
                ..element.clone()
            })
        }
    }

    // Acting

    /// A point of the element that is really its window's on screen (not under one of Natro's own
    /// always-on-top windows), or None.
    fn seen_point(&self, element: &Element) -> Option<(i32, i32)> {
        let rect = self
            .elements
            .get(&element.handle)
            .and_then(|found| unsafe { found.CurrentBoundingRectangle() }.ok())
            .map(|r| (r.left, r.top, r.right, r.bottom))
            .unwrap_or((element.x, element.y, element.x + 1, element.y + 1));
        let (left, top, right, bottom) = rect;
        let (width, height) = ((right - left) as f64, (bottom - top) as f64);
        for (fx, fy) in [(0.5, 0.5), (0.25, 0.5), (0.75, 0.5), (0.5, 0.25), (0.5, 0.75), (0.1, 0.5), (0.9, 0.5)] {
            let point = POINT { x: (left as f64 + width * fx) as i32, y: (top as f64 + height * fy) as i32 };
            if root_at(point) == element.hwnd {
                return Some((point.x, point.y));
            }
        }
        None
    }

    /// The app's own action for the element, as a screen reader does it; whether there was one.
    fn do_default(&self, element: &Element) -> bool {
        let Some(found) = self.elements.get(&element.handle) else {
            return false;
        };
        unsafe {
            let pattern = |id| found.GetCurrentPattern(id).ok();
            if let Some(p) = pattern(UIA_InvokePatternId).and_then(|p| p.cast::<IUIAutomationInvokePattern>().ok())
                && p.Invoke().is_ok()
            {
                return true;
            }
            if let Some(p) = pattern(UIA_TogglePatternId).and_then(|p| p.cast::<IUIAutomationTogglePattern>().ok())
                && p.Toggle().is_ok()
            {
                return true;
            }
            if let Some(p) =
                pattern(UIA_SelectionItemPatternId).and_then(|p| p.cast::<IUIAutomationSelectionItemPattern>().ok())
                && p.Select().is_ok()
            {
                return true;
            }
            pattern(UIA_LegacyIAccessiblePatternId)
                .and_then(|p| p.cast::<IUIAutomationLegacyIAccessiblePattern>().ok())
                .is_some_and(|p| p.DoDefaultAction().is_ok())
        }
    }

    fn click(&self, element: &Element, button: Button, clicks: u32) -> Result<(), String> {
        bring_to_front(element.hwnd);
        let Some((x, y)) = self.seen_point(element) else {
            if button == Button::Left && clicks == 1 && self.do_default(element) {
                return Ok(());
            }
            return Err(format!(
                "{} {} is covered by another window, and it has no action Natro can use without the mouse.",
                element.kind,
                quoted(&element.name)
            ));
        };
        let (down, up) = match button {
            Button::Left => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            Button::Right => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        };
        let presses: Vec<INPUT> = (0..clicks).flat_map(|_| [mouse_input(down, 0), mouse_input(up, 0)]).collect();
        mouse_kept(|| {
            unsafe {
                let _ = SetCursorPos(x, y);
            }
            send(&presses)
        })
    }

    fn type_text(&self, element: Option<&Element>, text: &str, clear: bool, enter: bool, window: isize)
    -> Result<(), String> {
        if !bring_to_front(element.map_or(window, |element| element.hwnd)) {
            return Err("Couldn't bring the window to the front to type into it.".into());
        }
        if let Some(element) = element {
            let focused = self.elements.get(&element.handle).is_some_and(|found| unsafe { found.SetFocus() }.is_ok());
            if !focused {
                self.click(element, Button::Left, 1)?;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let password = unsafe { self.automation.GetFocusedElement() }
            .and_then(|focused| unsafe { focused.CurrentIsPassword() })
            .is_ok_and(|password| password.as_bool());
        if password {
            return Err("That's a password field: Natro never types passwords. He can type it himself.".into());
        }
        let mut inputs = Vec::new();
        if clear && element.is_some() {
            let select_all = key_events(&[(VK_CONTROL, false), (b'A' as u16, false)]);
            inputs.extend(select_all.into_iter().map(|(code, extended, up)| key_input(code, extended, up)));
        }
        for typed in typing(text) {
            match typed {
                Typed::Char(unit) => inputs.extend([char_input(unit, false), char_input(unit, true)]),
                Typed::Key(code, extended, up) => inputs.push(key_input(code, extended, up)),
            }
        }
        if enter {
            inputs.extend([key_input(VK_RETURN, false, false), key_input(VK_RETURN, false, true)]);
        }
        send(&inputs)
    }

    fn key(&self, keys: &str, window: isize) -> Result<(), String> {
        if !bring_to_front(window) {
            return Err("Couldn't bring the window to the front to press keys in it.".into());
        }
        let events = key_events(&parse_keys(keys)?);
        send(&events.into_iter().map(|(code, extended, up)| key_input(code, extended, up)).collect::<Vec<_>>())
    }

    fn scroll(&self, element: Option<&Element>, down: bool, window: isize) -> Result<(), String> {
        let hwnd = element.map_or(window, |element| element.hwnd);
        bring_to_front(hwnd);
        let point = match element {
            Some(element) => self.seen_point(element),
            None => window_point(hwnd),
        };
        let Some((x, y)) = point else {
            let pattern = element
                .and_then(|element| self.elements.get(&element.handle))
                .and_then(|found| unsafe { found.GetCurrentPattern(UIA_ScrollPatternId) }.ok())
                .and_then(|pattern| pattern.cast::<IUIAutomationScrollPattern>().ok())
                .ok_or("What should scroll is covered by another window.")?;
            let amount = if down { ScrollAmount_LargeIncrement } else { ScrollAmount_LargeDecrement };
            return unsafe { pattern.Scroll(ScrollAmount_NoAmount, amount) }
                .map_err(|e| format!("The screen couldn't be used: {}", e.message()));
        };
        let delta = if down { -WHEEL_DELTA * 3 } else { WHEEL_DELTA * 3 };
        mouse_kept(|| {
            unsafe {
                let _ = SetCursorPos(x, y);
            }
            send(&[mouse_input(MOUSEEVENTF_WHEEL, delta as u32)])
        })
    }

    // Windows

    /// Bring the open window best matching the name to the front; its title.
    fn switch(&self, name: &str) -> Result<String, String> {
        let wanted = plain(name).to_lowercase();
        let squeezed = wanted.replace(' ', "");
        let his: Vec<Window> = visible(&windows(), &[std::process::id()])
            .into_iter()
            .filter(|w| !w.title.is_empty() && !w.tool)
            .collect();
        let score = |w: &Window| {
            let title = w.title.to_lowercase();
            let program = w.process.strip_suffix(".exe").unwrap_or(&w.process);
            if title == wanted {
                4
            } else if title.contains(&wanted) {
                3
            } else if program.contains(&squeezed) || (!program.is_empty() && squeezed.contains(program)) {
                2
            } else {
                0
            }
        };
        let mut best: Option<(i32, &Window)> = None;
        for w in &his {
            let score = score(w);
            if best.is_none_or(|(top, _)| score > top) {
                best = Some((score, w));
            }
        }
        let Some((score, best)) = best.filter(|(score, _)| *score > 0).map(|(score, w)| (score, w.clone())) else {
            let mut titles: Vec<&str> = Vec::new();
            for w in &his {
                if !titles.contains(&w.title.as_str()) {
                    titles.push(&w.title);
                }
            }
            let names = if titles.is_empty() { "none".to_string() } else { titles.join(", ") };
            return Err(format!("No open window matches {}. Open windows: {names}.", quoted(name)));
        };
        let _ = score;
        if !bring_to_front(best.hwnd) {
            return Err(format!("Windows didn't let Natro bring {} to the front.", quoted(&best.title)));
        }
        Ok(best.title)
    }
}

/// One element's cached properties.
fn raw(e: IUIAutomationElement, window: &str, hwnd: isize) -> windows::core::Result<Raw> {
    unsafe {
        let cached = |id| e.GetCachedPropertyValue(id).map(Owned::from);
        let has = |id| cached(id).map(|value| value.boolean());
        let r = e.CachedBoundingRectangle()?;
        // Some apps (Chromium) give a state for patterns an element doesn't have: only asked when it has them.
        let value = match has(UIA_IsValuePatternAvailablePropertyId)? {
            true => cached(UIA_ValueValuePropertyId)?.text(),
            false => None,
        };
        let toggle = match has(UIA_IsTogglePatternAvailablePropertyId)? {
            true => cached(UIA_ToggleToggleStatePropertyId)?.integer(),
            false => None,
        };
        let expand = if has(UIA_IsExpandCollapsePatternAvailablePropertyId)? {
            // 3: a leaf, nothing to open.
            cached(UIA_ExpandCollapseExpandCollapseStatePropertyId)?.integer().filter(|state| *state != 3)
        } else {
            None
        };
        let scrolls = has(UIA_IsScrollPatternAvailablePropertyId)?
            && cached(UIA_ScrollVerticallyScrollablePropertyId)?.boolean();
        Ok(Raw {
            kind: e.CachedControlType()?.0,
            name: e.CachedName()?.to_string(),
            rect: (r.left, r.top, r.right, r.bottom),
            window: window.to_string(),
            hwnd,
            focused: e.CachedHasKeyboardFocus()?.as_bool(),
            password: e.CachedIsPassword()?.as_bool(),
            enabled: e.CachedIsEnabled()?.as_bool(),
            focusable: e.CachedIsKeyboardFocusable()?.as_bool(),
            invokable: has(UIA_IsInvokePatternAvailablePropertyId)?,
            value,
            toggle,
            expand,
            scrolls,
            element: Some(e),
        })
    }
}

/// The top-level window at a point.
fn root_at(point: POINT) -> isize {
    unsafe { GetAncestor(WindowFromPoint(point), GA_ROOT).0 as isize }
}

/// The middle of a window, if that's really it on screen.
fn window_point(hwnd: isize) -> Option<(i32, i32)> {
    let mut rect = RECT::default();
    unsafe { GetWindowRect(handle(hwnd), &mut rect) }.ok()?;
    let point = POINT { x: (rect.left + rect.right).div_euclid(2), y: (rect.top + rect.bottom).div_euclid(2) };
    (root_at(point) == hwnd).then_some((point.x, point.y))
}

/// Bring the window to the front; whether it is.
fn bring_to_front(hwnd: isize) -> bool {
    let window = handle(hwnd);
    unsafe {
        if hwnd == 0 || GetForegroundWindow() == window {
            return hwnd != 0;
        }
        if IsIconic(window).as_bool() {
            let _ = ShowWindow(window, SW_RESTORE);
        }
        let front = GetForegroundWindow();
        let theirs = if front.is_invalid() { 0 } else { GetWindowThreadProcessId(front, None) };
        let mine = GetCurrentThreadId();
        let attached = theirs != 0 && theirs != mine && AttachThreadInput(mine, theirs, true).as_bool();
        let _ = BringWindowToTop(window);
        let _ = SetForegroundWindow(window);
        if attached {
            let _ = AttachThreadInput(mine, theirs, false);
        }
        if became_front(window) {
            return true;
        }
        // Windows lets a key press through.
        let _ = send(&[key_input(VK_MENU, false, false), key_input(VK_MENU, false, true)]);
        let _ = SetForegroundWindow(window);
        became_front(window)
    }
}

fn became_front(window: HWND) -> bool {
    let end = Instant::now() + Duration::from_millis(500);
    while Instant::now() < end {
        if unsafe { GetForegroundWindow() } == window {
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    fn win(hwnd: isize, title: &str, pid: u32, process: &str, class: &str) -> Window {
        Window { hwnd, title: title.into(), pid, process: process.into(), class: class.into(), ..Default::default() }
    }

    #[test]
    fn natros_own_windows_are_never_the_one_in_front() {
        let windows = vec![
            win(1, "Natro chat", 50, "natro.exe", "App"), // he just typed to her: hers is in front
            Window { tool: true, ..win(2, "", 20, "msedge.exe", "App") }, // Edge's open menu
            win(3, "Video - YouTube - Microsoft Edge", 20, "msedge.exe", CHROMIUM_CLASS),
            win(4, "", 30, "app.exe", "Shell_TrayWnd"),
            win(5, "general - Discord", 40, "discord.exe", "App"),
            Window { minimized: true, ..win(6, "Notes", 60, "app.exe", "App") },
            win(7, "Program Manager", 70, "explorer.exe", "Progman"),
        ];
        let (front, popups, others) = pick(&windows, &[99]);
        assert_eq!(front.unwrap().title, "Video - YouTube - Microsoft Edge");
        assert_eq!(popups.iter().map(|w| w.hwnd).collect::<Vec<_>>(), [2]);
        assert_eq!(others.iter().map(|w| w.title.as_str()).collect::<Vec<_>>(), ["general - Discord", "Notes"]);
        // The app's own process's windows are left out too, whatever it's called.
        let mut mine = vec![win(8, "Natro", 99, "natro-dev.exe", "App")];
        mine.extend(windows[2..].iter().cloned());
        assert_eq!(pick(&mine, &[99]).0.unwrap().hwnd, 3);
    }

    #[test]
    fn no_window_of_his() {
        let (front, popups, others) =
            pick(&[win(1, "Natro chat", 10, "natro.exe", "App"), win(4, "", 10, "app.exe", "Shell_TrayWnd")], &[]);
        assert!(front.is_none() && popups.is_empty() && others.is_empty());
    }

    fn raw(kind: i32, name: &str, rect: (i32, i32, i32, i32)) -> Raw {
        Raw { kind, name: name.into(), rect, window: "App".into(), hwnd: 1, enabled: true, ..Default::default() }
    }

    fn lines(kept: &[Raw]) -> Vec<String> {
        kept.iter().enumerate().map(|(i, raw)| element(raw, 0).line(i + 1)).collect()
    }

    #[test]
    fn what_gets_a_number() {
        let small = (0, 0, 100, 20);
        let (kept, cut) = keep(
            vec![
                raw(50000, "Send", (0, 0, 100, 30)),
                raw(50020, "Send", (10, 5, 90, 25)), // the button's own label: not twice
                raw(50020, "see you at 8", small),   // a chat message
                raw(50020, "", small),               // empty text
                Raw { focusable: true, ..raw(50026, "", small) }, // an unnamed group
                Raw { focusable: true, ..raw(50026, "Add a comment…", small) }, // a focusable placeholder
                raw(50033, "Main", (0, 0, 500, 500)), // a pane: layout only
                Raw { value: Some("cats".into()), focused: true, ..raw(50004, "Search", small) },
                Raw { toggle: Some(1), ..raw(50002, "Notifications", small) },
                Raw { expand: Some(0), ..raw(50024, "Text channels", small) },
                Raw { enabled: false, ..raw(50000, "Save", small) },
                raw(50000, "Hidden", (10, 10, 10, 30)), // no size
            ],
            MAX_ELEMENTS,
        );
        assert!(!cut);
        assert_eq!(
            lines(&kept),
            [
                "[1] button: Send",
                "[2] text: see you at 8",
                "[3] group: Add a comment…",
                "[4] field: Search (focused, value \"cats\")",
                "[5] check box: Notifications (on)",
                "[6] tree item: Text channels (collapsed)",
                "[7] button: Save (disabled)",
            ]
        );
        let first = element(&kept[0], 0);
        assert_eq!((first.x, first.y), (50, 15));
        // Left of the main screen, the middle is rounded down as Python's // did.
        assert_eq!(element(&raw(50000, "x", (-101, 0, 0, 2)), 0).x, -51);
    }

    #[test]
    fn a_long_screen_drops_text_before_buttons() {
        let mut raws = Vec::new();
        for i in 0..120 {
            raws.push(raw(50020, &format!("message {i}"), (0, i * 40, 100, i * 40 + 20)));
            raws.push(raw(50000, &format!("button {i}"), (200, i * 40, 300, i * 40 + 20)));
        }
        let (kept, cut) = keep(raws, MAX_ELEMENTS);
        assert!(cut && kept.len() == MAX_ELEMENTS);
        assert_eq!(kept.iter().filter(|raw| raw.kind == 50000).count(), 120); // every button kept
        let names: Vec<&str> = kept[..3].iter().map(|raw| raw.name.as_str()).collect();
        assert_eq!(names, ["message 0", "button 0", "message 1"]); // still in screen order
    }

    #[test]
    fn titles_lose_invisible_characters() {
        assert_eq!(plain("Video - Microsoft\u{200B} Edge\n"), "Video - Microsoft Edge");
    }

    /// By hand, on the PC: cargo test -- --ignored --nocapture reads_this_screen prints the
    /// window in front as Natro reads it, how long that took, and saves a screenshot.
    #[test]
    #[ignore]
    fn reads_this_screen() {
        let uia = Uia::new();
        uia.warm_up();
        for _ in 0..3 {
            let started = Instant::now();
            let screen = uia.read(false).unwrap();
            println!("{} elements of {:?} in {:?}", screen.elements.len(), screen.window, started.elapsed());
        }
        let screen = uia.read(false).unwrap();
        println!("WINDOW {:?}", screen.window);
        for (i, element) in screen.elements.iter().enumerate() {
            println!("LINE {} at {},{}", element.line(i + 1), element.x, element.y);
        }
        let started = Instant::now();
        let jpeg = uia.screenshot().unwrap();
        println!("screenshot: {} KB in {:?}", jpeg.len() / 1024, started.elapsed());
        std::fs::write(std::env::temp_dir().join("natro-screenshot-test.jpg"), jpeg).unwrap();
    }
}
