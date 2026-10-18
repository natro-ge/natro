//! Natro on the PC: the dock and the talk key, the chat, the tray, connected to
//! Natro's brain, and files to and from the phone.

mod audio;
mod brain;
mod chat;
mod config;
mod dock;
mod files;
mod helper;
mod icons;
mod recorder;
mod reply;
mod talk_key;
mod tray;
mod verbose;

use std::sync::Arc;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

use brain::Brain;
use chat::Chat;
use config::Config;
use dock::Dock;
use files::Files;
use helper::Helper;
use icons::Icons;
use reply::Reply;
use verbose::Verbose;

/// Opens the dock and listens; see talk_key.rs for hold and tap.
fn talk_key() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space)
}

/// Opens the chat, or closes it.
fn chat_key() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL), Code::Space)
}

pub fn run() {
    tauri::Builder::default()
        // First: a second start (Explorer's "Send to") hands its files over and ends.
        .plugin(tauri_plugin_single_instance::init(|app, args, _| {
            files::from_command_line(app, &args);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    let dock = app.state::<Dock>();
                    let pressed = event.state == ShortcutState::Pressed;
                    if *shortcut == talk_key() {
                        dock.talk_key(app, pressed);
                    } else if !pressed {
                    } else if *shortcut == chat_key() {
                        app.state::<Chat>().toggle(app);
                    } else if shortcut.key == Code::Escape {
                        // The dock first; once it's gone, the reply box.
                        if dock.is_shown() {
                            dock.cancel(app);
                        } else {
                            app.state::<Reply>().close(app);
                        }
                    } else if *shortcut == dock::yes_key() {
                        dock.answer(app, true, "Ctrl+Alt+Y");
                    } else if *shortcut == dock::no_key() {
                        dock.answer(app, false, "Ctrl+Alt+N");
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            dock::dock_state,
            dock::dock_size,
            dock::dock_answer,
            reply::reply_state,
            reply::reply_size,
            reply::reply_close,
            reply::reply_show,
            reply::reply_open,
            chat::chat_state,
            chat::chat_hits,
            chat::chat_send,
            chat::chat_mic,
            chat::chat_answer,
            chat::chat_clear,
            chat::chat_attach,
            chat::chat_picture,
            chat::chat_open_picture,
            chat::chat_queue,
            chat::chat_close
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let config = Config::load();
            log::info!("Natro's files: {}", config.home.display());
            // For testing, in debug builds: a clip as the microphone, or a typed request.
            let test = |key| cfg!(debug_assertions).then(|| config.get(key)).flatten();
            let test_clip = test("NATRO_TEST_CLIP").map(|clip| config.home.join(clip));
            let test_text = test("NATRO_TEST_TEXT");
            let test_ask = test("NATRO_TEST_ASK");
            let helper = Arc::new(Helper::start(&config));
            let chat = Chat::new(test_clip.clone());
            let dock = Dock::new(app.handle(), test_clip, test_text, test_ask);
            dock.prepare(app.handle())?;
            let keys = dock.keys(); // Esc, shared with the reply box
            app.manage(dock);
            let reply = Reply::new(&config.home, keys);
            reply.prepare(app.handle())?;
            app.manage(reply);
            chat.prepare(app.handle())?;
            app.manage(chat);
            app.manage(helper.clone());
            app.manage(Icons::new(helper.clone()));
            let verbose = Arc::new(Verbose::new(&config.home));
            app.manage(verbose.clone());
            app.manage(Arc::new(Files::new()));
            app.manage(Brain::start(app.handle().clone(), &config, helper, verbose));
            tray::create(app)?;
            files::add_to_send_to();
            // Started by "Send to" (no Natro was running): these go once she's connected.
            files::from_command_line(app.handle(), &std::env::args().collect::<Vec<_>>());
            if let Err(e) = app.global_shortcut().register(talk_key()) {
                log::error!("Ctrl+Alt+Space is taken by another program: {e}");
            }
            if let Err(e) = app.global_shortcut().register(chat_key()) {
                log::error!("Ctrl+Space is taken by another program: {e}");
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Natro");
}
