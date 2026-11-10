//! Using apps on the PC by their screen: read it, click, type, scroll, press keys (the tools
//! screen, find, act, act_risky, switch_window, wait_for and screenshot).
//!
//! Like the phone's screen control: screen lists the elements of the window in front, numbered
//! ("[4] button: Send"); act clicks, types, scrolls or presses keys by number, several in one
//! call, and returns the new screen. A click or key that looks like sending, buying, paying or
//! deleting is refused there and goes through act_risky, which the brain asks him about first.
//! Passwords are never typed.
//!
//! The screen is read through Windows UI Automation (uia.rs): the window he was using, never
//! Natro's own, in one pass of about 0.2 s. Before acting, each element is found where it is now
//! (a few milliseconds), so a click never lands on something that has moved, and one that has
//! gone is looked for again on a fresh read.
//!
//! Ported from the Python helper's control.py (cleanup step 5.4).

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;
use std::thread;
use std::time::Duration;

use regex::{Regex, RegexBuilder};
use serde_json::{Value, json};
use windows::Win32::System::SystemInformation::GetLocalTime;

use super::input;
use super::uia::Button;
use super::{Picture, is_space, number_arg, quoted, spec, text_arg, text_of, truthy, written};

const LABEL_CHARS: usize = 60;
/// After acting, before reading the screen again.
const SETTLE: Duration = Duration::from_millis(400);
/// The same words as the phone's (Text.RISKY in android/.../Speech.kt; a test keeps them the same).
const RISKY_PATTERN: &str = "\\b(send|buy|purchase|pay|payment|order|checkout|check out|delete|remove|erase|clear all|\
    uninstall|transfer|submit|post|publish|share|call|subscribe|unsubscribe|book|block|report|sign out|log out)\\b|\
    გაგზავნ|წაშალ|წაშლა|ყიდვა|იყიდე|გადახდ|შეკვეთ|გაზიარ|დარეკ";
/// Where Enter sends what was typed: a chat or mail app, or a screen with a Send button.
const MESSAGING_PATTERN: &str =
    "discord|whatsapp|telegram|messenger|slack|teams|signal|viber|skype|outlook|gmail|\\bmail\\b";
const SENDING_PATTERN: &str = "\\b(send|post|reply|submit|publish|comment)\\b|გაგზავნ";

fn pattern(text: &str) -> Regex {
    RegexBuilder::new(text).case_insensitive(true).build().expect("a valid pattern")
}

/// The browser's too (browser.rs).
pub(super) static RISKY: LazyLock<Regex> = LazyLock::new(|| pattern(RISKY_PATTERN));
static MESSAGING: LazyLock<Regex> = LazyLock::new(|| pattern(MESSAGING_PATTERN));
static SENDING: LazyLock<Regex> = LazyLock::new(|| pattern(SENDING_PATTERN));

/// The tools as the brain sees them: in desktop/tools.json, after the commands'.
pub fn specs() -> Vec<Value> {
    let number = "The element's number. type: the field (leave it out to type where the cursor is); scroll: what to \
                  scroll (default the window).";
    let action = json!({"type": "object", "required": ["do"], "properties": {
        "do": {"type": "string", "enum": ["click", "double_click", "right_click", "type", "key", "scroll"]},
        "element": {"type": "integer", "description": number},
        "text": {"type": "string", "description": "type: what to type (a line break is Shift+Enter, a new line). \
                                                    key: the keys, like ctrl+s, enter, alt+tab, page down."},
        "clear": {"type": "boolean",
                  "description": "type into a numbered field: replace what's in it (default true)."},
        "enter": {"type": "boolean", "description": "type: press Enter after typing."},
        "direction": {"type": "string", "enum": ["up", "down"], "description": "scroll: default down."},
    }});
    let actions = json!({"actions": {"type": "array", "items": action, "description": "Done in this order."}});
    vec![
        spec(
            "screen",
            "Read what's on the PC's screen now: the window he is using (web pages included) and its elements, \
             numbered (buttons, fields, links, list items, text), to act on by number.",
            &json!({"everything": {"type": "boolean", "description": "Also the taskbar."}}),
            &[],
            false,
            false,
        ),
        spec(
            "find",
            "Find elements on the PC's screen by their text: only those, numbered as on the screen.",
            &json!({"text": {"type": "string"}, "everything": {"type": "boolean"}}),
            &["text"],
            false,
            false,
        ),
        spec(
            "act",
            "Do one or more things on the PC's screen, in order, by element number: click, double_click, \
             right_click, type (into a field, or where the cursor is), key, scroll. Returns the new screen. Refuses \
             what looks like sending, buying, paying or deleting (a Send button, Enter in a chat): use act_risky for \
             those.",
            &actions,
            &["actions"],
            false,
            false,
        ),
        spec(
            "act_risky",
            "The same as act, for a click or key that sends, buys, pays or deletes.",
            &actions,
            &["actions"],
            true,
            false,
        ),
        spec(
            "switch_window",
            "Bring an open window to the front by its title or app name; returns its screen.",
            &json!({"name": {"type": "string"}}),
            &["name"],
            false,
            false,
        ),
        spec(
            "wait_for",
            "Wait until some text shows on the PC's screen (a page loading, a dialog), up to 20 seconds; returns \
             the screen.",
            &json!({"text": {"type": "string"}, "seconds": {"type": "integer"}}),
            &["text"],
            false,
            false,
        ),
        spec(
            "screenshot",
            "Take a picture of the PC's screen: you see it, and it goes into the chat on his devices for him (\"show \
             me my PC screen\", \"what's this on my screen?\"). To act on the screen, read it with screen.",
            &json!({}),
            &[],
            false,
            false,
        ),
    ]
}

/// Whether name is one of these tools.
pub fn offers(name: &str) -> bool {
    matches!(name, "screen" | "find" | "act" | "act_risky" | "switch_window" | "wait_for" | "screenshot")
}

/// One element on the screen, as she reads it.
#[derive(Clone, Debug, Default)]
pub struct Element {
    pub x: i32,
    pub y: i32,
    pub kind: String,
    pub name: String,
    pub window: String,
    pub focused: bool,
    pub password: bool,
    pub value: Option<String>,
    pub toggle: Option<String>,
    pub state: Option<String>,
    pub scrolls: bool,
    /// Its window.
    pub hwnd: isize,
    /// The reader's handle on it, to act on and find again (uia.rs; 0 for none).
    pub handle: u64,
}

impl Element {
    pub(super) fn line(&self, number: usize) -> String {
        let mut details = Vec::new();
        if self.focused {
            details.push("focused".to_string());
        }
        if self.password {
            details.push("password".to_string());
        }
        if let Some(value) = &self.value {
            details.push(format!("value \"{}\"", short(value, 40)));
        }
        details.extend(self.toggle.clone());
        details.extend(self.state.clone());
        if self.scrolls {
            details.push("scrolls".to_string());
        }
        let name = short(&self.name, LABEL_CHARS);
        let name = if name.is_empty() { "(no name)".to_string() } else { name };
        let details = if details.is_empty() { String::new() } else { format!(" ({})", details.join(", ")) };
        format!("[{number}] {}: {name}{details}", self.kind)
    }

    fn same_as(&self, other: &Element) -> bool {
        (&self.kind, &self.name, &self.window) == (&other.kind, &other.name, &other.window)
    }
}

/// What's on the screen: the window in front and its elements, numbered from 1 in this order.
#[derive(Clone, Debug, Default)]
pub struct Screen {
    pub window: Option<String>,
    pub elements: Vec<Element>,
    pub other_windows: Vec<String>,
    pub cut: bool,
    pub hwnd: isize,
    /// Why there's nothing to show, when Natro knows.
    pub note: Option<String>,
}

impl Screen {
    fn element(&self, number: &Value) -> Result<&Element, String> {
        let index = match number {
            Value::Number(number) => number.as_u64().unwrap_or(0) as usize,
            Value::String(text) if !text.trim().is_empty() && text.trim().chars().all(|c| c.is_ascii_digit()) => {
                text.trim().parse().unwrap_or(0)
            }
            _ => 0,
        };
        if index < 1 || index > self.elements.len() {
            return Err(format!("There is no element [{}] on the screen: read it again with screen.", written(number)));
        }
        Ok(&self.elements[index - 1])
    }

    /// The screen as text for the brain; only: the elements to list (by index), when not all.
    fn describe(&self, only: Option<&HashSet<usize>>) -> String {
        let mut lines = vec![format!("Window in front: {}", self.window.as_deref().unwrap_or("none"))];
        let numbered: Vec<String> = self
            .elements
            .iter()
            .enumerate()
            .filter(|(index, _)| only.is_none_or(|only| only.contains(index)))
            .map(|(index, element)| element.line(index + 1))
            .collect();
        let none = numbered.is_empty();
        lines.extend(numbered);
        if only.is_some() && none {
            lines.push("(nothing on screen matches)".into());
        } else if self.elements.is_empty() {
            let why = self
                .note
                .as_deref()
                .unwrap_or("no elements Natro can use: the app may not expose them, or it is still loading");
            lines.push(format!("({why})"));
        }
        if self.cut {
            lines.push("(more on screen than shown)".into());
        }
        if !self.other_windows.is_empty() {
            lines.push(format!("Other windows: {}.", self.other_windows.join(", ")));
        }
        lines.join("\n")
    }

    fn sends_on_enter(&self) -> bool {
        MESSAGING.is_match(self.window.as_deref().unwrap_or_default())
            || self.elements.iter().any(|element| SENDING.is_match(&element.name))
    }
}

/// Text on one line, cut to length with an ellipsis.
fn short(text: &str, length: usize) -> String {
    let flat = text.split(is_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= length {
        return flat;
    }
    flat.chars().take(length - 1).collect::<String>() + "…"
}

/// What reads the screen and acts on it (uia.rs; a stand-in in tests).
pub trait Desktop: Send {
    fn read(&self, everything: bool) -> Result<Screen, String>;
    /// The element where it is now, or None when it has gone or scrolled out of sight.
    fn locate(&self, element: &Element) -> Result<Option<Element>, String>;
    fn click(&self, element: &Element, button: Button, clicks: u32) -> Result<(), String>;
    fn type_text(&self, element: Option<&Element>, text: &str, clear: bool, enter: bool, window: isize)
    -> Result<(), String>;
    fn key(&self, keys: &str, window: isize) -> Result<(), String>;
    fn scroll(&self, element: Option<&Element>, down: bool, window: isize) -> Result<(), String>;
    /// Bring the open window best matching the name to the front; its title.
    fn switch(&self, name: &str) -> Result<String, String>;
    /// Whether the text showed, and the screen then.
    fn wait_for(&self, text: &str, seconds: u64) -> Result<(bool, Screen), String>;
    /// The screen he's using, as JPEG.
    fn screenshot(&self) -> Result<Vec<u8>, String>;
}

/// One thing act does, checked before any is done.
enum Step {
    Click(Element, Button, u32),
    Type(Option<Element>, String, bool, bool),
    Key(String),
    Scroll(Option<Element>, bool),
}

pub struct ScreenControl {
    desktop: Box<dyn Desktop>,
    /// The screen she last saw: her element numbers refer to it.
    shown: Option<Screen>,
    settle: Duration,
}

impl ScreenControl {
    pub fn new(desktop: Box<dyn Desktop>) -> ScreenControl {
        ScreenControl { desktop, shown: None, settle: SETTLE }
    }

    /// Run one of the tools (offers). Blocking. Ok: its result for Natro, and a picture (a
    /// screenshot) for her and the chat; Err: why it failed, for her.
    pub fn run(&mut self, name: &str, args: &Value) -> Result<(String, Option<Picture>), String> {
        let text = || text_arg(args, "text").unwrap_or_default().to_string();
        let everything = truthy(&args["everything"]);
        let said = match name {
            "screen" => self.screen(everything),
            "find" => self.find(&text(), everything),
            "act" => self.act(&args["actions"], false),
            "act_risky" => self.act(&args["actions"], true),
            "switch_window" => self.switch_window(text_arg(args, "name").unwrap_or_default()),
            "wait_for" => {
                let seconds = match number_arg(name, args, "seconds", 10)? {
                    0 => 10,
                    seconds => seconds,
                };
                self.wait_for(&text(), seconds.clamp(1, 20) as u64)
            }
            "screenshot" => return self.screenshot().map(|(text, picture)| (text, Some(picture))),
            _ => Err(format!("The PC has no tool called {}.", quoted(name))),
        };
        said.map(|text| (text, None))
    }

    fn show(&mut self, screen: Screen) -> String {
        let text = screen.describe(None);
        self.shown = Some(screen);
        text
    }

    fn screen(&mut self, everything: bool) -> Result<String, String> {
        let screen = self.desktop.read(everything)?;
        Ok(self.show(screen))
    }

    fn find(&mut self, text: &str, everything: bool) -> Result<String, String> {
        let lower = text.to_lowercase();
        let words: Vec<&str> = lower.split(is_space).filter(|word| !word.is_empty()).collect();
        if words.is_empty() {
            return Err("Say what to look for.".into());
        }
        let screen = self.desktop.read(everything)?;
        let matches: HashSet<usize> = screen
            .elements
            .iter()
            .enumerate()
            .filter(|(_, element)| {
                let said = format!("{} {}", element.name, element.value.as_deref().unwrap_or_default()).to_lowercase();
                words.iter().all(|word| said.contains(word))
            })
            .map(|(index, _)| index)
            .collect();
        let text = screen.describe(Some(&matches));
        self.shown = Some(screen);
        Ok(text)
    }

    /// The elements the actions name, where they are now; one that has gone is looked for on a
    /// fresh read. By each action's element as he gave it.
    fn targets(&mut self, actions: &[Value]) -> Result<HashMap<String, Element>, String> {
        let Some(shown) = &self.shown else {
            return Err("Read the screen first (screen), so the elements have numbers.".into());
        };
        let mut targets = Vec::new();
        for action in actions {
            let number = &action["element"];
            if !number.is_null() && !targets.iter().any(|(given, _): &(Value, Element)| given == number) {
                targets.push((number.clone(), shown.element(number)?.clone()));
            }
        }
        let mut now: Option<Screen> = None;
        let mut found = HashMap::new();
        for (number, old) in targets {
            let here = match self.desktop.locate(&old)? {
                Some(here) => here,
                None => {
                    if now.is_none() {
                        now = Some(self.desktop.read(false)?);
                    }
                    let screen = now.as_ref().expect("read");
                    let same = screen.elements.iter().filter(|element| element.same_as(&old));
                    let nearest = same.min_by_key(|element| (element.x - old.x).abs() + (element.y - old.y).abs());
                    match nearest {
                        Some(nearest) => nearest.clone(),
                        None => {
                            let now = now.take().expect("read");
                            let described = now.describe(None);
                            self.shown = Some(now);
                            return Err(format!(
                                "The screen changed: [{}] ({} {}) isn't there now. The screen now:\n{described}",
                                written(&number),
                                old.kind,
                                short(&old.name, 30)
                            ));
                        }
                    }
                }
            };
            found.insert(number.to_string(), here);
        }
        Ok(found)
    }

    fn act(&mut self, actions: &Value, risky: bool) -> Result<String, String> {
        let actions =
            actions.as_array().filter(|actions| !actions.is_empty()).ok_or("Say what to do: a list of actions.")?;
        let targets = self.targets(actions)?;
        // Every action is checked before any is done.
        let steps = actions.iter().map(|action| self.step(action, &targets, risky)).collect::<Result<Vec<_>, _>>()?;
        let window = self.shown.as_ref().map_or(0, |shown| shown.hwnd);
        for step in steps {
            match step {
                Step::Click(element, button, clicks) => self.desktop.click(&element, button, clicks)?,
                Step::Type(element, text, clear, enter) => {
                    self.desktop.type_text(element.as_ref(), &text, clear, enter, window)?
                }
                Step::Key(keys) => self.desktop.key(&keys, window)?,
                Step::Scroll(element, down) => self.desktop.scroll(element.as_ref(), down, window)?,
            }
        }
        thread::sleep(self.settle);
        let screen = self.desktop.read(false)?;
        Ok(format!("Done. {}", self.show(screen)))
    }

    fn step(&self, action: &Value, targets: &HashMap<String, Element>, risky: bool) -> Result<Step, String> {
        let shown = self.shown.as_ref().expect("targets needs it");
        let element = (!action["element"].is_null()).then(|| targets.get(&action["element"].to_string())).flatten();
        let what = &action["do"];
        match what.as_str() {
            Some(how @ ("click" | "double_click" | "right_click")) => {
                let Some(element) = element else {
                    return Err(format!("{how} needs an element number."));
                };
                if !risky && RISKY.is_match(&element.name) {
                    return Err(format!(
                        "[{}] ({}) looks like it sends, buys, pays or deletes: use act_risky, which asks him first.",
                        written(&action["element"]),
                        short(&element.name, 40)
                    ));
                }
                let button = if how == "right_click" { Button::Right } else { Button::Left };
                Ok(Step::Click(element.clone(), button, if how == "double_click" { 2 } else { 1 }))
            }
            Some("type") => {
                if element.is_some_and(|element| element.password) {
                    return Err("That's a password field: Natro never types passwords. He can type it himself.".into());
                }
                let enter = truthy(&action["enter"]);
                if enter && !risky && shown.sends_on_enter() {
                    return Err("Enter here would likely send it: use act_risky, which asks him first.".into());
                }
                let clear = action["clear"] != Value::Bool(false);
                Ok(Step::Type(element.cloned(), text_of(&action["text"]), clear, enter))
            }
            Some("key") => {
                let keys = text_of(&action["text"]).trim_matches(is_space).to_lowercase();
                // An unknown key name is refused before anything is done.
                let pressed = input::parse_keys(&keys)?;
                let sends = pressed.iter().any(|(vk, _)| *vk == input::VK_RETURN) && shown.sends_on_enter();
                if !risky && (sends || RISKY.is_match(&keys.replace('+', " "))) {
                    return Err(format!(
                        "Pressing {keys} here looks like it sends or deletes: use act_risky, which asks him first."
                    ));
                }
                Ok(Step::Key(keys))
            }
            Some("scroll") => {
                let given = &action["direction"];
                let direction = if truthy(given) { text_of(given) } else { "down".into() };
                if direction != "up" && direction != "down" {
                    return Err("scroll goes up or down.".into());
                }
                Ok(Step::Scroll(element.cloned(), direction == "down"))
            }
            _ => Err(format!(
                "Unknown action {}: click, double_click, right_click, type, key or scroll.",
                what.as_str().map_or_else(|| written(what), quoted)
            )),
        }
    }

    fn switch_window(&mut self, name: &str) -> Result<String, String> {
        if name.trim_matches(is_space).is_empty() {
            return Err("Say which window.".into());
        }
        self.desktop.switch(name)?;
        thread::sleep(self.settle);
        let screen = self.desktop.read(false)?;
        Ok(self.show(screen))
    }

    fn screenshot(&mut self) -> Result<(String, Picture), String> {
        let data = self.desktop.screenshot()?;
        let now = unsafe { GetLocalTime() };
        let name = format!("PC screen {:02}.{:02}.jpg", now.wHour, now.wMinute);
        let text = "The screenshot of his PC's screen is here, and in the chat, where he can see it too.";
        Ok((text.into(), Picture { mime: "image/jpeg", name, data }))
    }

    fn wait_for(&mut self, text: &str, seconds: u64) -> Result<String, String> {
        if text.trim_matches(is_space).is_empty() {
            return Err("Say what to wait for.".into());
        }
        let (found, screen) = self.desktop.wait_for(text, seconds)?;
        let said = if found {
            format!("{} is on the screen now.", quoted(text))
        } else {
            format!("{} didn't show within {seconds} seconds.", quoted(text))
        };
        Ok(format!("{said}\n{}", self.show(screen)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    const JPEG: &[u8] = &[0xFF, 0xD8, b'j', b'p', b'e', b'g'];

    fn element(x: i32, y: i32, kind: &str, name: &str, window: &str, hwnd: isize) -> Element {
        Element { x, y, kind: kind.into(), name: name.into(), window: window.into(), hwnd, ..Default::default() }
    }

    fn discord() -> Screen {
        let window = "general - Discord";
        Screen {
            window: Some(window.into()),
            elements: vec![
                element(200, 300, "list item", "general", window, 7),
                element(220, 330, "list item", "Delete channel", window, 7),
                Element {
                    focused: true,
                    value: Some("see you at 8".into()),
                    ..element(640, 760, "field", "Message #general", window, 7)
                },
                Element { password: true, ..element(900, 100, "field", "Password", window, 7) },
                Element { toggle: Some("on".into()), ..element(1000, 120, "check box", "Notifications", window, 7) },
                element(1200, 760, "button", "Send", window, 7),
                Element { scrolls: true, ..element(700, 400, "document", "Messages", window, 7) },
            ],
            other_windows: vec!["Google Chrome".into(), "Spotify".into()],
            hwnd: 7,
            ..Default::default()
        }
    }

    fn notepad() -> Screen {
        let window = "Untitled - Notepad";
        Screen {
            window: Some(window.into()),
            elements: vec![
                element(400, 400, "document", "Text editor", window, 9),
                element(30, 40, "menu item", "File", window, 9),
            ],
            hwnd: 9,
            ..Default::default()
        }
    }

    /// Reads give the screens in turn (the last one again once they run out); calls lists what was done.
    #[derive(Default)]
    struct Fake {
        screens: Vec<Screen>,
        last: Option<Screen>,
        calls: Vec<String>,
        /// Names of elements that have gone since they were read.
        gone: HashSet<String>,
        /// Name -> where it is now.
        moved: HashMap<String, (i32, i32)>,
    }

    #[derive(Clone)]
    struct FakeDesktop(Arc<Mutex<Fake>>);

    impl FakeDesktop {
        fn new(screens: Vec<Screen>) -> FakeDesktop {
            FakeDesktop(Arc::new(Mutex::new(Fake { screens, ..Default::default() })))
        }

        fn call(&self, call: String) {
            self.0.lock().unwrap().calls.push(call);
        }

        fn actions(&self) -> Vec<String> {
            self.0.lock().unwrap().calls.iter().filter(|call| !call.starts_with("read")).cloned().collect()
        }

        fn reads(&self) -> usize {
            self.0.lock().unwrap().calls.iter().filter(|call| call.starts_with("read")).count()
        }
    }

    impl Desktop for FakeDesktop {
        fn read(&self, everything: bool) -> Result<Screen, String> {
            self.call(format!("read {everything}"));
            let mut fake = self.0.lock().unwrap();
            if !fake.screens.is_empty() {
                fake.last = Some(fake.screens.remove(0));
            }
            Ok(fake.last.clone().unwrap_or_default())
        }

        fn locate(&self, element: &Element) -> Result<Option<Element>, String> {
            let fake = self.0.lock().unwrap();
            if fake.gone.contains(&element.name) {
                return Ok(None);
            }
            let (x, y) = fake.moved.get(&element.name).copied().unwrap_or((element.x, element.y));
            Ok(Some(Element { x, y, ..element.clone() }))
        }

        fn click(&self, element: &Element, button: Button, clicks: u32) -> Result<(), String> {
            self.call(format!("click {} ({}, {}) {button:?} {clicks}", element.name, element.x, element.y));
            Ok(())
        }

        fn type_text(&self, element: Option<&Element>, text: &str, clear: bool, enter: bool, window: isize)
        -> Result<(), String> {
            let name = element.map_or("None", |element| element.name.as_str());
            self.call(format!("type {name} {text:?} clear {clear} enter {enter} window {window}"));
            Ok(())
        }

        fn key(&self, keys: &str, window: isize) -> Result<(), String> {
            self.call(format!("key {keys} window {window}"));
            Ok(())
        }

        fn scroll(&self, element: Option<&Element>, down: bool, window: isize) -> Result<(), String> {
            let name = element.map_or("None", |element| element.name.as_str());
            self.call(format!("scroll {name} {} window {window}", if down { "down" } else { "up" }));
            Ok(())
        }

        fn switch(&self, name: &str) -> Result<String, String> {
            self.call(format!("switch {name}"));
            Ok(name.into())
        }

        fn wait_for(&self, text: &str, seconds: u64) -> Result<(bool, Screen), String> {
            self.call(format!("wait_for {text} {seconds}"));
            Ok((true, self.read(false)?))
        }

        fn screenshot(&self) -> Result<Vec<u8>, String> {
            self.call("screenshot".into());
            Ok(JPEG.to_vec())
        }
    }

    fn make(screens: Vec<Screen>) -> (ScreenControl, FakeDesktop) {
        let fake = FakeDesktop::new(screens);
        let mut control = ScreenControl::new(Box::new(fake.clone()));
        control.settle = Duration::ZERO;
        (control, fake)
    }

    fn act(pc: &mut ScreenControl, actions: Value) -> Result<String, String> {
        pc.run("act", &json!({"actions": actions})).map(|(text, _)| text)
    }

    fn act_risky(pc: &mut ScreenControl, actions: Value) -> Result<String, String> {
        pc.run("act_risky", &json!({"actions": actions})).map(|(text, _)| text)
    }

    fn screen(pc: &mut ScreenControl) -> String {
        pc.run("screen", &json!({})).unwrap().0
    }

    #[test]
    fn the_screen_as_she_reads_it() {
        let (mut pc, _) = make(vec![discord()]);
        assert_eq!(
            screen(&mut pc),
            [
                "Window in front: general - Discord",
                "[1] list item: general",
                "[2] list item: Delete channel",
                "[3] field: Message #general (focused, value \"see you at 8\")",
                "[4] field: Password (password)",
                "[5] check box: Notifications (on)",
                "[6] button: Send",
                "[7] document: Messages (scrolls)",
                "Other windows: Google Chrome, Spotify.",
            ]
            .join("\n")
        );
    }

    #[test]
    fn an_empty_screen_says_why() {
        let admin = Screen {
            window: Some("Task Manager".into()),
            note: Some("this window runs as administrator, and Natro can't use those".into()),
            ..Default::default()
        };
        let (mut pc, _) = make(vec![admin]);
        assert_eq!(
            screen(&mut pc),
            "Window in front: Task Manager\n(this window runs as administrator, and Natro can't use those)"
        );
        let (mut pc, _) = make(vec![Screen::default()]);
        assert!(screen(&mut pc).starts_with("Window in front: none\n(no elements Natro can use"));
    }

    #[test]
    fn screen_find_and_act() {
        let (mut pc, fake) = make(vec![discord(), discord(), notepad()]);
        assert!(screen(&mut pc).starts_with("Window in front: general - Discord\n[1] list item: general"));
        let found = pc.run("find", &json!({"text": "message"})).unwrap().0;
        assert_eq!(
            found.lines().skip(1).collect::<Vec<_>>(),
            [
                "[3] field: Message #general (focused, value \"see you at 8\")",
                "[7] document: Messages (scrolls)",
                "Other windows: Google Chrome, Spotify."
            ]
        );
        let result = act(
            &mut pc,
            json!([{"do": "click", "element": 1}, {"do": "type", "element": 3, "text": "on my way"},
                   {"do": "scroll", "element": 7, "direction": "up"}, {"do": "key", "text": "ctrl+a"}]),
        )
        .unwrap();
        assert!(result.starts_with("Done. Window in front: Untitled - Notepad"));
        assert_eq!(
            fake.actions(),
            [
                "click general (200, 300) Left 1",
                "type Message #general \"on my way\" clear true enter false window 7",
                "scroll Messages up window 7",
                "key ctrl+a window 7",
            ]
        );
        let (mut pc, _) = make(vec![discord()]);
        screen(&mut pc);
        let none = pc.run("find", &json!({"text": "zebra"})).unwrap().0;
        assert_eq!(none.lines().nth(1), Some("(nothing on screen matches)"));
        assert_eq!(pc.run("find", &json!({"text": " "})).unwrap_err(), "Say what to look for.");
    }

    #[test]
    fn typing_where_the_cursor_is_and_scrolling_the_window() {
        let (mut pc, fake) = make(vec![notepad()]);
        screen(&mut pc);
        act(&mut pc, json!([{"do": "type", "text": "hello"}, {"do": "scroll"}])).unwrap();
        assert_eq!(
            fake.actions(),
            ["type None \"hello\" clear true enter false window 9", "scroll None down window 9"]
        );
    }

    #[test]
    fn sending_buying_and_deleting_need_act_risky() {
        let (mut pc, fake) = make(vec![discord()]);
        screen(&mut pc);
        for actions in [
            json!([{"do": "click", "element": 6}]), // Send
            json!([{"do": "click", "element": 2}]), // Delete channel
            json!([{"do": "type", "element": 3, "text": "hi", "enter": true}]), // Enter in a chat sends
            json!([{"do": "type", "text": "hi", "enter": true}]), // typed where the cursor is, too
            json!([{"do": "key", "text": "enter"}]),
            json!([{"do": "key", "text": "shift+delete"}]),
            // Nothing is done when any action in the list is refused.
            json!([{"do": "click", "element": 1}, {"do": "click", "element": 6}]),
        ] {
            assert!(act(&mut pc, actions.clone()).unwrap_err().contains("act_risky"), "{actions}");
        }
        assert!(fake.actions().is_empty());
        act_risky(&mut pc, json!([{"do": "type", "element": 3, "text": "hi", "enter": true}])).unwrap();
        assert_eq!(fake.actions(), ["type Message #general \"hi\" clear true enter true window 7"]);
    }

    #[test]
    fn a_comment_box_counts_as_sending() {
        let window = "Video - YouTube - Microsoft Edge";
        let page = Screen {
            window: Some(window.into()),
            elements: vec![
                element(300, 500, "field", "Add a comment…", window, 3),
                element(600, 560, "button", "Comment", window, 3),
            ],
            hwnd: 3,
            ..Default::default()
        };
        let (mut pc, fake) = make(vec![page]);
        screen(&mut pc);
        let posting = act(&mut pc, json!([{"do": "type", "element": 1, "text": "hello", "enter": true}]));
        assert!(posting.unwrap_err().contains("act_risky"));
        act(&mut pc, json!([{"do": "type", "element": 1, "text": "hello"}])).unwrap(); // typing alone is fine
        assert_eq!(fake.actions(), ["type Add a comment… \"hello\" clear true enter false window 3"]);
    }

    #[test]
    fn passwords_are_never_typed_even_after_a_yes() {
        let (mut pc, fake) = make(vec![discord()]);
        screen(&mut pc);
        let typed = act_risky(&mut pc, json!([{"do": "type", "element": 4, "text": "hunter2"}]));
        assert!(typed.unwrap_err().contains("password"));
        assert!(fake.actions().is_empty());
    }

    #[test]
    fn unknown_keys_are_refused_before_anything_is_done() {
        let (mut pc, fake) = make(vec![notepad()]);
        screen(&mut pc);
        let pressed = act(&mut pc, json!([{"do": "click", "element": 2}, {"do": "key", "text": "hyper+s"}]));
        assert!(pressed.unwrap_err().starts_with("Unknown key 'hyper'"));
        assert!(fake.actions().is_empty());
    }

    #[test]
    fn elements_are_found_where_they_are_now() {
        let (mut pc, fake) = make(vec![discord()]);
        screen(&mut pc);
        fake.0.lock().unwrap().moved.insert("general".into(), (200, 500));
        // It moved: clicked where it is now, without reading the screen again.
        act(&mut pc, json!([{"do": "click", "element": 1}])).unwrap();
        assert_eq!(fake.actions().last().unwrap(), "click general (200, 500) Left 1");
        assert_eq!(fake.reads(), 2);
    }

    #[test]
    fn a_gone_element_is_looked_for_again_or_refused() {
        let mut moved = discord();
        moved.elements[0].y = 520;
        let mut without = discord();
        without.elements.remove(0);
        let (mut pc, fake) = make(vec![discord(), moved.clone(), moved, discord(), without]);
        screen(&mut pc);
        // Its handle no longer works (the list was rebuilt), but it is still on screen.
        fake.0.lock().unwrap().gone.insert("general".into());
        act(&mut pc, json!([{"do": "click", "element": 1}])).unwrap();
        assert_eq!(fake.actions().last().unwrap(), "click general (200, 520) Left 1");
        screen(&mut pc);
        let changed = act(&mut pc, json!([{"do": "click", "element": 1}])).unwrap_err();
        assert!(changed.starts_with("The screen changed: [1] (list item general) isn't there now."), "{changed}");
        assert_eq!(fake.actions().len(), 1);
    }

    #[test]
    fn numbers_need_a_screen_first_and_must_exist() {
        let (mut pc, fake) = make(vec![discord()]);
        assert!(act(&mut pc, json!([{"do": "click", "element": 1}])).unwrap_err().starts_with("Read the screen first"));
        screen(&mut pc);
        let missing = act(&mut pc, json!([{"do": "click", "element": 42}])).unwrap_err();
        assert!(missing.starts_with("There is no element [42]"));
        act(&mut pc, json!([{"do": "click", "element": "1"}])).unwrap(); // a number written as text works too
        assert_eq!(fake.actions(), ["click general (200, 300) Left 1"]);
        let unknown = act(&mut pc, json!([{"do": "drag", "element": 1}])).unwrap_err();
        assert_eq!(unknown, "Unknown action 'drag': click, double_click, right_click, type, key or scroll.");
        assert_eq!(act(&mut pc, json!([])).unwrap_err(), "Say what to do: a list of actions.");
    }

    #[test]
    fn switching_windows_and_waiting() {
        let (mut pc, fake) = make(vec![notepad(), discord()]);
        let switched = pc.run("switch_window", &json!({"name": "Notepad"})).unwrap().0;
        assert!(switched.starts_with("Window in front: Untitled - Notepad"));
        let waited = pc.run("wait_for", &json!({"text": "Messages", "seconds": 99})).unwrap().0;
        assert!(waited.starts_with("'Messages' is on the screen now.\nWindow in front: general - Discord"));
        assert_eq!(fake.actions(), ["switch Notepad", "wait_for Messages 20"]);
    }

    #[test]
    fn a_screenshot_is_a_picture_for_natro_and_the_chat() {
        let (mut pc, _) = make(vec![]);
        let (text, picture) = pc.run("screenshot", &json!({})).unwrap();
        assert!(text.starts_with("The screenshot of his PC's screen is here, and in the chat"));
        let picture = picture.unwrap();
        assert_eq!((picture.mime, picture.data.as_slice()), ("image/jpeg", JPEG));
        assert!(picture.name.starts_with("PC screen ") && picture.name.ends_with(".jpg"));
    }

    #[test]
    fn the_risky_words_are_the_phones() {
        let kotlin = include_str!("../../../../android/app/src/main/java/dev/natro/Speech.kt");
        let found = Regex::new(r#"(?s)val RISKY = Regex\(\s*(.*?),\s*RegexOption"#).unwrap();
        let pattern = &found.captures(kotlin).unwrap()[1];
        let parts = Regex::new(r#""((?:[^"\\]|\\.)*)""#).unwrap();
        let phone: String = parts.captures_iter(pattern).map(|part| part[1].replace("\\\\", "\\")).collect();
        assert_eq!(phone, RISKY_PATTERN.replace("    ", ""));
        assert!(RISKY.is_match("Send") && RISKY.is_match("გაგზავნა") && !RISKY.is_match("Sender's notes"));
    }
}
