//! His Edge, driven through Playwright (the tools browser_open, browser_page, browser_act,
//! browser_act_risky and browser_tabs): open a site, read the page by its structure (not its
//! pixels), click, type, choose, press keys, go back, switch tabs. It's his own browser, with his
//! logins.
//!
//! Playwright's MCP server (playwright.rs) connects to his running Edge through Microsoft's
//! "Playwright Extension", installed in his Edge profile; its token (PLAYWRIGHT_MCP_EXTENSION_TOKEN
//! in .env) lets it connect without asking him each time. Without the token the tools aren't
//! offered. It works in a tab group of its own: his tabs are left alone, and its tabs stay open
//! when it's done (owner, 2026-10-18), for him to see.
//!
//! Like the screen tools (screen.rs): a page is a list of elements with refs ('button "Send"
//! [ref=e12]'), and act does several things in order, each checked before any is done. What looks
//! like sending, buying, paying or deleting (screen.rs's RISKY, the phone's words), and Enter
//! where it would send, go through act_risky, which the brain asks him about first. Passwords are
//! never typed. On bank and payment sites (READ_ONLY) she only reads: no clicking or typing at
//! all, even with his yes (owner, 2026-10-18).
//!
//! send_message is a script with no model steps, for a message on Instagram or Messenger
//! (server/natro_agent/sending.py calls it after his yes when his phone has no quiet way to send
//! it; the model isn't offered it): the chat opened, its title checked against whom he asked for,
//! the text typed and sent, and seen in the chat. It runs in Natro's own Edge, not his: headless,
//! so nothing ever shows (owner, 2026-10-23), with a profile of its own (send_profile) that he
//! signs into once, Instagram and Messenger and Messenger's PIN, in a window the tray opens
//! (sign_in). It's closed after each message.
//!
//! Ported from the Python helper's browser.py (cleanup step 5.5).

use std::path::PathBuf;
use std::process::Command;
use std::sync::{LazyLock, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use regex::{Regex, RegexBuilder};
use serde_json::{Value, json};

use super::playwright::{Mcp, Playwright, which};
use super::screen::RISKY;
use super::{is_space, number_arg, quoted, spec, text_of, truthy, written};
use crate::config::Config;

const TOKEN: &str = "PLAYWRIGHT_MCP_EXTENSION_TOKEN";
const PAGE_CHARS: usize = 18000;
/// Its tools that change the page answer with a link to the page saved as a file; she reads it right after.
const CHANGING: &[&str] = &[
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_select_option",
    "browser_press_key",
    "browser_navigate_back",
];
/// Bank and payment sites: read only (owner, 2026-10-18). NATRO_READ_ONLY_SITES adds more (comma-separated).
const READ_ONLY_PATTERN: &str = "bank|(^|\\.)(bog\\.ge|tbconline\\.ge|tbcpay\\.ge|crystal\\.ge|paypal\\.com|\
    revolut\\.com|wise\\.com|payoneer\\.com|stripe\\.com|payze\\.io|keepz\\.me|payriff\\.com|unipay\\.com)$";
const PASSWORD_PATTERN: &str = "password|passcode|\\bpin\\b|პაროლ";
/// Fields where Enter looks something up rather than sending something.
const SEARCHING_PATTERN: &str = "search|find|look ?up|query|address|url|where|destination|ძებნ|ძიება";
/// send_message: how long a chat may take to show, and the message to show in it once sent.
const CHAT_WAIT: Duration = Duration::from_secs(10);
const SEEN_WAIT: Duration = Duration::from_secs(4);
/// Between reads of the page while waiting.
const POLL: Duration = Duration::from_millis(500);
const KEYS: &[(&str, &str)] = &[
    ("enter", "Enter"),
    ("escape", "Escape"),
    ("esc", "Escape"),
    ("tab", "Tab"),
    ("backspace", "Backspace"),
    ("delete", "Delete"),
    ("arrowdown", "ArrowDown"),
    ("arrowup", "ArrowUp"),
    ("arrowleft", "ArrowLeft"),
    ("arrowright", "ArrowRight"),
    ("pagedown", "PageDown"),
    ("pageup", "PageUp"),
    ("home", "Home"),
    ("end", "End"),
    ("space", " "),
];

fn pattern(text: &str) -> Regex {
    RegexBuilder::new(text).case_insensitive(true).build().expect("a valid pattern")
}

static READ_ONLY: LazyLock<Regex> = LazyLock::new(|| pattern(READ_ONLY_PATTERN));
static PASSWORD: LazyLock<Regex> = LazyLock::new(|| pattern(PASSWORD_PATTERN));
static SEARCHING: LazyLock<Regex> = LazyLock::new(|| pattern(SEARCHING_PATTERN));
static MESSAGE_BOX: LazyLock<Regex> = LazyLock::new(|| pattern("message|მესიჯ|შეტყობინ"));
/// An element on a page's snapshot, with its ref: '- button "Send" [ref=e6] [cursor=pointer]'.
static ELEMENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?m)^\s*- (?P<role>[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?[^\n]*?\[ref=(?P<ref>[\w-]+)\]"#)
        .expect("a valid pattern")
});
/// The words on a line of a page's snapshot: an element's name ('- heading "Nino" [level=2]') or
/// its text ('- text: Nino', '- link "x" [ref=e3]:' has its name).
static WORDS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?m)^\s*- (?:[\w-]+ "(?P<name>(?:[^"\\]|\\.)*)"|[\w-]+(?: \[[^\]]*\])*: (?P<text>.+))"#)
        .expect("a valid pattern")
});
static PAGE_URL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"Page URL: (\S+)").expect("a valid pattern"));
/// The code Playwright ran, which --codegen none leaves out anyway.
static RAN_CODE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"### Ran Playwright code\n```[^`]*```\n*").expect("a valid pattern"));

/// The tools as the brain sees them: in desktop/tools.json, after the screen's. His Edge's only
/// with the extension's token; send_message (hidden from the model) always.
pub fn specs(his_edge: bool) -> Vec<Value> {
    let strings = ["app", "text", "name", "chat", "facebook", "username"].map(|key| (key, json!({"type": "string"})));
    let mut fields: serde_json::Map<String, Value> =
        strings.into_iter().map(|(key, kind)| (key.to_string(), kind)).collect();
    fields.insert("group".into(), json!({"type": "boolean"}));
    let mut sending = spec(
        "send_message",
        "Send a message on Instagram or Messenger from Natro's own hidden Edge on the PC, after his yes to the \
         brain's own question (server/natro_agent/sending.py).",
        &Value::Object(fields),
        &["app", "text"],
        true,
        false,
    );
    sending["hidden"] = json!(true);
    if !his_edge {
        return vec![sending];
    }
    let action = json!({"type": "object", "required": ["do"], "properties": {
        "do": {"type": "string", "enum": ["click", "double_click", "type", "select", "key", "back"]},
        "ref": {"type": "string", "description": "The element's ref from the page (e12)."},
        "text": {"type": "string",
                 "description": "type: what to type. select: the option. key: Enter, Escape, Tab, PageDown..."},
        "submit": {"type": "boolean", "description": "type: press Enter after (a search box)."},
    }});
    let actions = json!({"actions": {"type": "array", "items": action, "description": "Done in this order."}});
    vec![
        spec(
            "browser_open",
            "Open a website in his own Edge on the PC, logged in as him (his accounts), in Natro's own tabs. Returns \
             the page: its elements with refs, to act on.",
            &json!({"url": {"type": "string"},
                    "new_tab": {"type": "boolean", "description": "Keep the page that's open; default false."}}),
            &["url"],
            false,
            false,
        ),
        spec(
            "browser_page",
            "Read the page open in his Edge now: its text and elements with refs.",
            &json!({}),
            &[],
            false,
            false,
        ),
        spec(
            "browser_act",
            "Do one or more things on the page in his Edge, in order, by ref: click, double_click, type (submit: \
             press Enter, for a search box), select (an option), key, back. Returns the page after. Refuses what \
             looks like sending, buying, paying or deleting: use browser_act_risky for those. On bank and payment \
             sites, only reading.",
            &actions,
            &["actions"],
            false,
            false,
        ),
        spec(
            "browser_act_risky",
            "The same as browser_act, for a click or key that sends, buys, pays or deletes.",
            &actions,
            &["actions"],
            true,
            false,
        ),
        spec(
            "browser_tabs",
            "Natro's tabs in his Edge: list them, open a new one, switch to one or close one (by index from list).",
            &json!({"action": {"type": "string", "enum": ["list", "new", "select", "close"]},
                    "index": {"type": "integer"}}),
            &["action"],
            false,
            false,
        ),
        sending,
    ]
}

/// Whether name is one of these tools.
pub fn offers(name: &str) -> bool {
    matches!(
        name,
        "browser_open" | "browser_page" | "browser_act" | "browser_act_risky" | "browser_tabs" | "send_message"
    )
}

/// His Edge (when the extension's token is there) and Natro's own hidden one, each one tool at a
/// time: her refs are the last page's.
pub struct Edge {
    his: Option<Mutex<Browser>>,
    hidden: Mutex<Browser>,
}

impl Edge {
    pub fn new(config: &Config) -> Edge {
        let read_only = extra_read_only(config.get("NATRO_READ_ONLY_SITES").as_deref().unwrap_or_default());
        let token = config.get(TOKEN).map(|token| token.trim_matches(is_space).to_string());
        let his = token.filter(|token| !token.is_empty()).map(|token| {
            let edge = his_edge(&token, config.get("NATRO_EDGE_PROFILE").as_deref());
            Mutex::new(Browser::new(Box::new(edge), read_only.clone()))
        });
        let hidden = Mutex::new(Browser::new(Box::new(hidden_edge(&send_profile())), read_only));
        Edge { his, hidden }
    }

    pub fn specs(&self) -> Vec<Value> {
        specs(self.his.is_some())
    }

    /// Run one of the tools (offers). Blocking. Ok: its result for Natro; Err: why it failed, for her.
    pub fn run(&self, name: &str, args: &Value) -> Result<String, String> {
        if name == "send_message" {
            let mut sender = self.hidden.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            let sent = sender.send_message(args);
            sender.close();
            return sent;
        }
        let Some(his) = &self.his else {
            return Err(format!("The PC has no tool called {}.", quoted(name)));
        };
        let mut edge = his.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        match name {
            "browser_open" => edge.open(&args["url"], truthy(&args["new_tab"])),
            "browser_page" => edge.page(),
            "browser_act" => edge.act(&args["actions"], false),
            "browser_act_risky" => edge.act(&args["actions"], true),
            "browser_tabs" => edge.tabs(args),
            _ => Err(format!("The PC has no tool called {}.", quoted(name))),
        }
    }
}

/// Playwright in his own running Edge, through the extension: his logins, in tabs of its own.
fn his_edge(token: &str, profile: Option<&str>) -> Playwright {
    // Edge named: otherwise it looks for the extension in Chrome.
    let mut args: Vec<String> = ["--extension", "--browser", "msedge"].map(String::from).into();
    if let Some(profile) = profile {
        args.extend(["--profile-dir-name".into(), profile.into()]);
    }
    Playwright::new(args, vec![(TOKEN.into(), token.into())])
}

/// Playwright in Natro's own Edge: headless (no window ever), with its own profile, which he
/// signs into once (sign_in).
fn hidden_edge(profile: &std::path::Path) -> Playwright {
    let args = ["--browser", "msedge", "--headless", "--user-data-dir"].map(String::from);
    Playwright::new(args.into_iter().chain([profile.display().to_string()]).collect(), Vec::new())
}

/// Natro's own Edge profile, for sending messages unseen: %LOCALAPPDATA%\Natro\Edge. Out of the
/// repository: it holds his sign-ins.
pub fn send_profile() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    base.join("Natro").join("Edge")
}

/// Open Natro's own Edge, in a window this once, at Instagram and Messenger: he signs in to both
/// (and enters Messenger's PIN, for its encrypted chats), then closes it. Natro never types any of
/// it. What to tell him.
pub fn sign_in() -> Result<String, String> {
    let installed = ["ProgramFiles(x86)", "ProgramFiles"].into_iter().filter_map(std::env::var_os).map(|folder| {
        PathBuf::from(folder).join(r"Microsoft\Edge\Application\msedge.exe")
    });
    let edge = installed.chain(which("msedge")).find(|path| path.is_file()).ok_or("Edge isn't installed where it usually is.")?;
    let profile = send_profile();
    std::fs::create_dir_all(&profile).map_err(|e| format!("{} couldn't be made ({e}).", profile.display()))?;
    Command::new(edge)
        .arg(format!("--user-data-dir={}", profile.display()))
        .args(["--no-first-run", "--new-window", "https://www.instagram.com/", "https://www.messenger.com/"])
        .spawn()
        .map_err(|e| format!("Edge didn't start ({e})."))?;
    Ok(format!(
        "Natro's own Edge is open ({}): sign in to Instagram and Messenger there, enter Messenger's PIN if it asks, \
         then close that window.",
        profile.display()
    ))
}

/// NATRO_READ_ONLY_SITES: more sites where Natro only reads, comma-separated.
fn extra_read_only(sites: &str) -> Vec<String> {
    sites.split(',').map(|site| site.trim_matches(is_space).to_lowercase()).filter(|site| !site.is_empty()).collect()
}

/// A link's host name, lowercase, as Python's urllib.parse.urlsplit(url).hostname found it.
fn host(url: &str) -> String {
    let rest = match url.split_once(':') {
        Some((scheme, rest))
            if scheme.starts_with(|c: char| c.is_ascii_alphabetic())
                && scheme.chars().all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c)) =>
        {
            rest
        }
        _ => url,
    };
    let Some(rest) = rest.strip_prefix("//") else {
        return String::new();
    };
    let place = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let place = place.rsplit('@').next().unwrap_or_default();
    let name = match place.strip_prefix('[') {
        Some(v6) => v6.split(']').next().unwrap_or_default(),
        None => place.split(':').next().unwrap_or_default(),
    };
    name.to_lowercase()
}

/// Where his chat with someone opens on the web, best first. chat: the app's shortcut id for it,
/// as the phone's notifications name it (Instagram: thread_shortcut_<his id>_<thread id>;
/// Messenger: thread_shortcut_GROUP:<id> or thread_shortcut_ADVANCED_CRYPTO_ONE_TO_ONE:<id>).
fn chat_urls(app: &str, chat: &str, facebook: &str, username: &str) -> Vec<String> {
    let thread = chat.rsplit('_').next().unwrap_or_default().rsplit(':').next().unwrap_or_default();
    let number = !thread.is_empty() && thread.chars().all(|c| c.is_ascii_digit());
    let mut urls = Vec::new();
    if app == "instagram" {
        if number {
            urls.push(format!("https://www.instagram.com/direct/t/{thread}/"));
        }
        if !username.is_empty() {
            urls.push(format!("https://ig.me/m/{username}"));
        }
        return urls;
    }
    urls.extend([facebook, username].iter().filter(|who| !who.is_empty()).map(|who| format!("https://www.messenger.com/t/{who}")));
    if number && chat.contains("GROUP:") {
        urls.insert(0, format!("https://www.messenger.com/t/{thread}"));
    } else if number && chat.contains("CRYPTO") {
        urls.push(format!("https://www.messenger.com/e2ee/t/{thread}"));
    }
    urls
}

/// Text to compare names by: casefolded, on one line.
fn plain(text: &str) -> String {
    let folded = text.to_lowercase().replace('ß', "ss").replace('ς', "σ");
    folded.split(is_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ")
}

/// Every name and text on a page's snapshot.
fn words_on(page: &str) -> Vec<String> {
    WORDS
        .captures_iter(page)
        .map(|found| {
            let words = found.name("name").or_else(|| found.name("text")).map_or("", |words| words.as_str());
            words.replace("\\\"", "\"").trim_matches(is_space).to_string()
        })
        .collect()
}

/// Playwright's answer, cut to PAGE_CHARS (the code it ran is left out: --codegen none).
fn tidy(text: &str) -> String {
    let text = RAN_CODE.replace_all(text, "");
    let text = if text.chars().count() > PAGE_CHARS {
        let kept: String = text.chars().take(PAGE_CHARS).collect();
        format!("{kept}\n[the page is longer than this: act on what's here, or open a part of it]")
    } else {
        text.into_owned()
    };
    text.trim_matches(is_space).to_string()
}

/// An element on the page as last read.
#[derive(Clone, Debug, PartialEq)]
struct Element {
    reference: String,
    role: String,
    name: String,
}

/// One thing act does, checked before any is done: Playwright's tool and its arguments.
type Step = (&'static str, Value);

pub struct Browser {
    playwright: Box<dyn Mcp>,
    /// The page she's on, as Playwright last said.
    url: String,
    /// From the page as last read, in its order.
    elements: Vec<Element>,
    /// The page as last read.
    snapshot: String,
    /// NATRO_READ_ONLY_SITES.
    read_only_sites: Vec<String>,
    chat_wait: Duration,
    seen_wait: Duration,
    poll: Duration,
}

impl Browser {
    fn new(playwright: Box<dyn Mcp>, read_only_sites: Vec<String>) -> Browser {
        Browser {
            playwright,
            url: String::new(),
            elements: Vec::new(),
            snapshot: String::new(),
            read_only_sites,
            chat_wait: CHAT_WAIT,
            seen_wait: SEEN_WAIT,
            poll: POLL,
        }
    }

    fn run(&mut self, name: &str, args: Value) -> Result<String, String> {
        let mut text = self.playwright.call(name, args)?;
        if CHANGING.contains(&name) {
            // Its answer links to the page in a file: read the page itself.
            text = self.playwright.call("browser_snapshot", json!({}))?;
        }
        if let Some(found) = PAGE_URL.captures(&text) {
            self.url = found[1].to_string();
        }
        let found: Vec<Element> = ELEMENT
            .captures_iter(&text)
            .map(|found| Element {
                reference: found["ref"].to_string(),
                role: found["role"].to_string(),
                name: found.name("name").map_or("", |name| name.as_str()).replace("\\\"", "\""),
            })
            .collect();
        if !found.is_empty() {
            self.elements = found;
            self.snapshot = text.clone();
        }
        Ok(tidy(&text))
    }

    fn element(&self, reference: &str) -> Option<&Element> {
        self.elements.iter().find(|element| element.reference == reference)
    }

    /// Whether a page is on a bank or payment site, where Natro only reads.
    fn read_only(&self, url: &str) -> bool {
        let name = host(url);
        !name.is_empty()
            && (READ_ONLY.is_match(&name)
                || self.read_only_sites.iter().any(|site| name == *site || name.ends_with(&format!(".{site}"))))
    }

    // Tools

    fn open(&mut self, url: &Value, new_tab: bool) -> Result<String, String> {
        let mut url = text_of(url).trim_matches(is_space).to_string();
        if !url.starts_with("http://") && !url.starts_with("https://") {
            if url.is_empty() || url.contains(' ') {
                return Err("Give a web address (example.com, or https://...).".into());
            }
            url = format!("https://{url}");
        }
        if new_tab {
            self.run("browser_tabs", json!({"action": "new"}))?;
        }
        let said = self.run("browser_navigate", json!({"url": url}))?;
        Ok(if self.read_only(&self.url) { format!("(A bank or payment site: you can only read here.)\n{said}") } else { said })
    }

    fn page(&mut self) -> Result<String, String> {
        self.run("browser_snapshot", json!({}))
    }

    fn tabs(&mut self, given: &Value) -> Result<String, String> {
        let action = match &given["action"] {
            Value::Null => "list",
            Value::String(action) if ["list", "select", "close", "new"].contains(&action.as_str()) => action,
            _ => return Err("action is list, new, select or close.".into()),
        };
        let mut args = json!({"action": action});
        if action == "select" || action == "close" {
            if given["index"].is_null() {
                return Err(format!("{action} needs the tab's index (from list)."));
            }
            args["index"] = json!(number_arg("browser_tabs", given, "index", 0)?);
        }
        self.run("browser_tabs", args)
    }

    fn act(&mut self, actions: &Value, risky: bool) -> Result<String, String> {
        let actions =
            actions.as_array().filter(|actions| !actions.is_empty()).ok_or("Say what to do: a list of actions.")?;
        // Every action is checked before any is done.
        let mut steps = Vec::new();
        let mut searching = false;
        for action in actions {
            let (step, looks_up) = self.step(action, risky, searching)?;
            steps.push(step);
            searching = looks_up;
        }
        let mut said = String::new();
        for (name, args) in steps {
            said = self.run(name, args)?;
        }
        Ok(format!("Done.\n{said}"))
    }

    /// The step, and whether Enter would now look something up.
    fn step(&self, action: &Value, risky: bool, searching: bool) -> Result<(Step, bool), String> {
        let empty = json!({});
        let action = if action.is_object() { action } else { &empty };
        let what = &action["do"];
        let how = what.as_str().unwrap_or_default();
        if how == "back" {
            return Ok((("browser_navigate_back", json!({})), false));
        }
        if self.read_only(&self.url) {
            return Err("This is a bank or payment site: Natro only reads here, and never clicks or types, even with \
                        his yes. He can do it himself."
                .into());
        }
        let reference = text_of(&action["ref"]);
        let (role, name) = self.element(&reference).map_or(("", ""), |element| (&element.role, &element.name));
        if ["click", "double_click", "type", "select"].contains(&how) && role.is_empty() {
            let shown = if reference.is_empty() { "(none)" } else { &reference };
            return Err(format!(
                "There's no element {shown} on the page as last read: read it again (browser_page) and use its refs."
            ));
        }
        let element = format!("{role} {name}").trim_matches(is_space).to_string();
        match how {
            "click" | "double_click" => {
                if !risky && RISKY.is_match(name) {
                    return Err(format!(
                        "{reference} ({element}) looks like it sends, buys, pays or deletes: use act_risky, which asks \
                         him first."
                    ));
                }
                let mut args = json!({"element": element, "target": reference});
                if how == "double_click" {
                    args["doubleClick"] = json!(true);
                }
                Ok((("browser_click", args), false))
            }
            "type" => {
                if PASSWORD.is_match(name) {
                    return Err("That's a password field: Natro never types passwords. He can type it himself.".into());
                }
                let submit = truthy(&action["submit"]);
                let looks_up = SEARCHING.is_match(name);
                if submit && !risky && !looks_up {
                    return Err(format!("Enter in {element} would likely send it: use act_risky, which asks him first."));
                }
                let args = json!({"element": element, "target": reference, "text": text_of(&action["text"]),
                                  "submit": submit});
                Ok((("browser_type", args), looks_up && !submit))
            }
            "select" => {
                let args = json!({"element": element, "target": reference, "values": [text_of(&action["text"])]});
                Ok((("browser_select_option", args), false))
            }
            "key" => {
                let pressed = text_of(&action["text"]).trim_matches(is_space).to_string();
                let named = pressed.to_lowercase().replace(' ', "");
                let key = KEYS.iter().find(|(name, _)| *name == named).map_or(pressed.as_str(), |(_, key)| key);
                if key.is_empty() {
                    return Err("Say which key (Enter, Escape, Tab, PageDown...).".into());
                }
                if !risky && ((key == "Enter" && !searching) || key == "Delete") {
                    return Err(format!(
                        "Pressing {key} here could send or delete something: use act_risky, which asks him first."
                    ));
                }
                Ok((("browser_press_key", json!({"key": key})), false))
            }
            _ => Err(format!(
                "Unknown action {}: click, double_click, type, select, key or back.",
                what.as_str().map_or_else(|| written(what), quoted)
            )),
        }
    }

    fn send_message(&mut self, args: &Value) -> Result<String, String> {
        let app = text_of(&args["app"]);
        let label = match app.as_str() {
            "instagram" => "Instagram",
            "messenger" => "Messenger",
            _ => return Err("Edge sends messages on Instagram and Messenger only.".into()),
        };
        let text = text_of(&args["text"]).trim_matches(is_space).to_string();
        if text.is_empty() {
            return Err("The message is empty.".into());
        }
        let (name, username) = (text_of(&args["name"]), text_of(&args["username"]));
        let shown = if name.is_empty() { username.clone() } else { name.clone() };
        let urls = chat_urls(&app, &text_of(&args["chat"]), &text_of(&args["facebook"]), &username);
        if urls.is_empty() {
            return Err(format!("Edge can't open your chat with {shown} on {label}: ask him for their username."));
        }
        let wanted: Vec<String> = [plain(&name), plain(&username)].into_iter().filter(|who| !who.is_empty()).collect();
        let mut said = String::new();
        let mut opened = false;
        for url in urls {
            said = self.run("browser_navigate", json!({"url": url}))?;
            // A sign-in page is never the chat, whatever names it shows (Instagram's shows "Instagram"),
            // and waiting longer won't make it one.
            let theirs = |page: &Browser| {
                !page.signing_in()
                    && page.message_box().is_some()
                    && words_on(&page.snapshot).iter().any(|words| wanted.contains(&plain(words)))
            };
            if self.wait(|page| page.signing_in() || theirs(page), self.chat_wait)? && !self.signing_in() {
                opened = true;
                break;
            }
            if self.signing_in() {
                return Err(format!(
                    "Natro's own Edge on the PC isn't signed in to {label} (or wants Messenger's PIN): he signs in \
                     there once himself (the tray's \"Sign in Natro's own Edge…\" opens it); Natro never types it. \
                     Nothing was sent."
                ));
            }
        }
        if !opened {
            return Err(format!(
                "Your chat with {shown} didn't open in Edge, or isn't plainly theirs, so nothing was sent. The page \
                 now:\n{said}"
            ));
        }
        let (reference, box_name) = self.message_box().expect("found");
        let before = self.shows(&text);
        let typed = json!({"element": format!("textbox {box_name}"), "target": reference, "text": text, "submit": false});
        self.run("browser_type", typed)?;
        let sends: Vec<String> = self
            .elements
            .iter()
            .filter(|element| element.role == "button" && plain(&element.name) == "send")
            .map(|element| element.reference.clone())
            .collect();
        if sends.len() > 1 {
            return Err("The chat has two Send buttons, so nothing was sent (the message is typed in its box).".into());
        }
        match sends.first() {
            Some(send) => self.run("browser_click", json!({"element": "button Send", "target": send}))?,
            // Instagram's web page sends on Enter.
            None => self.run("browser_press_key", json!({"key": "Enter"}))?,
        };
        if !self.wait(|page| page.shows(&text) > before, self.seen_wait)? {
            return Err(format!(
                "I sent it from the PC, but don't see it in the chat: he should check {label}. Don't send it again."
            ));
        }
        Ok(format!("Sent to {shown} on {label}."))
    }

    /// Close the browser (Natro's own Edge, after a message): nothing of it keeps running.
    fn close(&mut self) {
        let _ = self.playwright.call("browser_close", json!({}));
    }

    /// Whether the page asks to sign in (or for Messenger's PIN) rather than showing a chat: by its
    /// address, or a password or PIN box.
    fn signing_in(&self) -> bool {
        self.url.contains("/login")
            || self.elements.iter().any(|element| element.role == "textbox" && PASSWORD.is_match(&element.name))
    }

    /// (ref, name) of the chat's box, where a message is written, if the page has one: the text box
    /// named for a message, else the only one that isn't for searching (Instagram's has no name).
    fn message_box(&self) -> Option<(String, String)> {
        let boxes: Vec<&Element> = self
            .elements
            .iter()
            .filter(|element| {
                element.role == "textbox" && !SEARCHING.is_match(&element.name) && !PASSWORD.is_match(&element.name)
            })
            .collect();
        let named: Vec<&Element> = boxes.iter().copied().filter(|element| MESSAGE_BOX.is_match(&element.name)).collect();
        let found = if named.is_empty() { boxes } else { named };
        match found[..] {
            [only] => Some((only.reference.clone(), only.name.clone())),
            _ => None,
        }
    }

    /// How many times the page shows these words, not counting the box.
    fn shows(&self, text: &str) -> usize {
        self.snapshot.lines().filter(|line| line.contains(text) && !line.contains("textbox")).count()
    }

    /// Read the page again until check is true, for up to wait.
    fn wait(&mut self, check: impl Fn(&Browser) -> bool, wait: Duration) -> Result<bool, String> {
        let until = Instant::now() + wait;
        while !check(self) {
            if Instant::now() >= until {
                return Ok(false);
            }
            thread::sleep(self.poll);
            self.run("browser_snapshot", json!({}))?;
        }
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    const PAGE: &str = r#"### Page
- Page URL: {url}
- Page Title: Inbox
### Snapshot
```yaml
- generic [active] [ref=e1]:
  - searchbox "Search mail" [ref=e3]
  - textbox "Message" [ref=e4]
  - textbox "Password" [ref=e5]
  - button "Send" [ref=e6] [cursor=pointer]
  - link "Inbox (3)" [ref=e7] [cursor=pointer]:
    - /url: /inbox
  - combobox "Sort by" [ref=e8]
  - button "Say \"hi\"" [ref=e9]
```"#;

    type Calls = Arc<Mutex<Vec<(String, Value)>>>;

    /// Answers every call with the page at url; records the calls.
    struct FakePlaywright {
        url: String,
        calls: Calls,
    }

    impl Mcp for FakePlaywright {
        fn call(&self, name: &str, args: Value) -> Result<String, String> {
            self.calls.lock().unwrap().push((name.into(), args));
            if name == "browser_tabs" {
                return Ok("### Result\n- 0: (current) [Inbox](https://mail.example.com/)".into());
            }
            Ok(PAGE.replace("{url}", &self.url))
        }
    }

    fn make(url: &str) -> (Browser, Calls) {
        let calls = Calls::default();
        let mut edge = Browser::new(Box::new(FakePlaywright { url: url.into(), calls: calls.clone() }), Vec::new());
        edge.page().unwrap();
        calls.lock().unwrap().clear();
        (edge, calls)
    }

    fn mail() -> (Browser, Calls) {
        make("https://mail.example.com/")
    }

    fn taken(calls: &Calls) -> Vec<(String, Value)> {
        calls.lock().unwrap().clone()
    }

    fn call(name: &str, args: Value) -> (String, Value) {
        (name.into(), args)
    }

    #[test]
    fn open_reads_the_page_and_its_elements() {
        let (mut edge, calls) = mail();
        let said = edge.open(&json!("mail.example.com"), false).unwrap();
        assert_eq!(
            taken(&calls),
            [call("browser_navigate", json!({"url": "https://mail.example.com"})), call("browser_snapshot", json!({}))]
        );
        assert!(said.starts_with("### Page\n- Page URL: https://mail.example.com/"));
        assert_eq!(edge.element("e6").map(|e| (e.role.as_str(), e.name.as_str())), Some(("button", "Send")));
        assert_eq!(edge.element("e9").map(|e| e.name.as_str()), Some("Say \"hi\""));
        edge.open(&json!("https://x.example/a"), true).unwrap();
        assert_eq!(taken(&calls)[2], call("browser_tabs", json!({"action": "new"})));
        assert!(edge.open(&json!("the news"), false).unwrap_err().starts_with("Give a web address"));
    }

    #[test]
    fn actions_become_playwrights_in_order() {
        let (mut edge, calls) = mail();
        let actions = json!([{"do": "type", "ref": "e3", "text": "flight", "submit": true},
                             {"do": "click", "ref": "e7"}, {"do": "select", "ref": "e8", "text": "Newest"},
                             {"do": "key", "text": "page down"}, {"do": "back"}]);
        assert!(edge.act(&actions, false).unwrap().starts_with("Done.\n### Page"));
        let done: Vec<_> = taken(&calls).into_iter().filter(|(name, _)| name != "browser_snapshot").collect();
        assert_eq!(
            done,
            [
                call("browser_type",
                     json!({"element": "searchbox Search mail", "target": "e3", "text": "flight", "submit": true})),
                call("browser_click", json!({"element": "link Inbox (3)", "target": "e7"})),
                call("browser_select_option",
                     json!({"element": "combobox Sort by", "target": "e8", "values": ["Newest"]})),
                call("browser_press_key", json!({"key": "PageDown"})),
                call("browser_navigate_back", json!({})),
            ]
        );
        assert_eq!(taken(&calls).len(), 10); // each change is followed by reading the page
    }

    #[test]
    fn what_sends_buys_or_deletes_asks_first() {
        let (mut edge, calls) = mail();
        let both = edge.act(&json!([{"do": "click", "ref": "e7"}, {"do": "click", "ref": "e6"}]), false);
        assert!(both.unwrap_err().starts_with("e6 (button Send) looks like it sends"));
        assert!(taken(&calls).is_empty()); // nothing done: every action is checked first
        let enter = edge.act(&json!([{"do": "type", "ref": "e4", "text": "hi", "submit": true}]), false);
        assert!(enter.unwrap_err().starts_with("Enter in textbox Message would likely send it"));
        let key = edge.act(&json!([{"do": "type", "ref": "e4", "text": "hi"}, {"do": "key", "text": "enter"}]), false);
        assert!(key.unwrap_err().starts_with("Pressing Enter here"));
        // In a search box, Enter looks it up.
        edge.act(&json!([{"do": "type", "ref": "e3", "text": "flight"}, {"do": "key", "text": "Enter"}]), false).unwrap();
        let delete = edge.act(&json!([{"do": "key", "text": "Delete"}]), false);
        assert!(delete.unwrap_err().starts_with("Pressing Delete"));
        edge.act(&json!([{"do": "type", "ref": "e4", "text": "hi"}, {"do": "click", "ref": "e6"}]), true).unwrap();
        let calls = taken(&calls);
        assert_eq!(calls[calls.len() - 2].0, "browser_click");
        assert_eq!(calls[calls.len() - 2].1["target"], "e6");
    }

    #[test]
    fn never_a_password_and_only_known_elements() {
        let (mut edge, _) = mail();
        let password = edge.act(&json!([{"do": "type", "ref": "e5", "text": "hunter2"}]), true).unwrap_err();
        assert!(password.contains("never types passwords"));
        let missing = edge.act(&json!([{"do": "click", "ref": "e99"}]), false).unwrap_err();
        assert!(missing.starts_with("There's no element e99"));
        let none = edge.act(&json!([{"do": "click"}]), false).unwrap_err();
        assert!(none.starts_with("There's no element (none)"));
        let unknown = edge.act(&json!([{"do": "scroll"}]), false).unwrap_err();
        assert_eq!(unknown, "Unknown action 'scroll': click, double_click, type, select, key or back.");
        let nothing = edge.act(&json!(["click"]), false).unwrap_err();
        assert_eq!(nothing, "Unknown action None: click, double_click, type, select, key or back.");
        assert_eq!(edge.act(&json!([]), false).unwrap_err(), "Say what to do: a list of actions.");
    }

    #[test]
    fn bank_and_payment_sites_are_read_only() {
        let (mut edge, _) = make("https://ibank.bog.ge/accounts");
        for risky in [false, true] {
            let refused = edge.act(&json!([{"do": "click", "ref": "e7"}]), risky).unwrap_err();
            assert!(refused.contains("only reads here, and never clicks or types, even with his yes"));
        }
        edge.act(&json!([{"do": "back"}]), false).unwrap(); // leaving is fine
        let (mut paypal, _) = make("https://www.paypal.com/");
        let opened = paypal.open(&json!("paypal.com"), false).unwrap();
        assert!(opened.starts_with("(A bank or payment site: you can only read here.)\n"));
        let urls = ["https://tbconline.ge/x", "https://www.libertybank.ge/", "https://wise.com/",
                    "https://mail.google.com/", "https://www.bookmyshow.com/"];
        assert_eq!(urls.map(|url| edge.read_only(url)), [true, true, true, false, false]);
        edge.read_only_sites = extra_read_only(" MyShop.ge , ");
        assert!(edge.read_only("https://pay.myshop.ge/") && !edge.read_only("https://notmyshop.ge/"));
        assert_eq!(host("https://user@Pay.Example.com:8443/a?b#c"), "pay.example.com");
        assert_eq!((host("about:blank"), host("//x.ge/a"), host("http://[::1]:80/")), ("".into(), "x.ge".into(), "::1".into()));
    }

    #[test]
    fn tabs() {
        let (mut edge, calls) = mail();
        assert!(edge.tabs(&json!({"action": "list"})).unwrap().contains("(current) [Inbox]"));
        edge.tabs(&json!({"action": "select", "index": "2"})).unwrap();
        assert_eq!(taken(&calls).last().unwrap(), &call("browser_tabs", json!({"action": "select", "index": 2})));
        assert_eq!(edge.tabs(&json!({"action": "close"})).unwrap_err(), "close needs the tab's index (from list).");
        assert_eq!(edge.tabs(&json!({"action": "open"})).unwrap_err(), "action is list, new, select or close.");
    }

    #[test]
    fn offered_only_with_the_extensions_token() {
        // Without it only the brain's send_message is there: Natro's own Edge needs no extension.
        let names = |specs: Vec<Value>| specs.iter().map(|spec| spec["name"].as_str().unwrap().to_string()).collect::<Vec<_>>();
        assert_eq!(names(specs(false)), ["send_message"]);
        let offered: Vec<Value> = specs(true).into_iter().filter(|spec| spec["hidden"] == false).collect();
        assert_eq!(names(offered.clone()), ["browser_open", "browser_page", "browser_act", "browser_act_risky", "browser_tabs"]);
        let asking: Vec<_> = offered.iter().filter(|spec| spec["confirm"] == true).collect();
        assert_eq!(names(asking.into_iter().cloned().collect()), ["browser_act_risky"]);
        let sending = specs(true).pop().unwrap();
        assert!(sending["hidden"] == true && sending["confirm"] == true);
        assert_eq!(sending["input_schema"]["required"], json!(["app", "text"]));
    }

    const CHAT: &str = r#"### Page
- Page URL: {url}
### Snapshot
```yaml
- main [ref=e1]:
  - link "{who}" [ref=e2] [cursor=pointer]:
    - /url: /nino.k/
  - text: nino.k
{messages}
  - textbox "Message..." [ref=e9]{typed}
{send}```"#;

    /// A chat in Instagram's web page: what's typed in its box goes on Send or Enter, and shows in the chat.
    #[derive(Default)]
    struct Chat {
        who: String,
        typed: String,
        sent: Vec<String>,
        calls: Vec<(String, Value)>,
    }

    #[derive(Clone)]
    struct FakeChat(Arc<Mutex<Chat>>);

    impl FakeChat {
        fn new(who: &str) -> FakeChat {
            FakeChat(Arc::new(Mutex::new(Chat { who: who.into(), ..Default::default() })))
        }

        fn calls(&self) -> Vec<(String, Value)> {
            self.0.lock().unwrap().calls.clone()
        }

        fn sent(&self) -> Vec<String> {
            self.0.lock().unwrap().sent.clone()
        }
    }

    impl Mcp for FakeChat {
        fn call(&self, name: &str, args: Value) -> Result<String, String> {
            let mut chat = self.0.lock().unwrap();
            chat.calls.push((name.into(), args.clone()));
            if name == "browser_type" {
                chat.typed = args["text"].as_str().unwrap().into();
            }
            if (name == "browser_click" || name == "browser_press_key") && !chat.typed.is_empty() {
                let typed = std::mem::take(&mut chat.typed);
                chat.sent.push(typed);
            }
            let url = chat.calls.iter().rev().find(|(name, _)| name == "browser_navigate");
            let url = url.map_or("", |(_, args)| args["url"].as_str().unwrap()).to_string();
            let messages: Vec<String> =
                chat.sent.iter().enumerate().map(|(i, text)| format!("  - generic [ref=m{i}]: {text}")).collect();
            let typed = if chat.typed.is_empty() { String::new() } else { format!(": {}", chat.typed) };
            let send = if chat.typed.is_empty() { "" } else { "  - button \"Send\" [ref=e10]\n" };
            Ok(CHAT
                .replace("{url}", &url)
                .replace("{who}", &chat.who)
                .replace("{messages}", &messages.join("\n"))
                .replace("{typed}", &typed)
                .replace("{send}", send))
        }
    }

    fn quick(playwright: impl Mcp + 'static) -> Browser {
        let mut browser = Browser::new(Box::new(playwright), Vec::new());
        (browser.chat_wait, browser.seen_wait, browser.poll) = (Duration::ZERO, Duration::ZERO, Duration::ZERO);
        browser
    }

    fn without_reads(calls: Vec<(String, Value)>) -> Vec<(String, Value)> {
        calls.into_iter().filter(|(name, _)| name != "browser_snapshot").collect()
    }

    #[test]
    fn a_message_from_edge_when_the_phone_is_offline() {
        let chat = FakeChat::new("Nino Kapanadze");
        let said = quick(chat.clone()).send_message(&json!({"app": "instagram", "text": "Running late",
            "name": "Nino Kapanadze", "chat": "thread_shortcut_1234567890_3402", "username": "nino.k"}));
        assert_eq!(said.unwrap(), "Sent to Nino Kapanadze on Instagram.");
        assert_eq!(
            without_reads(chat.calls()),
            [
                call("browser_navigate", json!({"url": "https://www.instagram.com/direct/t/3402/"})),
                call("browser_type",
                     json!({"element": "textbox Message...", "target": "e9", "text": "Running late", "submit": false})),
                call("browser_click", json!({"element": "button Send", "target": "e10"})),
            ]
        );
        assert_eq!(chat.sent(), ["Running late"]);
        assert_eq!(
            chat_urls("messenger", "", "100012345678901", "andria.t"),
            ["https://www.messenger.com/t/100012345678901", "https://www.messenger.com/t/andria.t"]
        );
        assert_eq!(chat_urls("instagram", "", "", "nino.k"), ["https://ig.me/m/nino.k"]);
        assert_eq!(chat_urls("messenger", "thread_shortcut_GROUP:42", "", ""), ["https://www.messenger.com/t/42"]);
        assert_eq!(
            chat_urls("messenger", "thread_shortcut_ADVANCED_CRYPTO_ONE_TO_ONE:7", "1", ""),
            ["https://www.messenger.com/t/1", "https://www.messenger.com/e2ee/t/7"]
        );
    }

    #[test]
    fn someone_elses_chat_gets_nothing() {
        let chat = FakeChat::new("Giorgi");
        let refused = quick(chat.clone())
            .send_message(&json!({"app": "instagram", "text": "Hi", "name": "Nino Kapanadze", "chat": "3402"}));
        assert!(refused.unwrap_err().contains("didn't open in Edge, or isn't plainly theirs, so nothing was sent"));
        assert!(!chat.calls().iter().any(|(name, _)| name == "browser_type") && chat.sent().is_empty());
        let whatsapp = quick(chat).send_message(&json!({"app": "whatsapp", "text": "Hi", "name": "Nino"}));
        assert_eq!(whatsapp.unwrap_err(), "Edge sends messages on Instagram and Messenger only.");
    }

    #[test]
    fn sending_is_in_natros_own_hidden_edge_closed_after() {
        let chat = FakeChat::new("Nino Kapanadze");
        let edge = Edge { his: None, hidden: Mutex::new(quick(chat.clone())) };
        let args = json!({"app": "instagram", "text": "Hi", "name": "Nino Kapanadze", "chat": "thread_shortcut_1_3402"});
        edge.run("send_message", &args).unwrap();
        assert_eq!(chat.sent(), ["Hi"]);
        assert_eq!(chat.calls().last().unwrap(), &call("browser_close", json!({})));
        // Without the token, his Edge's tools aren't there.
        assert_eq!(edge.run("browser_open", &json!({"url": "x.ge"})).unwrap_err(), "The PC has no tool called 'browser_open'.");
        let profile = send_profile();
        let folder = profile.display().to_string();
        let hidden = hidden_edge(&profile);
        assert_eq!(hidden.args(), ["--browser", "msedge", "--headless", "--user-data-dir", folder.as_str()]);
        assert!(profile.ends_with(r"Natro\Edge"));
        let his = his_edge("token", Some("Profile 4"));
        assert_eq!(his.args(), ["--extension", "--browser", "msedge", "--profile-dir-name", "Profile 4"]);
    }

    #[test]
    fn a_pin_page_stops_it_and_is_never_the_box() {
        struct PinPage(Calls);
        impl Mcp for PinPage {
            fn call(&self, name: &str, args: Value) -> Result<String, String> {
                self.0.lock().unwrap().push((name.into(), args));
                Ok("### Page\n- Page URL: https://www.messenger.com/e2ee/t/7\n### Snapshot\n```yaml\n\
                    - heading \"Enter your PIN\" [ref=e1]\n- textbox \"PIN\" [ref=e2]\n```"
                    .into())
            }
        }
        let calls = Calls::default();
        let refused = quick(PinPage(calls.clone())).send_message(&json!({"app": "messenger", "text": "Hi",
            "name": "Andria Beridze", "facebook": "100012345678901"}));
        let refused = refused.unwrap_err();
        assert!(refused.starts_with("Natro's own Edge on the PC isn't signed in to Messenger") && refused.ends_with("Nothing was sent."));
        assert!(!taken(&calls).iter().any(|(name, _)| name == "browser_type"));
    }

    /// The same page whatever is done; records the calls.
    struct Fixed(Calls, String);

    impl Mcp for Fixed {
        fn call(&self, name: &str, args: Value) -> Result<String, String> {
            self.0.lock().unwrap().push((name.into(), args));
            Ok(self.1.clone())
        }
    }

    #[test]
    fn a_sign_in_page_showing_their_name_is_never_the_chat() {
        // Instagram signed out shows "Instagram" everywhere, and one box that isn't for a password:
        // at its login address, or a password box on any other.
        let pages = [("https://www.instagram.com/accounts/login/?next=%2Finstagram%2F", ""),
                     ("https://www.instagram.com/instagram/", "\n  - textbox \"Password\" [ref=e4]")];
        for (url, password) in pages {
            let page = format!(
                "### Page\n- Page URL: {url}\n### Snapshot\n```yaml\n- main [ref=e1]:\n  - img \"Instagram\" [ref=e2]\n  \
                 - textbox \"Phone number, username, or email\" [ref=e3]{password}\n  - button \"Log in\" [ref=e5]\n  \
                 - link \"instagram\" [ref=e6]\n```"
            );
            let calls = Calls::default();
            let mut sender = quick(Fixed(calls.clone(), page));
            sender.chat_wait = Duration::from_secs(10); // a sign-in page ends the wait at once
            let started = Instant::now();
            let refused = sender.send_message(&json!({"app": "instagram", "text": "test", "name": "Instagram",
                                                      "username": "instagram"}));
            let refused = refused.unwrap_err();
            assert!(refused.starts_with("Natro's own Edge on the PC isn't signed in to Instagram"), "{refused}");
            assert!(started.elapsed() < Duration::from_secs(5));
            assert!(!taken(&calls).iter().any(|(name, _)| name == "browser_type"), "{url}");
        }
    }

    /// By hand, on the PC: what the tools answer in headless Edges with profiles of their own (in
    /// the folder NATRO_TEST_PROFILES, made for them), for the calls in the JSON file
    /// NATRO_TEST_CALLS ([["browser_open", {"url": "example.com"}], ...]), as JSON ({"ok",
    /// "result"} each), to compare with the helper's: cargo test -- --ignored --nocapture answers_in_edge
    #[test]
    #[ignore]
    fn answers_in_edge() {
        let file = std::env::var("NATRO_TEST_CALLS").expect("NATRO_TEST_CALLS");
        let calls: Vec<(String, Value)> = serde_json::from_str(&std::fs::read_to_string(file).unwrap()).unwrap();
        let profiles = PathBuf::from(std::env::var("NATRO_TEST_PROFILES").expect("NATRO_TEST_PROFILES"));
        let browser = |name| Mutex::new(Browser::new(Box::new(hidden_edge(&profiles.join(name))), Vec::new()));
        let edge = Edge { his: Some(browser("his")), hidden: browser("hidden") };
        let answers: Vec<Value> = calls
            .iter()
            .map(|(tool, args)| match edge.run(tool, args) {
                Ok(result) => json!({"ok": true, "result": result}),
                Err(problem) => json!({"ok": false, "result": problem}),
            })
            .collect();
        println!("ANSWERS {}", serde_json::to_string(&answers).unwrap());
    }

    #[test]
    fn pages_are_cut_and_names_compared_plainly() {
        let long = format!("### Ran Playwright code\n```js\nawait page.click();\n```\n\n{}", "ა".repeat(PAGE_CHARS + 5));
        let cut = tidy(&long);
        assert!(cut.starts_with("ააა") && cut.ends_with("[the page is longer than this: act on what's here, or open a part of it]"));
        assert_eq!(cut.chars().filter(|c| *c == 'ა').count(), PAGE_CHARS);
        assert_eq!(plain("  Nino\u{a0}KAPANADZE \n"), "nino kapanadze");
        assert_eq!(plain("STRASSE"), plain("Straße"));
        assert_eq!(words_on("- heading \"Say \\\"hi\\\"\" [level=2]\n  - text:  nino.k \n- img\n"), ["Say \"hi\"", "nino.k"]);
    }
}
