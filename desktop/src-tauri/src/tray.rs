//! Natro's icon by the clock: whether she's online (or that her files are
//! missing), this month's spending, where she works for what's asked here (this PC
//! or the phone: works_on.rs, also in its tooltip), "Speak replies", "Show replies
//! as text", her last reply, "Verbose", open the dock or the chat, send files to
//! the phone, sign in Natro's own Edge (for messages from the PC: browser.rs), Settings
//! (settings.rs), Exit. The switches are the Settings window's too, and remembered there.
//! A click on the icon shows or hides her last reply (or opens the dock if there's none yet).

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::Value;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Manager, Wry};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

use crate::chat::Chat;
use crate::dock::Dock;
use crate::files::Files;
use crate::reply::Reply;
use crate::settings::{self, Keybinds, Settings, Switches};
use crate::tools::browser;
use crate::works_on::{self, WorksOn};

/// The menu's lines that change.
struct Lines {
    connection: MenuItem<Wry>,
    /// Why Natro's files couldn't be read (config.rs): shown instead of "reconnecting".
    problem: Option<String>,
    spending: MenuItem<Wry>,
    /// Whether Natro is online, for the tooltip.
    online: AtomicBool,
    /// Where Natro works: two lines, one checked.
    here: CheckMenuItem<Wry>,
    phone: CheckMenuItem<Wry>,
    speak: CheckMenuItem<Wry>,
    replies: CheckMenuItem<Wry>,
    keep_listening: CheckMenuItem<Wry>,
    verbose: CheckMenuItem<Wry>,
    open: MenuItem<Wry>,
    chat: MenuItem<Wry>,
}

/// The dock's and the chat's lines, with their keybinds: "Talk to Natro (Ctrl+Alt+Space)".
fn key_lines(keybinds: &Keybinds) -> (String, String) {
    (
        format!("Talk to Natro ({})", settings::label(keybinds.talk)),
        match keybinds.chat_twice {
            Some(tap) => format!("Chat with Natro ({} or {} twice)", settings::label(keybinds.chat), tap.label()),
            None => format!("Chat with Natro ({})", settings::label(keybinds.chat)),
        },
    )
}

/// The icon's tooltip: the connection's, and where Natro works when that's the phone.
fn tooltip(base: &str, online: bool, works_on: WorksOn, phone_online: bool) -> String {
    match (online, works_on) {
        (true, WorksOn::Phone) if phone_online => format!("{base}, works on the phone"),
        (true, WorksOn::Phone) => format!("{base}, set to the phone (offline)"),
        _ => base.to_string(),
    }
}

/// The phone's line: "(offline)" while it is, as she then works here.
fn phone_line(phone_online: bool) -> &'static str {
    if phone_online { "Work on the phone" } else { "Work on the phone (offline)" }
}

/// The connection's line and the icon's tooltip. The tooltip stays short: Windows keeps 127 characters.
fn connection_lines(online: bool, problem: Option<&str>) -> (String, &'static str) {
    match (online, problem) {
        (true, _) => ("Natro is online".into(), "Natro: online"),
        (false, Some(problem)) => (format!("Natro is offline: {problem}"), "Natro: offline, its files are missing"),
        (false, None) => ("Natro is offline (reconnecting)".into(), "Natro: offline"),
    }
}

pub fn create(app: &App, problem: Option<String>) -> tauri::Result<()> {
    let (first_line, first_tip) = match problem.as_deref() {
        Some(problem) => connection_lines(false, Some(problem)),
        None => ("Connecting to Natro…".into(), "Natro: connecting…"),
    };
    let connection = MenuItem::with_id(app, "connection", first_line, false, None::<&str>)?;
    let spending = MenuItem::with_id(
        app,
        "spending",
        "This month: not known yet",
        false,
        None::<&str>,
    )?;
    let works_on = app.state::<Settings>().works_on();
    let here = CheckMenuItem::with_id(app, "work_here", "Work on this PC", true, works_on == WorksOn::Pc, None::<&str>)?;
    let phone = CheckMenuItem::with_id(
        app,
        "work_phone",
        phone_line(false),
        true,
        works_on == WorksOn::Phone,
        None::<&str>,
    )?;
    let switches = app.state::<Settings>().switches();
    let speak =
        CheckMenuItem::with_id(app, "speak", "Speak replies", true, switches.speak, None::<&str>)?;
    let replies = CheckMenuItem::with_id(
        app,
        "replies",
        "Show replies as text",
        true,
        switches.replies,
        None::<&str>,
    )?;
    let keep_listening = CheckMenuItem::with_id(
        app,
        "keep_listening",
        "Keep listening after a reply",
        true,
        switches.keep_listening,
        None::<&str>,
    )?;
    let last = MenuItem::with_id(app, "last", "Show last reply", true, None::<&str>)?;
    let verbose = CheckMenuItem::with_id(
        app,
        "verbose",
        "Verbose (keep recordings and logs)",
        true,
        switches.verbose,
        None::<&str>,
    )?;
    let (talk_line, chat_line) = key_lines(&app.state::<Settings>().keybinds());
    let open = MenuItem::with_id(app, "open", talk_line, true, None::<&str>)?;
    let chat = MenuItem::with_id(app, "chat", chat_line, true, None::<&str>)?;
    let send = MenuItem::with_id(app, "send", "Send files to the phone…", true, None::<&str>)?;
    let edge = MenuItem::with_id(app, "edge", "Sign in Natro's own Edge…", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &connection,
            &spending,
            &separator,
            &here,
            &phone,
            &separator,
            &speak,
            &replies,
            &keep_listening,
            &last,
            &verbose,
            &open,
            &chat,
            &send,
            &separator,
            &edge,
            &settings,
            &exit,
        ],
    )?;
    app.manage(Lines {
        connection,
        problem,
        spending,
        online: AtomicBool::new(false),
        here,
        phone,
        speak: speak.clone(),
        replies: replies.clone(),
        keep_listening: keep_listening.clone(),
        verbose: verbose.clone(),
        open: open.clone(),
        chat: chat.clone(),
    });

    TrayIconBuilder::with_id("natro")
        .icon(
            app.default_window_icon()
                .expect("the bundle has an icon")
                .clone(),
        )
        .tooltip(first_tip)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => app.state::<Dock>().toggle(app),
            "chat" => app.state::<Chat>().toggle(app),
            // A click has checked or unchecked it: works_on sets both as they are.
            "work_here" => works_on::set(app, WorksOn::Pc),
            "work_phone" => works_on::set(app, WorksOn::Phone),
            // A click has checked or unchecked it already.
            "speak" => app.state::<Settings>().set(app, "speak", speak.is_checked().unwrap_or(true)),
            "replies" => app.state::<Settings>().set(app, "replies", replies.is_checked().unwrap_or(true)),
            "keep_listening" => {
                app.state::<Settings>().set(app, "keep_listening", keep_listening.is_checked().unwrap_or(true))
            }
            "last" => {
                app.state::<Reply>().show_last(app);
            }
            "verbose" => app.state::<Settings>().set(app, "verbose", verbose.is_checked().unwrap_or(false)),
            "settings" => settings::open(app),
            "send" => {
                let app = app.clone();
                app.dialog()
                    .file()
                    .set_title("Send to your phone")
                    .pick_files(move |picked| {
                        let paths = picked
                            .unwrap_or_default()
                            .into_iter()
                            .filter_map(|path| path.into_path().ok())
                            .collect::<Vec<_>>();
                        if !paths.is_empty() {
                            app.state::<Arc<Files>>().send_paths(&app, paths, "phone");
                        }
                    });
            }
            // Instagram and Messenger in her hidden Edge, for messages sent from the PC: he signs in himself.
            "edge" => {
                let (said, kind) = match browser::sign_in() {
                    Ok(said) => (said, MessageDialogKind::Info),
                    Err(problem) => (problem, MessageDialogKind::Error),
                };
                app.dialog().message(said).title("Natro's own Edge").kind(kind).show(|_| {});
            }
            "exit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if !app.state::<Reply>().toggle(app) {
                    app.state::<Dock>().toggle(app);
                }
            }
        })
        .build(app)?;
    Ok(())
}

/// The switches, as changed in the Settings window (or here).
pub fn show_switches(app: &AppHandle, switches: &Switches) {
    if let Some(lines) = app.try_state::<Lines>() {
        let _ = lines.speak.set_checked(switches.speak);
        let _ = lines.replies.set_checked(switches.replies);
        let _ = lines.keep_listening.set_checked(switches.keep_listening);
        let _ = lines.verbose.set_checked(switches.verbose);
    }
}

/// The keybinds, as changed in the Settings window.
pub fn show_keys(app: &AppHandle, keybinds: &Keybinds) {
    if let Some(lines) = app.try_state::<Lines>() {
        let (talk_line, chat_line) = key_lines(keybinds);
        let _ = lines.open.set_text(talk_line);
        let _ = lines.chat.set_text(chat_line);
    }
}

pub fn show_connection(app: &AppHandle, online: bool) {
    if let Some(lines) = app.try_state::<Lines>() {
        lines.online.store(online, Ordering::SeqCst);
        let _ = lines.connection.set_text(connection_lines(online, lines.problem.as_deref()).0);
    }
    show_tooltip(app);
}

/// Where Natro works (works_on.rs), and whether the phone is online.
pub fn show_works_on(app: &AppHandle, works_on: WorksOn, phone_online: bool) {
    if let Some(lines) = app.try_state::<Lines>() {
        let _ = lines.here.set_checked(works_on == WorksOn::Pc);
        let _ = lines.phone.set_checked(works_on == WorksOn::Phone);
        let _ = lines.phone.set_text(phone_line(phone_online));
    }
    show_tooltip(app);
}

fn show_tooltip(app: &AppHandle) {
    let (Some(lines), Some(tray)) = (app.try_state::<Lines>(), app.tray_by_id("natro")) else {
        return;
    };
    let online = lines.online.load(Ordering::SeqCst);
    let base = connection_lines(online, lines.problem.as_deref()).1;
    let works_on = app.state::<Settings>().works_on();
    let _ = tray.set_tooltip(Some(tooltip(base, online, works_on, works_on::phone_online())));
}

/// From the brain's welcome and each reply: {"spent": 0.42, "budget": 15}.
pub fn show_spending(app: &AppHandle, message: &Value) {
    let (Some(spent), Some(budget)) = (message["spent"].as_f64(), message["budget"].as_f64())
    else {
        return;
    };
    if let Some(lines) = app.try_state::<Lines>() {
        let _ = lines
            .spending
            .set_text(format!("This month: ${spent:.2} of ${budget}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_files_stay_in_the_connection_line() {
        let problem = Some(r"no folder at C:\Users\him\AppData\Local\Natro");
        assert_eq!(
            connection_lines(false, problem),
            (r"Natro is offline: no folder at C:\Users\him\AppData\Local\Natro".into(), "Natro: offline, its files are missing")
        );
        assert_eq!(connection_lines(false, None), ("Natro is offline (reconnecting)".into(), "Natro: offline"));
        assert_eq!(connection_lines(true, problem), ("Natro is online".into(), "Natro: online"));
    }

    #[test]
    fn the_tooltip_says_when_natro_works_on_the_phone() {
        assert_eq!(tooltip("Natro: online", true, WorksOn::Pc, true), "Natro: online");
        assert_eq!(tooltip("Natro: online", true, WorksOn::Phone, true), "Natro: online, works on the phone");
        assert_eq!(tooltip("Natro: online", true, WorksOn::Phone, false), "Natro: online, set to the phone (offline)");
        assert_eq!(tooltip("Natro: offline", false, WorksOn::Phone, false), "Natro: offline");
    }
}
