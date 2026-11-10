//! The PC's tools, as Natro's brain sees them (desktop/tools.json; the hello lists them, see
//! shared/PROTOCOL.md): apps.rs, files.rs, commands.rs, screen.rs (with uia.rs, input.rs and
//! screenshot.rs), this.rs and browser.rs (with playwright.rs), all in the desktop app since cleanup step 5.
//! Each takes the arguments the brain sends and gives back text for her, and a screenshot a
//! picture too.

pub mod apps;
pub mod browser;
pub mod commands;
pub mod files;
pub mod input;
pub mod playwright;
pub mod screen;
pub mod screenshot;
pub mod this;
pub mod uia;

use std::sync::{Arc, Mutex};

use base64::Engine;
use serde_json::{Value, json};

use crate::config::Config;
use apps::Apps;
use browser::Edge;
use commands::Commands;
use files::Files;
use screen::ScreenControl;
use uia::Uia;

pub struct Tools {
    apps: Arc<Apps>,
    files: Arc<Files>,
    commands: Arc<Commands>,
    /// One screen tool at a time: her element numbers are the last screen's.
    screen: Arc<Mutex<ScreenControl>>,
    uia: Uia,
    edge: Arc<Edge>,
}

/// A tool's picture (a screenshot): for Natro, and for the chat on his devices.
#[derive(Debug)]
pub struct Picture {
    pub mime: &'static str,
    pub name: String,
    pub data: Vec<u8>,
}

impl Tools {
    pub fn new(apps: Apps, config: &Config) -> Tools {
        let commands = Arc::new(Commands::new());
        let uia = Uia::new();
        // PowerShell and UI Automation started ahead of the first command or screen (a second or two).
        let (starting, reading) = (commands.clone(), uia.clone());
        std::thread::spawn(move || starting.warm_up());
        std::thread::spawn(move || reading.warm_up());
        let screen = Arc::new(Mutex::new(ScreenControl::new(Box::new(uia.clone()))));
        let edge = Arc::new(Edge::new(config));
        Tools { apps: Arc::new(apps), files: Arc::new(Files::new()), commands, screen, uia, edge }
    }

    /// All of them for the brain's hello, in tools.json's order (his Edge's only with the
    /// Playwright Extension's token).
    pub fn specs(&self) -> Vec<Value> {
        let mut specs = own();
        specs.extend(self.edge.specs());
        specs
    }

    /// Run one: (ok, its result for the brain, a picture for the chat or null).
    pub async fn run(&self, name: &str, args: &Value) -> (bool, String, Value) {
        let (apps, files, commands, screen, uia, edge) = (
            self.apps.clone(),
            self.files.clone(),
            self.commands.clone(),
            self.screen.clone(),
            self.uia.clone(),
            self.edge.clone(),
        );
        let (tool, args) = (name.to_string(), args.clone());
        let ran = tauri::async_runtime::spawn_blocking(move || {
            let text = |result: Result<String, String>| result.map(|text| (text, None));
            if apps::offers(&tool) {
                text(apps.run(&tool, &args))
            } else if files::offers(&tool) {
                text(files.run(&tool, &args))
            } else if commands::offers(&tool) {
                text(commands.run(&tool, &args))
            } else if screen::offers(&tool) {
                screen.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).run(&tool, &args)
            } else if this::offers(&tool) {
                text(this::run(&uia, &files))
            } else if browser::offers(&tool) {
                text(edge.run(&tool, &args))
            } else {
                Err(format!("The PC has no tool called {}.", quoted(&tool)))
            }
        });
        match ran.await {
            Ok(Ok((result, picture))) => (true, result, picture.map_or(Value::Null, |picture| picture.json())),
            Ok(Err(problem)) => (false, problem, Value::Null),
            Err(e) => (false, format!("{name} failed on the PC: {e}"), Value::Null),
        }
    }
}

impl Picture {
    /// As the tool_result's picture (shared/PROTOCOL.md).
    fn json(&self) -> Value {
        let data = base64::engine::general_purpose::STANDARD.encode(&self.data);
        json!({"mime": self.mime, "name": self.name, "data": data})
    }
}

/// The tools that are always there (browser.rs adds his Edge's and send_message).
fn own() -> Vec<Value> {
    let mut specs = apps::specs();
    specs.extend(files::specs());
    specs.extend(commands::specs());
    specs.extend(screen::specs());
    specs.extend(this::specs());
    specs
}

/// His home folder (as Python's Path.home(): USERPROFILE).
pub fn home() -> String {
    std::env::var("USERPROFILE").unwrap_or_else(|_| r"C:\Users".into())
}

/// Python's str.isspace, which its strip() and \s go by.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// A tool as the hello describes it: whether he must say yes first (confirm), and whether its
/// result is a fine spoken reply on its own (final: "Opened Spotify.").
pub fn spec(
    name: &str,
    description: &str,
    properties: &Value,
    required: &[&str],
    confirm: bool,
    final_: bool,
) -> Value {
    json!({"name": name, "description": description, "confirm": confirm, "final": final_, "hidden": false,
           "input_schema": {"type": "object", "properties": properties, "required": required}})
}

/// A text argument.
pub fn text_arg<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args[key].as_str()
}

/// A whole-number argument (default when it isn't given), taken as Python's int() took it.
pub fn number_arg(tool: &str, args: &Value, key: &str, default: i64) -> Result<i64, String> {
    let wrong = || format!("{tool}'s {key} must be a whole number.");
    match &args[key] {
        Value::Null => Ok(default),
        Value::Bool(yes) => Ok(*yes as i64),
        Value::Number(number) => {
            number.as_i64().or_else(|| number.as_f64().map(|n| n.trunc() as i64)).ok_or_else(wrong)
        }
        Value::String(text) => text.trim().parse().map_err(|_| wrong()),
        _ => Err(wrong()),
    }
}

/// Whether an argument counts as yes, as Python's truth did.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(yes) => *yes,
        Value::Number(number) => number.as_f64().is_some_and(|n| n != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(fields) => !fields.is_empty(),
    }
}

/// A value as Python's str() wrote it in a message (an element's number as he gave it).
pub fn written(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(yes) => if *yes { "True" } else { "False" }.into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// An argument as text, as Python's str(... or "") took it.
pub fn text_of(value: &Value) -> String {
    if !truthy(value) {
        return String::new();
    }
    written(value)
}

/// A name in quotes as the Python helper's messages had it (its repr): 'Spotify'.
pub fn quoted(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// desktop/tools.json lists them all as the Python helper offered them (the golden list,
    /// server/tests/golden.py, reads it): the hello must offer the same, in its order, unchanged.
    /// A tool changed here is changed there too.
    #[test]
    fn the_tools_are_tools_json() {
        let listed: Vec<Value> = serde_json::from_str(include_str!("../../tools.json")).unwrap();
        let mut every = own();
        every.extend(browser::specs(true));
        assert_eq!(listed.len(), 25);
        assert_eq!(listed, every);
        // Without the Playwright Extension's token, all but his Edge's.
        let mut without = own();
        without.extend(browser::specs(false));
        let names = |specs: &[Value]| specs.iter().map(|spec| spec["name"].clone()).collect::<Vec<_>>();
        let missing = ["browser_open", "browser_page", "browser_act", "browser_act_risky", "browser_tabs"];
        let others: Vec<Value> =
            listed.iter().filter(|spec| !missing.contains(&spec["name"].as_str().unwrap())).cloned().collect();
        assert_eq!(names(&without), names(&others));
    }

    #[test]
    fn arguments_as_python_took_them() {
        let args = json!({"a": "12", "b": 7.9, "c": true, "d": "x", "e": "", "f": "false"});
        assert_eq!(number_arg("t", &args, "a", 0), Ok(12));
        assert_eq!(number_arg("t", &args, "b", 0), Ok(7));
        assert_eq!(number_arg("t", &args, "c", 0), Ok(1));
        assert_eq!(number_arg("t", &args, "missing", 15), Ok(15));
        assert_eq!(number_arg("t", &args, "d", 0), Err("t's d must be a whole number.".into()));
        assert!(!truthy(&args["e"]) && truthy(&args["f"]) && !truthy(&args["missing"]));
    }

    #[test]
    fn names_are_quoted_as_python_did() {
        assert_eq!(quoted("spotify"), "'spotify'");
        assert_eq!(quoted("it's"), "\"it's\"");
        assert_eq!(quoted("say \"hi\" it's"), "'say \"hi\" it\\'s'");
        assert_eq!(quoted("C:\\a\nb"), "'C:\\\\a\\nb'");
    }
}
