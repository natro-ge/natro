//! The chat's entries: what the bubbles show (Exchange), and how requests, her replies, questions and the
//! shared chat's entries change them (Log). No window here: chat.rs shows what this says changed.

use std::collections::HashSet;

use serde::Serialize;
use serde_json::Value;

/// The most exchanges kept; the oldest go.
const MOST: usize = 200;
/// How many recordings heard but not (yet) known as requests are remembered.
const REMEMBERED: usize = 16;
/// What he typed to her that Up and Down go through: the newest.
const MOST_TYPED: usize = 100;

/// One request and Natro's answer, as the bubbles show them.
#[derive(Serialize, Clone, Default, Debug)]
pub struct Exchange {
    /// The request's id ("r12").
    pub(super) id: String,
    /// What the owner asked: typed, or her English for what was said (empty until she has it).
    pub(super) asked: String,
    /// The Georgian she heard, for a spoken request.
    pub(super) heard: String,
    pub(super) spoken: bool,
    /// Her reply so far, and whether all of it is there.
    pub(super) reply: String,
    pub(super) done: bool,
    /// What she's doing ("Opening Spotify"), while working.
    pub(super) step: Option<Step>,
    /// Her questions before something risky, with the answers.
    pub(super) questions: Vec<Question>,
    /// Why it failed.
    pub(super) problem: String,
    /// The device it was asked on ("phone"), for a request made on the other one; empty for the PC's.
    pub(super) from: String,
    /// Its pictures, by id (each comes once, as ChatEvent::Picture).
    pub(super) pictures: Vec<String>,
    /// A request he made on that device while the PC was off ("phone"), run on the PC after.
    pub(super) queued: String,
    /// A picture he sent, not a request.
    pub(super) sent: bool,
    /// A reminder that went off: Natro's words alone, with no request.
    pub(super) reminder: bool,
    /// The names of the files he attached for Natro: they go after his words, once she has them.
    #[serde(skip)]
    pub(super) files: Vec<String>,
    /// A background job's task (server/natro_agent/jobs.py): Natro's work on it, then what she found.
    pub(super) job: String,
    /// Something a watcher saw (server/natro_agent/watchers.py): her words alone, as a reminder's.
    pub(super) alert: bool,
}

#[derive(Serialize, Clone, Debug, PartialEq)]
pub(super) struct Step {
    pub(super) label: String,
    /// A kind of picture (glyphs.ts)...
    pub(super) icon: String,
    /// ...or the app's own icon, as a data URL (icons.rs).
    pub(super) image: Option<String>,
}

#[derive(Serialize, Clone, Debug, PartialEq)]
pub(super) struct Question {
    /// The brain's id for it ("c4").
    pub(super) id: String,
    pub(super) text: String,
    /// Empty while she waits; then "Yes", "No", or the words typed or said ("…" until they're heard).
    pub(super) answer: String,
    /// The device that asked, for a question about a request made on the other one ("phone").
    pub(super) device: String,
}

/// A message from the phone the chat is answering (reply.rs): the brain's id for it, who it's from, in
/// which app, and its words (quoted over the box).
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Replying {
    pub(super) id: String,
    pub(super) from: String,
    pub(super) app: String,
    pub(super) text: String,
}

impl Replying {
    /// Whom the answer goes to: "Nino on WhatsApp".
    pub(super) fn to(&self) -> String {
        format!("{} on {}", self.from, self.app)
    }
}

/// A request he made on the phone while the PC was off, kept for it (pending.py).
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Queued {
    pub(super) id: String,
    pub(super) text: String,
    /// The device it was asked on.
    pub(super) from: String,
    pub(super) time: u64,
}

/// The conversation the bubbles show, as the owner and the brain's messages change
/// it. Each change gives the exchange that changed (for the page), or None.
#[derive(Default)]
pub(super) struct Log {
    pub(super) exchanges: Vec<Exchange>,
    /// The chat's own requests still waiting for their reply: hers to them isn't spoken.
    pub(super) mine: HashSet<String>,
    /// What she heard of recordings made elsewhere (the dock): (id, Georgian, English),
    /// until one turns out to be a request (a spoken yes or no to a question never gets a reply).
    pub(super) heard: Vec<(String, String, String)>,
    /// Spoken answers to the chat's questions: (the recording's id, the question's).
    pub(super) answers: Vec<(String, String)>,
}

impl Log {
    /// A request from the chat: typed, or spoken (what was said comes later).
    pub(super) fn asked(&mut self, id: &str, asked: &str, spoken: bool) -> Option<Exchange> {
        self.asked_with(id, asked, spoken, Vec::new())
    }

    /// A request from the chat, with the names of the files he attached to it.
    pub(super) fn asked_with(&mut self, id: &str, asked: &str, spoken: bool, files: Vec<String>) -> Option<Exchange> {
        self.mine.insert(id.to_string());
        self.add(Exchange {
            id: id.into(),
            asked: with_files(asked, &files),
            spoken,
            files,
            ..Exchange::default()
        });
        self.exchanges.last().cloned()
    }

    pub(super) fn add(&mut self, exchange: Exchange) {
        self.exchanges.push(exchange);
        if self.exchanges.len() > MOST {
            self.exchanges.remove(0);
        }
    }

    pub(super) fn find(&mut self, id: &str) -> Option<&mut Exchange> {
        self.exchanges.iter_mut().rev().find(|e| e.id == id)
    }

    /// The exchange for a request; for one made elsewhere that she heard, a new one.
    pub(super) fn claim(&mut self, id: &str) -> Option<&mut Exchange> {
        if self.exchanges.iter().any(|e| e.id == id) {
            return self.find(id);
        }
        let at = self.heard.iter().position(|(heard, ..)| heard == id)?;
        let (_, georgian, english) = self.heard.remove(at);
        self.add(Exchange {
            id: id.into(),
            asked: english,
            heard: georgian,
            spoken: true,
            ..Exchange::default()
        });
        self.exchanges.last_mut()
    }

    /// A piece of her English for what was said to the chat, as it's written.
    pub(super) fn english(&mut self, id: &str, piece: &str) -> Option<Exchange> {
        let exchange = self.find(id).filter(|e| e.spoken && e.heard.is_empty())?;
        exchange.asked.push_str(piece);
        Some(exchange.clone())
    }

    /// What the voice pipeline heard of a recording.
    pub(super) fn heard(&mut self, id: &str, georgian: &str, english: &str) -> Option<Exchange> {
        if let Some(at) = self.answers.iter().position(|(recording, _)| recording == id) {
            let (_, question) = self.answers.remove(at);
            let said = if english.trim().is_empty() { georgian } else { english };
            // Nothing understood counts as no (the brain's is_yes).
            let said = if said.trim().is_empty() { "No" } else { said.trim() };
            return self.answer(&question, said);
        }
        match self.find(id) {
            Some(exchange) => {
                exchange.heard = georgian.into();
                if !english.trim().is_empty() {
                    exchange.asked = with_files(english, &exchange.files);
                }
                Some(exchange.clone())
            }
            None => {
                self.heard.push((id.into(), georgian.into(), english.into()));
                if self.heard.len() > REMEMBERED {
                    self.heard.remove(0);
                }
                None
            }
        }
    }

    /// What she's doing for a request.
    pub(super) fn step(&mut self, id: &str, step: Step) -> Option<Exchange> {
        let exchange = self.claim(id).filter(|e| !e.done)?;
        exchange.step = Some(step);
        Some(exchange.clone())
    }

    /// The app's own icon for the step showing, once it's found.
    pub(super) fn step_image(&mut self, id: &str, label: &str, image: String) -> Option<Exchange> {
        let exchange = self.find(id)?;
        let step = exchange.step.as_mut().filter(|step| step.label == label)?;
        step.image = Some(image);
        Some(exchange.clone())
    }

    /// A piece of her reply, as it's written.
    pub(super) fn piece(&mut self, id: &str, piece: &str) -> Option<Exchange> {
        let exchange = self.claim(id)?;
        exchange.reply.push_str(piece);
        Some(exchange.clone())
    }

    /// Her whole reply (the pieces may have been only part of it).
    pub(super) fn replied(&mut self, id: &str, text: &str) -> Option<Exchange> {
        self.mine.remove(id);
        let exchange = self.claim(id)?;
        if !text.trim().is_empty() {
            exchange.reply = text.into();
        }
        exchange.done = true;
        exchange.step = None;
        Some(exchange.clone())
    }

    pub(super) fn failed(&mut self, id: &str, problem: &str) -> Option<Exchange> {
        self.mine.remove(id);
        let exchange = self.claim(id).filter(|e| !e.done)?;
        exchange.problem = problem.into();
        exchange.done = true;
        exchange.step = None;
        Some(exchange.clone())
    }

    /// Natro asks before something risky: about the chat's latest request still waiting, if any.
    pub(super) fn ask(&mut self, question: &str, text: &str) -> Option<Exchange> {
        let mine = &self.mine;
        let exchange = self
            .exchanges
            .iter_mut()
            .rev()
            .find(|e| !e.done && mine.contains(&e.id))?;
        exchange.questions.push(Question {
            id: question.into(),
            text: text.trim().into(),
            answer: String::new(),
            device: String::new(),
        });
        Some(exchange.clone())
    }

    /// The PC's question still waiting for a yes or no.
    pub(super) fn waiting(&self) -> Option<String> {
        self.exchanges
            .iter()
            .filter(|e| !e.done && e.from.is_empty())
            .flat_map(|e| &e.questions)
            .find(|q| q.answer.is_empty())
            .map(|q| q.id.clone())
    }

    /// The owner's answer, as it shows under the question (device: whose question, "" for the PC's).
    pub(super) fn answer_for(&mut self, device: &str, question: &str, answer: &str) -> Option<Exchange> {
        let mine = |q: &Question| q.id == question && q.device == device;
        let exchange = self
            .exchanges
            .iter_mut()
            .rev()
            .find(|e| e.questions.iter().any(mine))?;
        let asked = exchange.questions.iter_mut().find(|q| mine(q))?;
        asked.answer = answer.into();
        Some(exchange.clone())
    }

    pub(super) fn answer(&mut self, question: &str, answer: &str) -> Option<Exchange> {
        self.answer_for("", question, answer)
    }

    /// An entry of the shared chat, as the brain sends it (server/natro_agent/chats.py). The PC's own
    /// requests show here as they happen, so from those only the pictures are taken; the rest (the
    /// phone's, and the PC's from before this app started) show as the brain has them.
    pub(super) fn entry(&mut self, entry: &Value) -> Option<Exchange> {
        let text = |key: &str| entry[key].as_str().unwrap_or_default().to_string();
        let pictures: Vec<String> = entry["pictures"]
            .as_array()
            .map(|all| all.iter().filter_map(|p| p.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let ours = text("device") == "pc";
        if ours {
            if let Some(exchange) = self.find(&text("request")) {
                exchange.pictures = pictures;
                return Some(exchange.clone());
            }
        }
        let step = Some(text("step")).filter(|s| !s.is_empty()).map(|label| Step {
            label,
            icon: "work".into(),
            image: None,
        });
        let question = &entry["question"];
        let questions = question.is_object().then(|| Question {
            id: question["id"].as_str().unwrap_or_default().into(),
            text: question["text"].as_str().unwrap_or_default().into(),
            answer: question["answer"].as_str().unwrap_or_default().into(),
            device: if ours { String::new() } else { text("device") },
        });
        let exchange = Exchange {
            id: if ours { text("request") } else { text("id") },
            asked: text("asked"),
            heard: text("heard"),
            spoken: entry["spoken"].as_bool().unwrap_or(false),
            reply: text("reply"),
            done: entry["done"].as_bool().unwrap_or(false),
            step,
            questions: questions.into_iter().collect(),
            problem: text("problem"),
            from: if ours { String::new() } else { text("label") },
            pictures,
            queued: text("queued"),
            sent: entry["sent"].as_bool().unwrap_or(false),
            reminder: entry["reminder"].as_bool().unwrap_or(false),
            job: text("job"),
            alert: entry["alert"].as_bool().unwrap_or(false),
            files: Vec::new(),
        };
        match self.exchanges.iter_mut().rev().find(|e| e.id == exchange.id) {
            Some(old) => *old = exchange.clone(),
            None => self.add(exchange.clone()),
        }
        Some(exchange)
    }

    /// The whole shared chat, as the brain has it (after connecting): the phone's anew, the PC's merged.
    pub(super) fn all(&mut self, entries: &[Value]) -> Vec<Exchange> {
        self.exchanges.retain(|e| e.from.is_empty());
        for entry in entries {
            self.entry(entry);
        }
        self.exchanges.clone()
    }

    /// A spoken answer on its way: what was said shows once it's heard.
    pub(super) fn answered_by_voice(&mut self, recording: &str, question: &str) -> Option<Exchange> {
        self.answers.push((recording.into(), question.into()));
        if self.answers.len() > REMEMBERED {
            self.answers.remove(0);
        }
        self.answer(question, "…")
    }

    /// A new chat: the bubbles go. Requests still on their way stay the chat's (and quiet).
    pub(super) fn clear(&mut self) {
        self.exchanges.clear();
        self.heard.clear();
        self.answers.clear();
    }

    /// The connection to Natro is lost: what was still waiting won't get its reply.
    pub(super) fn lost(&mut self) -> Vec<Exchange> {
        self.mine.clear();
        self.exchanges
            .iter_mut()
            .filter(|e| !e.done)
            .map(|e| {
                e.done = true;
                e.step = None;
                if e.reply.trim().is_empty() {
                    e.problem = "Lost the connection to Natro".into();
                }
                e.clone()
            })
            .collect()
    }
}

/// A request as the chat shows it: his words, then the names of the files he attached (as the brain's
/// _respond in server/natro_agent/server.py). No words yet: nothing to show yet.
fn with_files(words: &str, files: &[String]) -> String {
    if words.is_empty() || files.is_empty() {
        return words.to_string();
    }
    format!("{words} ({})", files.join(", "))
}

/// Whether a typed answer means yes; anything unclear is no. The brain's is_yes
/// (server/natro_agent/server.py), with Georgian too.
pub(super) fn is_yes(text: &str) -> bool {
    const YES: [&str; 15] = [
        "yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "correct", "right", "go", "do",
        "კი", "ხო", "ჰო", "დიახ",
    ];
    const NO: [&str; 11] = [
        "no", "nope", "not", "don't", "dont", "cancel", "stop", "wait", "never", "არა", "არ",
    ];
    let lower = text.to_lowercase();
    let words: Vec<&str> = lower
        .split(|c: char| !(c.is_alphanumeric() || c == '\''))
        .filter(|w| !w.is_empty())
        .collect();
    // "OK Natro, no" must not count as yes because of the "OK".
    let words = match words.as_slice() {
        ["ok" | "okay", "natro", rest @ ..] => rest,
        all => all,
    };
    !words.iter().any(|w| NO.contains(w)) && words.iter().any(|w| YES.contains(w))
}

/// A request he typed, kept for Up and Down: last, unless it's the one before again; the oldest go past
/// MOST_TYPED. False if nothing changed.
pub(super) fn remember(typed: &mut Vec<String>, text: &str) -> bool {
    if typed.last().is_some_and(|last| last == text) {
        return false;
    }
    typed.push(text.to_string());
    if typed.len() > MOST_TYPED {
        typed.drain(..typed.len() - MOST_TYPED);
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn step(label: &str) -> Step {
        Step {
            label: label.into(),
            icon: "app".into(),
            image: None,
        }
    }

    #[test]
    fn files_he_attached_show_after_his_words() {
        let mut log = Log::default();
        let files = vec!["report.pdf".to_string(), "b.pdf".to_string()];
        let e = log.asked_with("r1", "merge these", false, files.clone()).unwrap();
        assert_eq!(e.asked, "merge these (report.pdf, b.pdf)");
        // Spoken: nothing shows until she has his words.
        let e = log.asked_with("r2", "", true, files).unwrap();
        assert_eq!(e.asked, "");
        let e = log.heard("r2", "გააერთიანე", "Merge these.").unwrap();
        assert_eq!(e.asked, "Merge these. (report.pdf, b.pdf)");
    }

    #[test]
    fn a_typed_request_is_the_chats_until_its_reply() {
        let mut log = Log::default();
        log.asked("r1", "open Spotify", false);
        assert!(log.mine.contains("r1"));
        assert!(log.step("r1", step("Opening Spotify")).is_some());
        log.piece("r1", "Opened ");
        let e = log.piece("r1", "Spotify.").unwrap();
        assert_eq!(e.reply, "Opened Spotify.");
        assert!(!e.done);
        let e = log.replied("r1", "Opened Spotify for you.").unwrap();
        assert_eq!((e.reply.as_str(), e.done, e.step), ("Opened Spotify for you.", true, None));
        assert!(!log.mine.contains("r1"));
    }

    #[test]
    fn what_was_said_to_the_chat_fills_in_as_she_translates_it() {
        let mut log = Log::default();
        log.asked("r2", "", true);
        log.english("r2", "what's the ");
        assert_eq!(log.english("r2", "weather").unwrap().asked, "what's the weather");
        let e = log.heard("r2", "როგორი ამინდია", "What's the weather like?").unwrap();
        assert_eq!((e.asked.as_str(), e.heard.as_str()), ("What's the weather like?", "როგორი ამინდია"));
        // Once heard, a late piece doesn't add to it.
        assert!(log.english("r2", "!").is_none());
    }

    #[test]
    fn a_request_to_the_dock_shows_once_she_works_on_it() {
        let mut log = Log::default();
        assert!(log.english("r3", "pause").is_none());
        assert!(log.heard("r3", "დააპაუზე", "Pause the music").is_none());
        assert!(log.exchanges.is_empty());
        let e = log.step("r3", step("Pausing")).unwrap();
        assert_eq!((e.asked.as_str(), e.spoken), ("Pause the music", true));
        log.replied("r3", "Paused.");
        assert_eq!(log.exchanges.len(), 1);
        assert!(!log.mine.contains("r3"));
        // A spoken yes or no to the dock's question is heard but never gets a reply: no bubble.
        log.heard("r4", "კი", "Yes");
        assert_eq!(log.exchanges.len(), 1);
    }

    #[test]
    fn questions_go_to_the_chats_request_still_waiting_and_take_any_answer() {
        let mut log = Log::default();
        assert!(log.ask("c1", "I'll force close Spotify.").is_none());
        log.asked("r5", "force close Spotify", false);
        let e = log.ask("c1", " I'll force close Spotify. ").unwrap();
        assert_eq!(e.questions[0].text, "I'll force close Spotify.");
        assert_eq!(log.waiting().as_deref(), Some("c1"));
        log.answer("c1", "Yes");
        assert_eq!(log.waiting(), None);
        // Said to the microphone: "…" until it's heard.
        log.ask("c2", "And Chrome?");
        assert_eq!(log.answered_by_voice("r6", "c2").unwrap().questions[1].answer, "…");
        let e = log.heard("r6", "არა", "No").unwrap();
        assert_eq!(e.questions[1].answer, "No");
        assert_eq!(log.exchanges.len(), 1);
    }

    #[test]
    fn a_new_chat_drops_the_bubbles_but_keeps_requests_on_their_way_quiet() {
        let mut log = Log::default();
        log.asked("r7", "what time is it", false);
        log.clear();
        assert!(log.exchanges.is_empty());
        assert!(log.mine.contains("r7"));
        assert!(log.piece("r7", "It's noon.").is_none());
        assert!(log.replied("r7", "It's noon.").is_none());
        assert!(!log.mine.contains("r7"));
    }

    #[test]
    fn a_lost_connection_ends_what_was_waiting() {
        let mut log = Log::default();
        log.asked("r8", "hi", false);
        log.replied("r8", "Hi!");
        log.asked("r9", "open Chrome", false);
        let ended = log.lost();
        assert_eq!(ended.len(), 1);
        assert_eq!(ended[0].problem, "Lost the connection to Natro");
        assert!(log.failed("r9", "late").is_none());
    }

    fn entry(device: &str, request: &str, reply: &str, done: bool) -> Value {
        json!({"id": format!("{device}:{request}"), "device": device, "label": device, "request": request,
               "asked": "what's on my calendar", "heard": "", "spoken": false, "reply": reply, "step": "",
               "done": done, "problem": "", "pictures": [], "question": null, "queued": null, "sent": false})
    }

    #[test]
    fn the_phones_requests_show_as_the_brain_has_them() {
        let mut log = Log::default();
        let e = log.entry(&entry("phone", "r9", "", false)).unwrap();
        assert_eq!((e.id.as_str(), e.from.as_str(), e.done), ("phone:r9", "phone", false));
        let e = log.entry(&entry("phone", "r9", "Nothing today.", true)).unwrap();
        assert_eq!((e.reply.as_str(), e.done), ("Nothing today.", true));
        assert_eq!(log.exchanges.len(), 1);
        // Its question can't take what's typed here: that answers only the PC's own.
        let mut asking = entry("phone", "r10", "", false);
        asking["question"] = json!({"id": "c1", "text": "Text Nino?", "answer": ""});
        let e = log.entry(&asking).unwrap();
        assert_eq!(e.questions[0].device, "phone");
        assert_eq!(log.waiting(), None);
        assert_eq!(log.answer_for("phone", "c1", "Yes").unwrap().questions[0].answer, "Yes");
    }

    #[test]
    fn the_pcs_own_requests_take_only_their_pictures_from_the_brain() {
        let mut log = Log::default();
        log.asked("r1", "show me my phone screen", false);
        log.piece("r1", "Here");
        let mut shared = entry("pc", "r1", "Here it is.", true);
        shared["pictures"] = json!(["pic1"]);
        let e = log.entry(&shared).unwrap();
        assert_eq!((e.reply.as_str(), e.done, e.pictures.clone()), ("Here", false, vec!["pic1".to_string()]));
        assert!(log.mine.contains("r1"));
        // One from before the app started shows whole.
        let e = log.entry(&entry("pc", "r0", "Done.", true)).unwrap();
        assert_eq!((e.id.as_str(), e.from.as_str(), e.reply.as_str()), ("r0", "", "Done."));
    }

    #[test]
    fn after_connecting_the_phones_side_is_replaced_and_the_pcs_kept() {
        let mut log = Log::default();
        log.asked("r1", "hi", false);
        log.entry(&entry("phone", "r5", "Old.", true));
        let all = log.all(&[entry("phone", "r6", "New.", true)]);
        let ids: Vec<&str> = all.iter().map(|e| e.id.as_str()).collect();
        assert_eq!(ids, ["r1", "phone:r6"]);
    }

    #[test]
    fn a_reminder_is_her_words_alone() {
        let mut log = Log::default();
        let mut reminder = entry("natro", "rm1-17", "Time to call Nino.", true);
        reminder["asked"] = json!("");
        reminder["reminder"] = json!(true);
        let e = log.entry(&reminder).unwrap();
        assert!(e.reminder && e.asked.is_empty() && e.done);
        assert_eq!((e.id.as_str(), e.reply.as_str()), ("natro:rm1-17", "Time to call Nino."));
        assert!(!log.entry(&entry("phone", "r1", "Done.", true)).unwrap().reminder);
        let mut alert = entry("natro", "al1", "Nino emailed: Dinner.", true);
        alert["asked"] = json!("");
        alert["alert"] = json!(true);
        let e = log.entry(&alert).unwrap();
        assert!(e.alert && !e.reminder && e.asked.is_empty());
    }

    #[test]
    fn a_background_job_is_her_work_with_its_task() {
        let mut log = Log::default();
        let mut job = entry("natro", "jb1", "", false);
        job["asked"] = json!("");
        job["job"] = json!("Find three flats under $600.");
        job["question"] = json!({"id": "c4", "text": "I'll email Nino the flats.", "answer": "", "device": "pc"});
        let e = log.entry(&job).unwrap();
        assert_eq!(e.job, "Find three flats under $600.");
        assert!(e.asked.is_empty() && !e.done && !e.reminder);
        // Answered here, it goes to the brain as the device "natro"'s: the brain knows where it was asked.
        assert_eq!(e.questions[0].device, "natro");
    }

    #[test]
    fn typed_answers() {
        for yes in ["yes", "Yes please", "ok", "OK, do it", "კი", "ხო", "sure!"] {
            assert!(is_yes(yes), "{yes}");
        }
        for no in ["no", "don't", "ok natro, no", "not now", "არა", "hmm", "", "yes, no, wait"] {
            assert!(!is_yes(no), "{no}");
        }
    }

    #[test]
    fn up_and_down_keep_the_newest_requests_once_each() {
        let mut typed = Vec::new();
        assert!(remember(&mut typed, "open Spotify"));
        assert!(!remember(&mut typed, "open Spotify")); // the same again: once
        assert!(remember(&mut typed, "what's the weather"));
        assert!(remember(&mut typed, "open Spotify")); // not right after itself: again
        assert_eq!(typed, ["open Spotify", "what's the weather", "open Spotify"]);
        for n in 0..MOST_TYPED {
            remember(&mut typed, &format!("request {n}"));
        }
        assert_eq!(typed.len(), MOST_TYPED);
        assert_eq!(typed[0], "request 0");
        assert_eq!(typed.last().unwrap(), &format!("request {}", MOST_TYPED - 1));
    }
}
