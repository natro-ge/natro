//! Where Natro works for what's asked on the PC (server/natro_agent/works_on.py): here, or on the
//! phone. The chat's switch and the tray's two lines change it, and so does asking, here or on the
//! phone ("work on the phone", "make the PC work on the phone"); while it's the phone, the dock
//! shows a badge. It's remembered in settings.json (settings.rs). The brain hears it in the hello
//! and whenever it's changed here, and says when it changed it, and which of the owner's devices are online:
//! set to the phone while the phone is offline, Natro works here, and the badge and the switch say so.

use std::sync::atomic::{AtomicBool, Ordering};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::{AppHandle, Emitter, Manager};

use crate::brain::Brain;
use crate::settings::Settings;
use crate::tray;

#[derive(Serialize, Deserialize, Clone, Copy, Debug, Default, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum WorksOn {
    #[default]
    Pc,
    Phone,
}

impl WorksOn {
    /// As the brain names the devices.
    pub fn name(self) -> &'static str {
        match self {
            WorksOn::Pc => "pc",
            WorksOn::Phone => "phone",
        }
    }

    fn named(name: &str) -> Option<WorksOn> {
        match name {
            "pc" => Some(WorksOn::Pc),
            "phone" => Some(WorksOn::Phone),
            _ => None,
        }
    }
}

/// Whether the phone is online, as the brain last said (not while the PC isn't connected).
static PHONE_ONLINE: AtomicBool = AtomicBool::new(false);

/// For the chat's switch and the dock's badge: where Natro works, and whether that device is online.
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Shown {
    device: WorksOn,
    online: bool,
}

fn shown(works_on: WorksOn, phone_online: bool) -> Shown {
    Shown {
        device: works_on,
        online: works_on == WorksOn::Pc || phone_online,
    }
}

/// Changed here (the chat, the tray): remembered, shown, and told to the brain (when it's
/// offline, the next hello tells it).
pub fn set(app: &AppHandle, works_on: WorksOn) {
    if app.state::<Settings>().set_works_on(works_on) {
        log::info!("Natro works on the {} for what's asked here", works_on.name());
        app.state::<Brain>().send_json(json!({"type": "works_on", "device": works_on.name()}));
    }
    show(app);
}

/// The brain's works_on: set by asking, or this PC's own change said back.
pub fn from_brain(app: &AppHandle, message: &Value) {
    if let Some(works_on) = WorksOn::named(message["device"].as_str().unwrap_or_default()) {
        if app.state::<Settings>().set_works_on(works_on) {
            log::info!("Natro works on the {} for what's asked here (asked for)", works_on.name());
        }
        show(app);
    }
}

/// The brain's online: which of the owner's devices are connected.
pub fn online(app: &AppHandle, message: &Value) {
    let devices = message["devices"].as_array().map(Vec::as_slice).unwrap_or_default();
    PHONE_ONLINE.store(devices.iter().any(|device| device == "phone"), Ordering::SeqCst);
    show(app);
}

/// The connection to the brain is lost: nothing is known to be online.
pub fn lost(app: &AppHandle) {
    PHONE_ONLINE.store(false, Ordering::SeqCst);
    show(app);
}

/// Whether the phone is online, as the brain last said (the tray's line for it).
pub fn phone_online() -> bool {
    PHONE_ONLINE.load(Ordering::SeqCst)
}

fn now(app: &AppHandle) -> Shown {
    let works_on = app.try_state::<Settings>().map(|settings| settings.works_on()).unwrap_or_default();
    shown(works_on, phone_online())
}

fn show(app: &AppHandle) {
    let now = now(app);
    tray::show_works_on(app, now.device, phone_online());
    for page in ["chat", "dock"] {
        let _ = app.emit_to(page, "works_on", now.clone());
    }
}

/// The chat and the dock ask once their page has loaded.
#[tauri::command]
pub fn works_on_state(app: AppHandle) -> Shown {
    now(&app)
}

/// The chat's switch: "pc" or "phone".
#[tauri::command]
pub fn works_on_set(app: AppHandle, device: String) {
    if let Some(works_on) = WorksOn::named(&device) {
        set(&app, works_on);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn named_as_the_brain_names_them() {
        for works_on in [WorksOn::Pc, WorksOn::Phone] {
            assert_eq!(WorksOn::named(works_on.name()), Some(works_on));
            assert_eq!(serde_json::to_value(works_on).unwrap(), json!(works_on.name()));
        }
        assert_eq!(WorksOn::named("tablet"), None);
    }

    #[test]
    fn the_phone_shows_offline_until_the_brain_says_its_online() {
        assert_eq!(shown(WorksOn::Pc, false), Shown { device: WorksOn::Pc, online: true });
        assert_eq!(shown(WorksOn::Phone, false), Shown { device: WorksOn::Phone, online: false });
        assert_eq!(shown(WorksOn::Phone, true), Shown { device: WorksOn::Phone, online: true });
        let page = serde_json::to_value(shown(WorksOn::Phone, true)).unwrap();
        assert_eq!(page, json!({"device": "phone", "online": true}));
    }
}
