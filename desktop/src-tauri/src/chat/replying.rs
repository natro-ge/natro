//! Answering a message from the phone in the chat: the reply box's "answer in the chat" opens it quoting the
//! message (ChatReplying.svelte), and what's typed goes from the phone, in that message's own app
//! (chat.rs's send). Its ✕, Esc or closing the chat stops answering.

use tauri::AppHandle;

use super::{Chat, ChatEvent, Replying};

impl Chat {

    /// His answer to a message from the phone, typed in the reply box (reply.rs) and on its way: it shows here
    /// as his, and her reply to it isn't spoken.
    pub fn answered_message(&self, app: &AppHandle, request: &str, to: &str, text: &str) {
        let changed = self.state.lock().unwrap().log.asked(request, &format!("To {to}: {text}"), false);
        self.changed(app, changed);
    }

    /// The reply box's "answer in the chat" on a message from the phone (reply.rs): the chat opens answering it.
    pub fn reply_to(&self, app: &AppHandle, card: &crate::reply::Card) {
        let replying = Replying {
            id: card.id.clone(),
            from: card.from.clone(),
            app: card.app.clone(),
            text: card.text.clone(),
        };
        let open = {
            let mut state = self.state.lock().unwrap();
            state.replying = Some(replying.clone());
            state.open
        };
        if open {
            self.emit(app, ChatEvent::Replying { replying: Some(replying) });
        } else {
            self.open(app);
        }
    }

    /// Not answering that message after all (its ✕).
    pub(super) fn stop_replying(&self, app: &AppHandle) {
        self.state.lock().unwrap().replying = None;
        self.emit(app, ChatEvent::Replying { replying: None });
    }
}
