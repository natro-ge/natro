//! Files from the chat's +: to the phone (as the tray's "Send files to the phone…"), and for Natro, which wait
//! over the box until his next request takes them (chat.rs's send and listened).

use std::path::PathBuf;
use std::sync::Arc;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

use super::window::window;
use super::{Chat, ChatEvent};
use crate::files::Files;


/// The most files for Natro on one request (MOST_ATTACHED in server/natro_agent/server.py).
const MOST_FILES: usize = 20;

impl Chat {
    /// The + menu's "Files for Natro": they wait over the box for his next request.
    pub(super) fn for_natro(&self, app: &AppHandle) {
        let sender = app.clone();
        app.dialog()
            .file()
            .set_parent(&window(app))
            .set_title("Files for Natro")
            .pick_files(move |picked| {
                let paths = picked
                    .unwrap_or_default()
                    .into_iter()
                    .filter_map(|path| path.into_path().ok())
                    .collect::<Vec<_>>();
                if !paths.is_empty() {
                    let chat = sender.state::<Chat>();
                    let mut state = chat.state.lock().unwrap();
                    add_files(&mut state.attached, paths);
                    let names = names_of(&state.attached);
                    drop(state);
                    chat.emit(&sender, ChatEvent::Attached { names });
                }
            });
    }

    /// A file's ✕ over the box.
    pub(super) fn detach(&self, app: &AppHandle, at: usize) {
        let mut state = self.state.lock().unwrap();
        if at < state.attached.len() {
            state.attached.remove(at);
        }
        let names = names_of(&state.attached);
        drop(state);
        self.emit(app, ChatEvent::Attached { names });
    }

    pub(super) fn attach(&self, app: &AppHandle) {
        let sender = app.clone();
        app.dialog()
            .file()
            .set_parent(&window(app))
            .set_title("Send to your phone")
            .pick_files(move |picked| {
                let paths = picked
                    .unwrap_or_default()
                    .into_iter()
                    .filter_map(|path| path.into_path().ok())
                    .collect::<Vec<_>>();
                if !paths.is_empty() {
                    sender.state::<Arc<Files>>().send_paths(&sender, paths, "phone");
                }
            });
    }
}

/// The names of files, as the chat shows them.
pub(super) fn names_of(files: &[PathBuf]) -> Vec<String> {
    files.iter().map(|path| path.file_name().unwrap_or(path.as_os_str()).to_string_lossy().to_string()).collect()
}

/// The files waiting for a request, gone now it's made: their names.
pub(super) fn take_names(files: &mut Vec<PathBuf>) -> Vec<String> {
    names_of(&std::mem::take(files))
}

/// More files for Natro: each once, and no more than the brain takes.
fn add_files(files: &mut Vec<PathBuf>, more: Vec<PathBuf>) {
    for path in more {
        if !files.contains(&path) && files.len() < MOST_FILES {
            files.push(path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn files_for_natro_come_once_and_no_more_than_the_brain_takes() {
        let path = |n: usize| PathBuf::from(format!("C:\\Scans\\{n}.pdf"));
        let mut files = vec![path(0)];
        add_files(&mut files, vec![path(0), path(1)]);
        assert_eq!(names_of(&files), ["0.pdf", "1.pdf"]);
        add_files(&mut files, (0..40).map(path).collect());
        assert_eq!(files.len(), MOST_FILES);
        assert_eq!(take_names(&mut files).len(), MOST_FILES);
        assert!(files.is_empty());
    }
}
