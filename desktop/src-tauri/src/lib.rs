//! Natro on the PC: the dock and the talk key, the chat, the tray, connected to
//! Natro's brain, and files to and from the phone.

mod audio;
mod automation;
mod brain;
mod chat;
mod config;
mod difflib;
mod dock;
mod double_tap;
mod files;
mod icons;
mod keys;
mod playing;
mod recorder;
mod reply;
mod settings;
mod startup;
mod talk_key;
mod tools;
mod tray;
mod verbose;
mod voice;
mod works_on;

use std::sync::Arc;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, ShortcutState};

use tools::apps::{Apps, StartMenu};
use brain::Brain;
use chat::Chat;
use config::Config;
use dock::Dock;
use files::Files;
use icons::Icons;
use keys::Keys;
use reply::Reply;
use settings::Settings;
use tools::Tools;
use verbose::Verbose;
use voice::Voice;

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
            reply::reply_mute,
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
            chat::chat_for_natro,
            chat::chat_detach,
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
            keys::keys_lock,
            works_on::works_on_state,
            works_on::works_on_set
        ])
        .setup(|app| {
            // %LOCALAPPDATA%\dev.natro.desktop\logs\Natro.log (and the console in debug builds),
            // found by Windows rather than by Natro's folder, so it's there when the folder isn't.
            app.handle().plugin(
                tauri_plugin_log::Builder::default()
                    .level(log::LevelFilter::Info)
                    // read_file's PDF reading says a lot about PDFs it finds odd; their errors are enough.
                    .level_for("pdf_extract", log::LevelFilter::Error)
                    .level_for("lopdf", log::LevelFilter::Error)
                    .max_file_size(1_000_000)
                    .rotation_strategy(tauri_plugin_log::RotationStrategy::KeepSome(1))
                    .build(),
            )?;
            let config = Config::load();
            log::info!("Natro's files: {}", config.home.display());
            if let Some(problem) = &config.problem {
                log::error!(
                    "Natro's files: {problem}. Without them there's no brain to reach (no NATRO_SERVER), \
                     no voice and no settings. A folder made by a program the Claude app started is in \
                     Claude's own storage (%LOCALAPPDATA%\\Packages\\Claude_...\\LocalCache\\Local), which \
                     only Claude's programs see: move it out from Explorer or a normal terminal."
                );
            }
            // For testing, in debug builds: a clip as the microphone, or a typed request.
            let test = |key| cfg!(debug_assertions).then(|| config.get(key)).flatten();
            let test_clip = test("NATRO_TEST_CLIP").map(|clip| config.home.join(clip));
            let test_text = test("NATRO_TEST_TEXT");
            let test_ask = test("NATRO_TEST_ASK");
            let settings = Settings::load(&config.home);
            let switches = settings.switches();
            // Before the pages: the chat and the dock ask where Natro works (works_on.rs) once they've loaded.
            app.manage(settings);
            // Again while it's on: the program may have moved since.
            if switches.startup
                && let Err(e) = startup::set(true)
            {
                log::warn!("couldn't keep starting with Windows: {e}");
            }
            let voice = Arc::new(Voice::start(&config, switches.speak));
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
            app.manage(voice.clone());
            // Read ahead of the first open_app or app icon (about a second).
            let menu = Arc::new(StartMenu::new());
            let reading = menu.clone();
            std::thread::spawn(move || reading.warm_up());
            app.manage(Icons::new(menu.clone()));
            app.manage(Arc::new(Tools::new(Apps::new(menu), &config)));
            let verbose = Arc::new(Verbose::new(&config.home, switches.verbose));
            app.manage(verbose.clone());
            app.manage(Keys::new(&config.home));
            app.manage(Arc::new(Files::new()));
            app.manage(Brain::start(app.handle().clone(), &config, voice, verbose));
            tray::create(app, config.problem.clone())?;
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
