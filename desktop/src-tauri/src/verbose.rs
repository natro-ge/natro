//! The "Verbose" switch (the tray, Settings): while it's on, every request is kept for later testing.
//!
//! Each request leaves two files in `logs/verbose/` (git-ignored, like all logs): the
//! recording (`<time>_<id>.wav`, 16 kHz mono, as it was sent) and `<time>_<id>.json`
//! with everything that happened to it: what Natro heard (Georgian and English),
//! each step, each PC tool that ran, her questions and the answers, the reply with
//! its model and cost, and any error, each with the milliseconds since the request
//! was sent. The JSON is rewritten at every event, so a request that never finishes
//! is still there. Typed requests (no recording) are kept the same way.
//!
//! settings.rs remembers whether it's on.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

/// Unfinished requests kept in memory (one is normal; this only bounds a pile-up).
const MAX_OPEN: usize = 20;

struct Entry {
    started: Instant,
    path: PathBuf,
    record: Value,
}

pub struct Verbose {
    on: AtomicBool,
    dir: PathBuf,
    open: Mutex<Open>,
}

#[derive(Default)]
struct Open {
    entries: HashMap<String, Entry>,
    /// The latest request not yet finished: the brain's questions and tool calls
    /// carry their own ids (q1, c1), and belong to it.
    active: Option<String>,
}

impl Verbose {
    pub fn new(home: &std::path::Path, on: bool) -> Verbose {
        Verbose {
            on: AtomicBool::new(on),
            dir: home.join("logs").join("verbose"),
            open: Mutex::default(),
        }
    }

    pub fn is_on(&self) -> bool {
        self.on.load(Ordering::SeqCst)
    }

    pub fn set(&self, on: bool) {
        self.on.store(on, Ordering::SeqCst);
        log::info!("verbose {}", if on { "on" } else { "off" });
    }

    /// A request was sent: its recording (if spoken) is saved, and its record started.
    /// `answer_to` is the question a spoken yes or no answers.
    pub fn sent(&self, id: &str, pcm: Option<&[i16]>, text: Option<&str>, answer_to: Option<&str>) {
        if !self.is_on() {
            return;
        }
        let name = format!("{}_{id}", timestamp());
        let mut record = json!({"id": id, "time": timestamp(), "kind": if pcm.is_some() { "audio" } else { "text" }});
        if let Some(text) = text {
            record["typed"] = json!(text);
        }
        if let Some(question) = answer_to {
            record["answer_to"] = json!(question);
        }
        if let Some(pcm) = pcm {
            let clip = format!("{name}.wav");
            match fs::create_dir_all(&self.dir).and_then(|_| fs::write(self.dir.join(&clip), wav(pcm))) {
                Ok(()) => {
                    record["clip"] = json!(clip);
                    record["seconds"] = json!(pcm.len() as f64 / 16_000.0);
                }
                Err(e) => log::warn!("couldn't save the recording: {e}"),
            }
        }
        record["events"] = json!([]);
        let mut open = self.open.lock().unwrap();
        if open.entries.len() >= MAX_OPEN {
            open.entries.clear();
        }
        let entry = Entry {
            started: Instant::now(),
            path: self.dir.join(format!("{name}.json")),
            record,
        };
        write(&entry);
        open.entries.insert(id.to_string(), entry);
        open.active = Some(id.to_string());
    }

    /// Something happened to a request. `id` is the brain's (a question's or tool call's id
    /// belongs to the request being handled). `kind` is "heard", "step", "tool", "asked",
    /// "answered", "reply" or "error"; the first and last two also fill the record's top.
    pub fn event(&self, id: &str, kind: &str, data: Value) {
        if !self.is_on() {
            return;
        }
        let mut open = self.open.lock().unwrap();
        let key = if open.entries.contains_key(id) {
            Some(id.to_string())
        } else {
            open.active.clone()
        };
        let Some(key) = key else { return };
        let Some(entry) = open.entries.get_mut(&key) else {
            return;
        };
        let ms = entry.started.elapsed().as_millis() as u64;
        let mut event = json!({"ms": ms, "kind": kind});
        if let (Some(object), Some(extra)) = (event.as_object_mut(), data.as_object()) {
            object.extend(extra.clone());
        }
        if matches!(kind, "heard" | "reply" | "error") {
            entry.record[kind] = data;
            entry.record["total_ms"] = json!(ms);
        }
        entry.record["events"].as_array_mut().unwrap().push(event);
        write(entry);
        if matches!(kind, "reply" | "error") {
            open.entries.remove(&key);
            if open.active.as_deref() == Some(&key) {
                open.active = None;
            }
        }
    }
}

fn write(entry: &Entry) {
    if let Err(e) = fs::write(&entry.path, serde_json::to_vec_pretty(&entry.record).unwrap_or_default()) {
        log::warn!("couldn't write the verbose log: {e}");
    }
}

/// 16 kHz mono 16-bit WAV, like the test clips.
fn wav(pcm: &[i16]) -> Vec<u8> {
    let data = (pcm.len() * 2) as u32;
    let mut out = Vec::with_capacity(44 + data as usize);
    out.extend(b"RIFF");
    out.extend((36 + data).to_le_bytes());
    out.extend(b"WAVEfmt ");
    out.extend(16u32.to_le_bytes());
    out.extend(1u16.to_le_bytes()); // PCM
    out.extend(1u16.to_le_bytes()); // mono
    out.extend(16_000u32.to_le_bytes());
    out.extend(32_000u32.to_le_bytes());
    out.extend(2u16.to_le_bytes());
    out.extend(16u16.to_le_bytes());
    out.extend(b"data");
    out.extend(data.to_le_bytes());
    out.extend(pcm.iter().flat_map(|s| s.to_le_bytes()));
    out
}

/// UTC, sortable: 20261008T153012Z.
fn timestamp() -> String {
    format_time(SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs()))
}

fn format_time(seconds: u64) -> String {
    let (days, rest) = ((seconds / 86_400) as i64, seconds % 86_400);
    // Days since 1970-01-01 to a calendar date (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = z.rem_euclid(146_097);
    let year_of_era = (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 { month_index + 3 } else { month_index - 9 };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    format!("{year:04}{month:02}{day:02}T{:02}{:02}{:02}Z", rest / 3600, rest % 3600 / 60, rest % 60)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A folder of the test's own: tests run at the same time.
    fn temp(test: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("natro-verbose-{}-{test}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn off_keeps_nothing() {
        let home = temp("off_keeps_nothing");
        let verbose = Verbose::new(&home, false);
        verbose.sent("r1", Some(&[1, 2, 3]), None, None);
        verbose.event("r1", "reply", json!({"text": "Hi"}));
        assert!(!home.join("logs").exists());
    }

    #[test]
    fn a_request_is_kept_from_recording_to_reply() {
        let home = temp("a_request_is_kept_from_recording_to_reply");
        let verbose = Verbose::new(&home, false);
        verbose.set(true);
        verbose.sent("r1", Some(&[100, -100, 7]), None, None);
        verbose.event("r1", "heard", json!({"georgian": "გამარჯობა", "english": "hello"}));
        verbose.event("r1", "step", json!({"text": "Opening Spotify"}));
        verbose.event("q1", "asked", json!({"question": "I'll force close Spotify."})); // a question's own id
        verbose.event("r1", "reply", json!({"text": "Done.", "model": "gemini", "cost": 0.0}));
        let dir = home.join("logs").join("verbose");
        let mut files: Vec<_> = fs::read_dir(&dir).unwrap().map(|e| e.unwrap().path()).collect();
        files.sort();
        assert_eq!(files.len(), 2);
        let clip = fs::read(&files[1]).unwrap();
        assert_eq!(&clip[..4], b"RIFF");
        assert_eq!(clip.len(), 44 + 6);
        let record: Value = serde_json::from_slice(&fs::read(&files[0]).unwrap()).unwrap();
        assert_eq!(record["heard"]["english"], "hello");
        assert_eq!(record["reply"]["text"], "Done.");
        assert_eq!(record["events"].as_array().unwrap().len(), 4);
        assert_eq!(record["events"][2]["question"], "I'll force close Spotify.");
        verbose.set(false);
        verbose.sent("r2", None, Some("hi"), None);
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 2, "off, nothing more is kept");
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn dates_are_right() {
        assert_eq!(format_time(1_791_473_412), "20261008T153012Z");
        assert_eq!(format_time(1_709_164_801), "20240229T000001Z");
    }
}
