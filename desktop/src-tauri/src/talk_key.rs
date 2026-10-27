//! The talk key's presses and releases as what to do with the recording.
//!
//! Hold it and talk, release to send; or tap it and talk hands-free (recording
//! stops when you stop talking), tap again to stop sooner. The same rules as
//! the Python tray app's `PressAndHold` (desktop/helper/natro_pc/hotkey.py).

use std::time::{Duration, Instant};

/// A press shorter than this is a tap (hands-free); longer, the recording ends on release.
pub const TAP: Duration = Duration::from_millis(400);

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum TalkEvent {
    /// Start listening.
    Start,
    /// It was a tap: keep listening until the owner stops talking (or taps again).
    HandsFree,
    /// It was a hold, now released: send what was said.
    Send,
    /// A tap while listening hands-free: send now.
    Stop,
}

#[derive(Default)]
pub struct PressAndHold {
    down_at: Option<Instant>,
    hands_free: bool,
    ignore_release: bool,
}

impl PressAndHold {
    /// Key repeats (a press while already down) give None.
    pub fn press(&mut self, now: Instant) -> Option<TalkEvent> {
        if self.down_at.is_some() {
            return None;
        }
        self.down_at = Some(now);
        if self.hands_free {
            self.hands_free = false;
            self.ignore_release = true;
            return Some(TalkEvent::Stop);
        }
        Some(TalkEvent::Start)
    }

    /// The release after a Stop gives None.
    pub fn release(&mut self, now: Instant) -> Option<TalkEvent> {
        let held = now - self.down_at.take()?;
        if std::mem::take(&mut self.ignore_release) {
            return None;
        }
        if held < TAP {
            self.hands_free = true;
            Some(TalkEvent::HandsFree)
        } else {
            Some(TalkEvent::Send)
        }
    }

    /// Listening ended some other way (silence, Esc): the next press starts again.
    pub fn finished(&mut self) {
        self.hands_free = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(start: Instant, ms: u64) -> Instant {
        start + Duration::from_millis(ms)
    }

    #[test]
    fn hold_sends_on_release() {
        let (mut keys, t) = (PressAndHold::default(), Instant::now());
        assert_eq!(keys.press(t), Some(TalkEvent::Start));
        assert_eq!(keys.press(at(t, 30)), None); // key repeat
        assert_eq!(keys.release(at(t, 1500)), Some(TalkEvent::Send));
        assert_eq!(keys.press(at(t, 3000)), Some(TalkEvent::Start));
    }

    #[test]
    fn tap_goes_hands_free_and_the_next_tap_stops() {
        let (mut keys, t) = (PressAndHold::default(), Instant::now());
        assert_eq!(keys.press(t), Some(TalkEvent::Start));
        assert_eq!(keys.release(at(t, 150)), Some(TalkEvent::HandsFree));
        assert_eq!(keys.press(at(t, 4000)), Some(TalkEvent::Stop));
        assert_eq!(keys.release(at(t, 4100)), None);
        assert_eq!(keys.press(at(t, 6000)), Some(TalkEvent::Start));
    }

    #[test]
    fn after_finishing_by_itself_a_press_starts_again() {
        let (mut keys, t) = (PressAndHold::default(), Instant::now());
        keys.press(t);
        keys.release(at(t, 100));
        keys.finished();
        assert_eq!(keys.press(at(t, 5000)), Some(TalkEvent::Start));
    }
}
