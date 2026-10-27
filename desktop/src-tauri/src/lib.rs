//! Natro on the PC: the dock and the talk key, the chat, the tray, connected to
//! Natro's brain, and files to and from the phone.

mod audio;
mod brain;
mod chat;
mod config;
mod dock;
mod double_tap;
mod files;
mod helper;
mod icons;
mod keys;
mod recorder;
mod reply;
mod settings;
mod startup;
mod talk_key;
mod tray;
mod verbose;

use std::sync::Arc;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, ShortcutState};

use brain::Brain;
use chat::Chat;
use config::Config;
use dock::Dock;
use files::Files;
use helper::Helper;
use icons::Icons;
use keys::Keys;
use reply::Reply;
use settings::Settings;
use verbose::Verbose;

pub fn run() {
    tauri::Builder::default()
        // First: a second start (Explorer's "Send to") hands its files over and ends.
        .plugin(tauri_plugin_single_instance::init(|app, args, _| {
            files::from_command_line(app, &args);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                // The keys are the keybinds (settings.rs), which Settings changes.
                .with_handler(|app, shortcut, event| {
                    let dock = app.state::<Dock>();
                    let keys = app.state::<Settings>().keybinds();
                    let pressed = event.state == ShortcutState::Pressed;
                    if *shortcut == keys.talk {
                        dock.talk_key(app, pressed);
                    } else if !pressed {
                    } else if *shortcut == keys.chat {
                        app.state::<Chat>().toggle(app);
                    } else if shortcut.key == Code::Escape {
                        // The dock first; once it's gone, the reply box.
                        if dock.is_shown() {
                            dock.cancel(app);
                        } else {
                            app.state::<Reply>().close(app);
                        }
                    } else if *shortcut == keys.yes {
                        dock.answer(app, true, &settings::label(keys.yes));
                    } else if *shortcut == keys.no {
                        dock.answer(app, false, &settings::label(keys.no));
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
            reply::reply_send,
            reply::reply_keyboard,
            reply::reply_show,
            reply::reply_open,
            chat::chat_state,
            chat::chat_hits,
            chat::chat_send,
            chat::chat_history,
            chat::chat_stop_replying,
            chat::chat_mic,
            chat::chat_answer,
            chat::chat_clear,
            chat::chat_attach,
            chat::chat_picture,
            chat::chat_open_picture,
            chat::chat_queue,
            chat::chat_close,
            settings::settings_state,
            settings::settings_switch,
            settings::settings_size,
            settings::settings_open,
            settings::settings_close,
            settings::settings_capture,
            settings::settings_keybind,
            settings::settings_keybinds_reset,
            settings::settings_twice,
            keys::keys_status,
            keys::keys_unlock,
            keys::keys_save,
            keys::keys_pin,
            keys::keys_lock
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
            log::info!("Natro's files: {} (the helper's .venv in {})", config.home.display(), config.repo.display());
            // For testing, in debug builds: a clip as the microphone, or a typed request.
            let test = |key| cfg!(debug_assertions).then(|| config.get(key)).flatten();
            let test_clip = test("NATRO_TEST_CLIP").map(|clip| config.home.join(clip));
            let test_text = test("NATRO_TEST_TEXT");
            let test_ask = test("NATRO_TEST_ASK");
            let settings = Settings::load(&config.home);
            let switches = settings.switches();
            // Again while it's on: the program may have moved since.
            if switches.startup
                && let Err(e) = startup::set(true)
            {
                log::warn!("couldn't keep starting with Windows: {e}");
            }
            let helper = Arc::new(Helper::start(&config));
            helper.speaking.store(switches.speak, std::sync::atomic::Ordering::SeqCst);
            let chat = Chat::new(&config.home, test_clip.clone());
            let dock = Dock::new(app.handle(), test_clip, test_text, test_ask);
            dock.prepare(app.handle())?;
            let keys = dock.keys(); // Esc, shared with the reply box
            app.manage(dock);
            let reply = Reply::new(switches.replies, keys);
            reply.prepare(app.handle())?;
            app.manage(reply);
            chat.prepare(app.handle())?;
            app.manage(chat);
            app.manage(helper.clone());
            app.manage(Icons::new(helper.clone()));
            let verbose = Arc::new(Verbose::new(&config.home, switches.verbose));
            app.manage(verbose.clone());
            app.manage(settings);
            app.manage(Keys::new(&config.home));
            app.manage(Arc::new(Files::new()));
            app.manage(Brain::start(app.handle().clone(), &config, helper, verbose));
            tray::create(app)?;
            if test("NATRO_TEST_TAPS").is_some() {
                double_tap::count_simulated();
            }
            if test("NATRO_TEST_SETTINGS").is_some() {
                settings::open(app.handle());
            }
            files::add_to_send_to();
            // Started by "Send to" (no Natro was running): these go once she's connected.
            files::from_command_line(app.handle(), &std::env::args().collect::<Vec<_>>());
            settings::hold_keys(app.handle(), true);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Natro");
}
