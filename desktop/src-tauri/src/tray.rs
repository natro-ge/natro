//! Natro's icon by the clock: whether she's online, this month's spending,
//! "Speak replies", "Show replies as text", her last reply, "Verbose", open the
//! dock or the chat, send files to the phone, Settings (settings.rs), Exit. The
//! switches are the Settings window's too, and remembered there.
//! A click on the icon shows or hides her last reply (or opens the dock if there's none yet).

use std::sync::Arc;

use serde_json::Value;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Manager, Wry};
use tauri_plugin_dialog::DialogExt;

use crate::chat::Chat;
use crate::dock::Dock;
use crate::files::Files;
use crate::reply::Reply;
use crate::settings::{self, Keybinds, Settings, Switches};

/// The menu's lines that change.
struct Lines {
    connection: MenuItem<Wry>,
    spending: MenuItem<Wry>,
    speak: CheckMenuItem<Wry>,
    replies: CheckMenuItem<Wry>,
    verbose: CheckMenuItem<Wry>,
    open: MenuItem<Wry>,
    chat: MenuItem<Wry>,
}

/// The dock's and the chat's lines, with their keybinds: "Talk to Natro (Ctrl+Alt+Space)".
fn key_lines(keybinds: &Keybinds) -> (String, String) {
    (
        format!("Talk to Natro ({})", settings::label(keybinds.talk)),
        format!("Chat with Natro ({})", settings::label(keybinds.chat)),
    )
}

pub fn create(app: &App) -> tauri::Result<()> {
    let connection = MenuItem::with_id(
        app,
        "connection",
        "Connecting to Natro…",
        false,
        None::<&str>,
    )?;
    let spending = MenuItem::with_id(
        app,
        "spending",
        "This month: not known yet",
        false,
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
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &connection,
            &spending,
            &separator,
            &speak,
            &replies,
            &last,
            &verbose,
            &open,
            &chat,
            &send,
            &separator,
            &settings,
            &exit,
        ],
    )?;
    app.manage(Lines {
        connection,
        spending,
        speak: speak.clone(),
        replies: replies.clone(),
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
        .tooltip("Natro: connecting…")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => app.state::<Dock>().toggle(app),
            "chat" => app.state::<Chat>().toggle(app),
            // A click has checked or unchecked it already.
            "speak" => app.state::<Settings>().set(app, "speak", speak.is_checked().unwrap_or(true)),
            "replies" => app.state::<Settings>().set(app, "replies", replies.is_checked().unwrap_or(true)),
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
    let text = if online {
        "Natro is online"
    } else {
        "Natro is offline (reconnecting)"
    };
    if let Some(lines) = app.try_state::<Lines>() {
        let _ = lines.connection.set_text(text);
    }
    if let Some(tray) = app.tray_by_id("natro") {
        let _ = tray.set_tooltip(Some(format!(
            "Natro: {}",
            if online { "online" } else { "offline" }
        )));
    }
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
