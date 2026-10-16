//! Recordings: 16 kHz mono 16-bit, what the brain's voice pipeline takes, and
//! deciding when the speaker has finished.
//!
//! The same rules as the Python PC app (pc/natro_pc/audio.py), which match the
//! brain's (voice/natro_voice/speech.py): a recording judged to have speech here
//! is judged the same way there.

pub const SAMPLE_RATE: u32 = 16_000;
/// Loudness is judged in 100 ms chunks.
pub const CHUNK: usize = SAMPLE_RATE as usize / 10;
/// Recordings shorter than this are ignored (an accidental tap).
pub const MIN_SAMPLES: usize = SAMPLE_RATE as usize / 2;

/// Mean absolute sample value.
pub fn mean_loudness(samples: &[i16]) -> f64 {
    samples.iter().map(|&s| (s as f64).abs()).sum::<f64>() / samples.len().max(1) as f64
}

/// Loudness above which audio counts as speech. Measured: a quiet room up to
/// ~180, speech ~1,000-16,000; in a noisy recording the background was ~800.
pub fn speech_threshold(noise_floor: f64) -> f64 {
    (6.0 * noise_floor).max(300.0)
}

/// Whether the recording has at least 0.3 s clearly louder than its background
/// noise. Recordings without speech are never sent (silence costs money, and
/// the recognizer can make up words from noise).
pub fn has_speech(pcm: &[i16]) -> bool {
    let frame = SAMPLE_RATE as usize / 50;
    let mut loudness: Vec<f64> = pcm.chunks_exact(frame).map(mean_loudness).collect();
    if loudness.is_empty() {
        return false;
    }
    loudness.sort_by(f64::total_cmp);
    let threshold = speech_threshold(loudness[loudness.len() / 10]);
    loudness.iter().filter(|&&level| level > threshold).count() >= 15
}

/// Decides from each chunk's loudness when the speaker has finished: after
/// `silence` chunks of quiet following speech, or `no_speech` chunks without any.
pub struct EndOfSpeech {
    silence: usize,
    no_speech: usize,
    chunks: usize,
    first_speech: Option<usize>,
    last_speech: Option<usize>,
    quietest: Option<f64>,
}

impl EndOfSpeech {
    pub fn new(silence_seconds: f64, no_speech_seconds: f64) -> EndOfSpeech {
        EndOfSpeech {
            silence: (silence_seconds * 10.0).round() as usize,
            no_speech: (no_speech_seconds * 10.0).round() as usize,
            chunks: 0,
            first_speech: None,
            last_speech: None,
            quietest: None,
        }
    }

    pub fn add(&mut self, loudness: f64) {
        // The quietest chunk so far stands in for the background noise.
        let quietest = self.quietest.map_or(loudness, |q| q.min(loudness));
        self.quietest = Some(quietest);
        if loudness > speech_threshold(quietest) {
            self.first_speech.get_or_insert(self.chunks);
            self.last_speech = Some(self.chunks);
        }
        self.chunks += 1;
    }

    pub fn done(&self) -> bool {
        match self.last_speech {
            None => self.chunks >= self.no_speech,
            Some(last) => self.chunks - 1 - last >= self.silence,
        }
    }

    /// The recording without the silence before and after the speech, keeping a little margin.
    pub fn trim<'a>(&self, pcm: &'a [i16]) -> &'a [i16] {
        let (Some(first), Some(last)) = (self.first_speech, self.last_speech) else {
            return pcm;
        };
        let start = (first.saturating_sub(3) * CHUNK).min(pcm.len());
        let end = ((last + 1 + 5) * CHUNK).min(pcm.len());
        &pcm[start..end]
    }
}

/// Turns the microphone's audio (any rate, already mono) into 16 kHz, a buffer at a time.
pub struct Resampler {
    /// Input samples per output sample.
    ratio: f64,
    pending: Vec<f32>,
    /// Where the next output sample falls in `pending`.
    at: f64,
}

impl Resampler {
    pub fn new(input_rate: u32) -> Resampler {
        Resampler {
            ratio: input_rate as f64 / SAMPLE_RATE as f64,
            pending: Vec::new(),
            at: 0.0,
        }
    }

    pub fn push(&mut self, input: &[f32], out: &mut Vec<i16>) {
        self.pending.extend_from_slice(input);
        // Each output sample averages the input it stands for (a simple low-pass,
        // so higher frequencies don't fold into speech), else is interpolated.
        let span = self.ratio.max(1.0);
        while self.at + span <= self.pending.len() as f64 {
            let sample = if self.ratio >= 2.0 {
                let (from, to) = (self.at as usize, (self.at + span) as usize);
                self.pending[from..to].iter().sum::<f32>() / (to - from) as f32
            } else {
                let i = self.at as usize;
                let next = self.pending.get(i + 1).copied().unwrap_or(self.pending[i]);
                self.pending[i] + (next - self.pending[i]) * (self.at - i as f64) as f32
            };
            out.push((sample.clamp(-1.0, 1.0) * 32767.0) as i16);
            self.at += self.ratio;
        }
        let used = (self.at as usize).min(self.pending.len());
        self.pending.drain(..used);
        self.at -= used as f64;
    }
}

/// A 16 kHz mono 16-bit WAV file's samples (for testing with recorded clips).
pub fn read_wav(bytes: &[u8]) -> Option<Vec<i16>> {
    let mut at = 12;
    let mut format_ok = false;
    while at + 8 <= bytes.len() {
        let id = &bytes[at..at + 4];
        let size = u32::from_le_bytes(bytes[at + 4..at + 8].try_into().ok()?) as usize;
        let body = bytes.get(at + 8..(at + 8 + size).min(bytes.len()))?;
        if id == b"fmt " && body.len() >= 16 {
            let channels = u16::from_le_bytes([body[2], body[3]]);
            let rate = u32::from_le_bytes(body[4..8].try_into().ok()?);
            let bits = u16::from_le_bytes([body[14], body[15]]);
            format_ok = (channels, rate, bits) == (1, SAMPLE_RATE, 16);
        } else if id == b"data" {
            return format_ok.then(|| {
                body.chunks_exact(2)
                    .map(|b| i16::from_le_bytes([b[0], b[1]]))
                    .collect()
            });
        }
        at += 8 + size + size % 2;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(samples: usize, amplitude: f64) -> Vec<i16> {
        (0..samples)
            .map(|i| ((i as f64 * 0.3).sin() * amplitude) as i16)
            .collect()
    }

    #[test]
    fn silence_has_no_speech_and_a_voice_does() {
        assert!(!has_speech(&vec![0; 16_000]));
        let mut pcm = tone(16_000, 50.0);
        pcm.extend(tone(8_000, 8_000.0));
        assert!(has_speech(&pcm));
    }

    #[test]
    fn ends_after_silence_following_speech() {
        let mut end = EndOfSpeech::new(3.0, 8.0);
        for _ in 0..5 {
            end.add(100.0);
        }
        for _ in 0..10 {
            end.add(5_000.0);
        }
        for _ in 0..29 {
            end.add(100.0);
            assert!(!end.done());
        }
        end.add(100.0);
        assert!(end.done());
        // Trimmed to 3 chunks before the speech and 5 after.
        let pcm = vec![0i16; 45 * CHUNK];
        assert_eq!(end.trim(&pcm).len(), (3 + 10 + 5) * CHUNK);
    }

    #[test]
    fn ends_when_nothing_is_said() {
        let mut end = EndOfSpeech::new(3.0, 8.0);
        for _ in 0..79 {
            end.add(100.0);
        }
        assert!(!end.done());
        end.add(100.0);
        assert!(end.done());
    }

    #[test]
    fn resamples_48k_to_16k_in_pieces() {
        let mut resampler = Resampler::new(48_000);
        let mut out = Vec::new();
        let input = vec![0.5f32; 4_800];
        for piece in input.chunks(333) {
            resampler.push(piece, &mut out);
        }
        assert_eq!(out.len(), 1_600);
        assert!(out.iter().all(|&s| s == 16_383));
    }

    #[test]
    fn reads_a_wav_file() {
        let mut wav = b"RIFF\0\0\0\0WAVEfmt ".to_vec();
        wav.extend(16u32.to_le_bytes());
        wav.extend([1, 0, 1, 0]);
        wav.extend(16_000u32.to_le_bytes());
        wav.extend(32_000u32.to_le_bytes());
        wav.extend([2, 0, 16, 0]);
        wav.extend(b"data");
        wav.extend(4u32.to_le_bytes());
        wav.extend([1, 0, 255, 255]);
        assert_eq!(read_wav(&wav), Some(vec![1, -1]));
    }
}
