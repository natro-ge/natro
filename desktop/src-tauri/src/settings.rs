//! The Settings window (Settings.svelte): the chat's + menu and the tray's "Settings…" open it.
//!
//! Its switches are the tray's (Speak replies, Show replies as text, Verbose): a change in
//! either shows in both, and they're remembered in settings.json (git-ignored) beside .env,
//! with its own, Start with Windows (startup.rs).
//! So are the keybinds, the keys that work from any window (talk, chat, yes and no): a click
//! on one, then the new keys, changes it at once (Esc keeps it). Below them are the keys,
//! behind a PIN (keys.rs).
//!
//! Unlike the dock, the reply box and the chat, it's an ordinary window, made when it opens
//! and gone when it closes: it stays put while he copies a new key from the browser, and
//! it's in the taskbar and Alt+Tab. Esc or its ✕ closes it, and that locks the keys.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::Ordering;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

use crate::chat::Chat;
use crate::dock::Dock;
use crate::helper::Helper;
use crate::keys::Keys;
use crate::reply::Reply;
use crate::startup;
use crate::tray;
use crate::verbose::Verbose;

const LABEL: &str = "settings";
/// The window (logical pixels): the card, and room for its shadow. Keep in step with Settings.svelte.
/// It's as tall as the page says, from SHORTEST to TALLEST, and within the screen less MARGIN
/// (then the fields scroll).
const WIDTH: f64 = 480.0;
const TALLEST: f64 = 780.0;
const SHORTEST: f64 = 240.0;
const MARGIN: f64 = 40.0;

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq)]
#[serde(default)]
pub struct Switches {
    /// "Speak replies": the Python helper says Natro's replies.
    pub speak: bool,
    /// "Show replies as text": the reply box (reply.rs).
    pub replies: bool,
    /// "Verbose": every request is kept in logs/verbose (verbose.rs).
    pub verbose: bool,
    /// "Start with Windows": Natro starts when he signs in (startup.rs).
    pub startup: bool,
}

impl Default for Switches {
    fn default() -> Switches {
        Switches {
            speak: true,
            replies: true,
            verbose: false,
            startup: false,
        }
    }
}

/// The keys that work from any window (lib.rs), remembered in settings.json under "keybinds"
/// as the shortcut plugin writes them ("control+alt+Space").
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq)]
#[serde(default)]
pub struct Keybinds {
    /// Opens the dock and listens; see talk_key.rs for hold and tap.
    pub talk: Shortcut,
    /// Opens the chat, or closes it.
    pub chat: Shortcut,
    /// Yes and no while Natro asks: taken from other apps only then (dock.rs).
    pub yes: Shortcut,
    pub no: Shortcut,
}

impl Default for Keybinds {
    fn default() -> Keybinds {
        let ctrl_alt = Some(Modifiers::CONTROL | Modifiers::ALT);
        Keybinds {
            talk: Shortcut::new(ctrl_alt, Code::Space),
            chat: Shortcut::new(Some(Modifiers::CONTROL), Code::Space),
            yes: Shortcut::new(ctrl_alt, Code::KeyY),
            no: Shortcut::new(ctrl_alt, Code::KeyN),
        }
    }
}

/// The keybinds by name, as the window and settings.json say them, and what each does.
const KEYBINDS: [(&str, &str); 4] = [
    ("talk", "talks to Natro"),
    ("chat", "opens the chat"),
    ("yes", "answers yes"),
    ("no", "answers no"),
];

impl Keybinds {
    fn get(&self, name: &str) -> Option<Shortcut> {
        match name {
            "talk" => Some(self.talk),
            "chat" => Some(self.chat),
            "yes" => Some(self.yes),
            "no" => Some(self.no),
            _ => None,
        }
    }

    fn get_mut(&mut self, name: &str) -> Option<&mut Shortcut> {
        match name {
            "talk" => Some(&mut self.talk),
            "chat" => Some(&mut self.chat),
            "yes" => Some(&mut self.yes),
            "no" => Some(&mut self.no),
            _ => None,
        }
    }

    /// Why `key` can't be `name`'s, if it can't: a key alone would be taken from his typing in
    /// every app, Esc closes Natro's windows, and two keybinds can't share keys.
    fn check(&self, name: &str, key: Shortcut) -> Result<(), String> {
        if key.key == Code::Escape {
            return Err("Esc closes Natro's windows: choose other keys.".into());
        }
        if !key.mods.intersects(Modifiers::CONTROL | Modifiers::ALT | Modifiers::SUPER) {
            return Err("Use Ctrl, Alt or Win with it, so it doesn't take a key you type with.".into());
        }
        for (other, does) in KEYBINDS {
            if other != name && self.get(other) == Some(key) {
                return Err(format!("{} already {does}.", label(key)));
            }
        }
        Ok(())
    }
}

/// Keys as he'd say them: "Ctrl+Alt+Space".
pub fn label(key: Shortcut) -> String {
    let mut parts: Vec<String> = [
        (Modifiers::CONTROL, "Ctrl"),
        (Modifiers::ALT, "Alt"),
        (Modifiers::SHIFT, "Shift"),
        (Modifiers::SUPER, "Win"),
    ]
    .into_iter()
    .filter(|(modifier, _)| key.mods.contains(*modifier))
    .map(|(_, name)| name.to_string())
    .collect();
    let code = key.key.to_string();
    let name = match code.as_str() {
        "Backquote" => "`",
        "Minus" => "-",
        "Equal" => "=",
        "BracketLeft" => "[",
        "BracketRight" => "]",
        "Backslash" => "\\",
        "Semicolon" => ";",
        "Quote" => "'",
        "Comma" => ",",
        "Period" => ".",
        "Slash" => "/",
        _ => ["Key", "Digit", "Arrow"]
            .iter()
            .find_map(|prefix| code.strip_prefix(prefix))
            .unwrap_or(&code),
    };
    parts.push(name.replace("Numpad", "Num "));
    parts.join("+")
}

/// A keybind, for the window: its keys, whether they're the first ones, and whether another
/// program holds them (so they do nothing).
#[derive(Serialize)]
pub struct Keybind {
    name: &'static str,
    keys: String,
    default: bool,
    taken: bool,
}

pub struct Settings {
    path: PathBuf,
    switches: Mutex<Switches>,
    keybinds: Mutex<Keybinds>,
    /// The keybinds another program holds: they couldn't be taken.
    taken: Mutex<Vec<&'static str>>,
}

/// settings.json: the switches, and the keybinds under "keybinds".
#[derive(Serialize)]
struct Saved {
    #[serde(flatten)]
    switches: Switches,
    keybinds: Keybinds,
}

#[derive(Serialize, Clone)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum SettingsEvent {
    Switches { switches: Switches },
    Online { online: bool },
}

impl Settings {
    fn new(path: PathBuf, switches: Switches, keybinds: Keybinds) -> Settings {
        Settings {
            path,
            switches: Mutex::new(switches),
            keybinds: Mutex::new(keybinds),
            taken: Mutex::new(Vec::new()),
        }
    }

    /// From settings.json; the first time, from the marker files that came before it (logs/).
    pub fn load(home: &Path) -> Settings {
        let path = home.join("settings.json");
        let settings = match fs::read_to_string(&path) {
            Ok(text) => {
                let saved = serde_json::from_str::<Value>(&text).unwrap_or_else(|e| {
                    log::warn!("settings.json isn't readable ({e}): the defaults instead");
                    Value::Null
                });
                Settings::new(path, part(&saved, "switches"), part(&saved["keybinds"], "keybinds"))
            }
            Err(_) => {
                let logs = home.join("logs");
                let (verbose, reply_off) = (logs.join("verbose.on"), logs.join("reply-text.off"));
                let switches = Switches {
                    replies: !reply_off.is_file(),
                    verbose: verbose.is_file(),
                    ..Switches::default()
                };
                let settings = Settings::new(path, switches, Keybinds::default());
                if settings.save() {
                    let _ = fs::remove_file(verbose);
                    let _ = fs::remove_file(reply_off);
                }
                settings
            }
        };
        settings
    }

    pub fn switches(&self) -> Switches {
        *self.switches.lock().unwrap()
    }

    pub fn keybinds(&self) -> Keybinds {
        *self.keybinds.lock().unwrap()
    }

    fn shown_keybinds(&self) -> Vec<Keybind> {
        let (keybinds, first) = (self.keybinds(), Keybinds::default());
        let taken = self.taken.lock().unwrap();
        KEYBINDS
            .iter()
            .map(|&(name, _)| Keybind {
                name,
                keys: keybinds.get(name).map(label).unwrap_or_default(),
                default: keybinds.get(name) == first.get(name),
                taken: taken.contains(&name),
            })
            .collect()
    }

    /// A switch changed, in the tray or the window: it takes effect, is remembered, and both show it.
    /// Start with Windows (the window's only) stays as it was if Windows won't have it.
    pub fn set(&self, app: &AppHandle, name: &str, on: bool) {
        if name == "startup" {
            if let Err(e) = startup::set(on) {
                log::warn!("couldn't change starting with Windows: {e}");
                let _ = app.emit_to(LABEL, LABEL, SettingsEvent::Switches { switches: self.switches() });
                return;
            }
        }
        let now = {
            let mut switches = self.switches.lock().unwrap();
            match name {
                "speak" => switches.speak = on,
                "replies" => switches.replies = on,
                "verbose" => switches.verbose = on,
                "startup" => switches.startup = on,
                _ => return,
            }
            *switches
        };
        match name {
            "speak" => app.state::<Arc<Helper>>().speaking.store(on, Ordering::SeqCst),
            "replies" => app.state::<Reply>().set_on(app, on),
            "verbose" => app.state::<Arc<Verbose>>().set(on),
            _ => {}
        }
        self.save();
        tray::show_switches(app, &now);
        let _ = app.emit_to(LABEL, LABEL, SettingsEvent::Switches { switches: now });
    }

    fn save(&self) -> bool {
        let saved = Saved {
            switches: self.switches(),
            keybinds: self.keybinds(),
        };
        let text = serde_json::to_string_pretty(&saved).unwrap_or_default();
        match fs::write(&self.path, text + "\n") {
            Ok(()) => true,
            Err(e) => {
                log::warn!("couldn't save the settings in {}: {e}", self.path.display());
                false
            }
        }
    }
}

/// One part of settings.json, or its defaults if it's missing or isn't readable.
fn part<T: DeserializeOwned + Default>(value: &Value, what: &str) -> T {
    if value.is_null() {
        return T::default();
    }
    serde_json::from_value(value.clone()).unwrap_or_else(|e| {
        log::warn!("settings.json's {what} aren't readable ({e}): the defaults instead");
        T::default()
    })
}

/// Takes the talk and chat keys from other apps (at the start, and once the window has the new
/// keys), or lets them go (while it waits for new keys, so that pressing them reaches it).
/// Never with a lock held: from another thread, registering waits for the main thread, where
/// the keys' handler reads the keybinds. Yes and no are taken only while Natro asks (dock.rs).
pub fn hold_keys(app: &AppHandle, hold: bool) {
    let settings = app.state::<Settings>();
    let keybinds = settings.keybinds();
    let shortcuts = app.global_shortcut();
    let mut taken = Vec::new();
    for (name, key) in [("talk", keybinds.talk), ("chat", keybinds.chat)] {
        if !hold {
            if shortcuts.is_registered(key) {
                let _ = shortcuts.unregister(key);
            }
        } else if !shortcuts.is_registered(key) {
            if let Err(e) = shortcuts.register(key) {
                log::error!("{} is taken by another program: {e}", label(key));
                taken.push(name);
            }
        }
    }
    if hold {
        *settings.taken.lock().unwrap() = taken;
    }
}

/// New keybinds: the old keys go back to the other apps, and the new ones are taken.
fn change(app: &AppHandle, keybinds: Keybinds) {
    hold_keys(app, false);
    let settings = app.state::<Settings>();
    let old = std::mem::replace(&mut *settings.keybinds.lock().unwrap(), keybinds);
    settings.save();
    hold_keys(app, true);
    app.state::<Dock>().answer_keys_changed(app, old);
    tray::show_keys(app, &keybinds);
}

/// Opens the window (or brings it forward), in the middle of the screen. From the chat's cog, the chat
/// closes first: it would cover the window's lower part.
pub fn open(app: &AppHandle) {
    app.state::<Chat>().close(app, false);
    if let Some(window) = app.get_webview_window(LABEL) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let made = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("settings.html".into()))
        .title("Natro settings")
        .inner_size(WIDTH, TALLEST)
        .center()
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(false)
        .maximizable(false)
        .focused(true)
        .build();
    match made {
        Ok(window) => {
            square_corners(&window);
            let app = app.clone();
            window.on_window_event(move |event| {
                if let WindowEvent::Destroyed = event {
                    app.state::<Keys>().lock(&app);
                    hold_keys(&app, true); // it may have been waiting for new keys
                }
            });
        }
        Err(e) => log::error!("the Settings window couldn't open: {e}"),
    }
}

/// Natro connected or not: the window says whether the server's keys can be changed.
pub fn show_connection(app: &AppHandle, online: bool) {
    let _ = app.emit_to(LABEL, LABEL, SettingsEvent::Online { online });
}

/// Windows 11 would round the (see-through) window and draw an edge: the page draws the card itself.
fn square_corners(window: &WebviewWindow) {
    use windows::Win32::Graphics::Dwm::{
        DWM_WINDOW_CORNER_PREFERENCE, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND,
        DwmSetWindowAttribute,
    };
    let Ok(hwnd) = window.hwnd() else { return };
    let corners = DWMWCP_DONOTROUND;
    unsafe {
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            (&corners as *const DWM_WINDOW_CORNER_PREFERENCE).cast(),
            size_of::<DWM_WINDOW_CORNER_PREFERENCE>() as u32,
        );
    }
}

/// What the window shows when it loads.
#[derive(Serialize)]
pub struct SettingsState {
    switches: Switches,
    keybinds: Vec<Keybind>,
    online: bool,
}

#[tauri::command]
pub fn settings_state(app: AppHandle) -> SettingsState {
    let settings = app.state::<Settings>();
    SettingsState {
        switches: settings.switches(),
        keybinds: settings.shown_keybinds(),
        online: app.state::<crate::brain::Brain>().online(),
    }
}

/// The window waits for new keys (on), or stopped waiting (off): meanwhile Natro lets hers go,
/// so that pressing them reaches the window. Synchronous, as the others that take keys: on the
/// main thread, where keys are taken, one after the other in the order the page asked.
#[tauri::command]
pub fn settings_capture(app: AppHandle, on: bool) -> Vec<Keybind> {
    hold_keys(&app, !on);
    app.state::<Settings>().shown_keybinds()
}

/// New keys for a keybind, as the page writes them ("control+alt+KeyT"): taken at once, or why not.
#[tauri::command]
pub fn settings_keybind(app: AppHandle, name: String, keys: String) -> Result<Vec<Keybind>, String> {
    let settings = app.state::<Settings>();
    let key = keys
        .parse::<Shortcut>()
        .map_err(|_| "Natro can't use that key: choose another.".to_string())?;
    let mut keybinds = settings.keybinds();
    keybinds.check(&name, key)?;
    let Some(slot) = keybinds.get_mut(&name) else {
        return Err(format!("There's no keybind called {name}."));
    };
    if *slot != key {
        // Another program's? Natro holds none of the others now: the window is waiting.
        let shortcuts = app.global_shortcut();
        if !shortcuts.is_registered(key) {
            if let Err(e) = shortcuts.register(key) {
                log::warn!("{} for {name}: {e}", label(key));
                return Err(format!("{} is taken by another program: choose other keys.", label(key)));
            }
            let _ = shortcuts.unregister(key);
        }
        *slot = key;
        log::info!("the {name} keybind is now {}", label(key));
        change(&app, keybinds);
    }
    Ok(settings.shown_keybinds())
}

/// The keybinds back to the first ones.
#[tauri::command]
pub fn settings_keybinds_reset(app: AppHandle) -> Vec<Keybind> {
    log::info!("the keybinds are back to the first ones");
    change(&app, Keybinds::default());
    app.state::<Settings>().shown_keybinds()
}

#[tauri::command]
pub fn settings_switch(app: AppHandle, name: String, on: bool) {
    app.state::<Settings>().set(&app, &name, on);
}

/// How tall the page is (logical pixels): the window fits it, growing downwards. The first time
/// (center), it moves to the middle of the screen too, before the card opens from there.
#[tauri::command]
pub fn settings_size(app: AppHandle, height: f64, center: bool) {
    if let Some(window) = app.get_webview_window(LABEL) {
        let screen = window.current_monitor().ok().flatten().map(|monitor| {
            monitor.work_area().size.to_logical::<f64>(monitor.scale_factor()).height - MARGIN
        });
        let tallest = screen.unwrap_or(TALLEST).clamp(SHORTEST, TALLEST);
        let _ = window.set_size(LogicalSize::new(WIDTH, height.clamp(SHORTEST, tallest)));
        if center {
            let _ = window.center();
        }
    }
}

/// The chat's + menu. Async: a window made in a synchronous command can deadlock on Windows.
#[tauri::command]
pub async fn settings_open(app: AppHandle) {
    open(&app);
}

/// Esc or the ✕: the window goes, and the keys lock.
#[tauri::command]
pub fn settings_close(app: AppHandle) {
    if let Some(window) = app.get_webview_window(LABEL) {
        let _ = window.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(test: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("natro-settings-{}-{test}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("logs")).unwrap();
        dir
    }

    #[test]
    fn the_markers_from_before_are_taken_once() {
        let home = temp("markers");
        fs::write(home.join("logs").join("verbose.on"), "on\n").unwrap();
        fs::write(home.join("logs").join("reply-text.off"), "off\n").unwrap();
        let expected = Switches {
            speak: true,
            replies: false,
            verbose: true,
            startup: false,
        };
        assert_eq!(Settings::load(&home).switches(), expected);
        assert!(!home.join("logs").join("verbose.on").exists());
        assert!(!home.join("logs").join("reply-text.off").exists());
        assert_eq!(Settings::load(&home).switches(), expected, "now from settings.json");
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn switches_are_remembered_and_missing_ones_take_their_default() {
        let home = temp("remembered");
        assert_eq!(Settings::load(&home).switches(), Switches::default());
        fs::write(home.join("settings.json"), r#"{"speak": false}"#).unwrap();
        let settings = Settings::load(&home);
        assert_eq!(
            settings.switches(),
            Switches {
                speak: false,
                ..Switches::default()
            }
        );
        let all_off = Switches {
            speak: false,
            replies: false,
            verbose: false,
            startup: false,
        };
        *settings.switches.lock().unwrap() = all_off;
        assert!(settings.save());
        assert_eq!(Settings::load(&home).switches(), all_off);
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn keybinds_are_remembered_beside_the_switches() {
        let home = temp("keybinds");
        let settings = Settings::load(&home);
        assert_eq!(settings.keybinds(), Keybinds::default());
        let talk = "control+shift+KeyT".parse::<Shortcut>().unwrap();
        settings.keybinds.lock().unwrap().talk = talk;
        settings.switches.lock().unwrap().speak = false;
        assert!(settings.save());
        let text = fs::read_to_string(home.join("settings.json")).unwrap();
        assert!(text.contains(r#""speak": false"#), "the switches stay at the top: {text}");
        assert!(text.contains(r#""talk": "shift+control+KeyT""#), "{text}");
        let again = Settings::load(&home);
        assert_eq!(again.keybinds().talk, talk);
        assert_eq!(again.keybinds().chat, Keybinds::default().chat);
        assert!(!again.switches().speak);

        // Keybinds that aren't readable don't cost the switches.
        let broken = r#"{"speak": false, "keybinds": {"talk": "control+Nonsense"}}"#;
        fs::write(home.join("settings.json"), broken).unwrap();
        let settings = Settings::load(&home);
        assert_eq!(settings.keybinds(), Keybinds::default());
        assert!(!settings.switches().speak);
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn a_keybind_needs_ctrl_alt_or_win_and_keys_of_its_own() {
        let keybinds = Keybinds::default();
        let keys = |text: &str| text.parse::<Shortcut>().unwrap();
        assert_eq!(keybinds.check("talk", keys("control+alt+KeyT")), Ok(()));
        assert_eq!(keybinds.check("talk", keys("super+KeyT")), Ok(()));
        assert_eq!(keybinds.check("talk", keybinds.talk), Ok(()), "its own keys again");
        assert!(keybinds.check("talk", keys("KeyT")).unwrap_err().contains("Ctrl, Alt or Win"));
        assert!(keybinds.check("talk", keys("shift+KeyT")).unwrap_err().contains("Ctrl, Alt or Win"));
        assert!(keybinds.check("chat", keys("control+Escape")).unwrap_err().starts_with("Esc"));
        assert_eq!(
            keybinds.check("talk", keybinds.chat),
            Err("Ctrl+Space already opens the chat.".to_string())
        );
        assert_eq!(
            keybinds.check("no", keybinds.yes),
            Err("Ctrl+Alt+Y already answers yes.".to_string())
        );
    }

    #[test]
    fn keys_are_labelled_as_said() {
        let keybinds = Keybinds::default();
        assert_eq!(label(keybinds.talk), "Ctrl+Alt+Space");
        assert_eq!(label(keybinds.yes), "Ctrl+Alt+Y");
        let keys = |text: &str| label(text.parse::<Shortcut>().unwrap());
        assert_eq!(keys("shift+control+Digit1"), "Ctrl+Shift+1");
        assert_eq!(keys("super+alt+ArrowUp"), "Alt+Win+Up");
        assert_eq!(keys("control+Numpad5"), "Ctrl+Num 5");
        assert_eq!(keys("control+Slash"), "Ctrl+/");
        assert_eq!(keys("alt+F5"), "Alt+F5");
    }
}
