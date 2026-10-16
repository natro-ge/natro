//! The microphone while the dock or the chat listens: the recording (16 kHz mono
//! 16-bit), its loudness for their green dot, and, hands-free, noticing when the
//! speaker has finished.
//!
//! Debug builds can take a recorded clip instead of the microphone:
//! NATRO_TEST_CLIP=testset/clips/clip010.wav (16 kHz mono WAV, relative to
//! NATRO_HOME), played in real time and followed by silence.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use tauri::{AppHandle, Emitter};

use crate::audio::{CHUNK, EndOfSpeech, Resampler, mean_loudness, read_wav};

/// Silence that ends a hands-free recording. In test memos, thinking pauses
/// inside one request lasted up to 3.9 s: 2 s would have cut in 12 times, 3 s 3 times.
const SILENCE_SECONDS: f64 = 3.0;
/// Hands-free, with no speech at all for this long, it stops.
const NO_SPEECH_SECONDS: f64 = 8.0;
const MAX_SECONDS: usize = 120;
/// How often the loudness goes to the page (about 30 times a second).
const LEVEL_EVERY: Duration = Duration::from_millis(33);

pub struct Recorder {
    stop: mpsc::Sender<()>,
    recording: mpsc::Receiver<Vec<i16>>,
    hands_free: Arc<AtomicBool>,
}

impl Recorder {
    /// The loudness goes to the window `page` ("dock", "chat") as "level" events.
    /// `finished` is called (once, from another thread) when a hands-free
    /// recording has heard the speaker finish.
    pub fn start(
        app: AppHandle,
        test_clip: Option<PathBuf>,
        page: &'static str,
        finished: impl FnOnce() + Send + 'static,
    ) -> Recorder {
        let (stop, stopped) = mpsc::channel();
        let (send_recording, recording) = mpsc::channel();
        let hands_free = Arc::new(AtomicBool::new(false));
        let flag = hands_free.clone();
        thread::spawn(move || {
            let mut take = Take {
                app: Some(app),
                page,
                hands_free: flag,
                finished: Some(Box::new(finished)),
                ..Take::default()
            };
            let result = match test_clip {
                Some(path) => take.play_clip(&path, &stopped),
                None => take.record_microphone(&stopped),
            };
            if let Err(e) = result {
                log::warn!("recording: {e}");
            }
            let pcm = if take.hands_free.load(Ordering::SeqCst) {
                take.end.trim(&take.pcm).to_vec()
            } else {
                take.pcm
            };
            let _ = send_recording.send(pcm);
        });
        Recorder {
            stop,
            recording,
            hands_free,
        }
    }

    /// From now on, stop by itself once the speaker has finished.
    pub fn hands_free(&self) {
        self.hands_free.store(true, Ordering::SeqCst);
    }

    /// Stop, and the recording (hands-free, without the silence around the speech).
    pub fn stop(self) -> Vec<i16> {
        let _ = self.stop.send(());
        self.recording
            .recv_timeout(Duration::from_secs(3))
            .unwrap_or_default()
    }
}

impl Drop for Recorder {
    /// Dropped without stop(): the microphone closes and the recording is thrown away.
    fn drop(&mut self) {
        let _ = self.stop.send(());
    }
}

/// What the recording thread keeps.
struct Take {
    app: Option<AppHandle>,
    /// Where the loudness goes.
    page: &'static str,
    hands_free: Arc<AtomicBool>,
    finished: Option<Box<dyn FnOnce() + Send>>,
    pcm: Vec<i16>,
    /// How much of `pcm` has been judged, in whole chunks.
    judged: usize,
    end: EndOfSpeech,
    /// Sum of squares and count, for the next level.
    energy: (f64, usize),
    level_sent: Option<Instant>,
}

impl Default for Take {
    fn default() -> Take {
        Take {
            app: None,
            page: "dock",
            hands_free: Arc::default(),
            finished: None,
            pcm: Vec::new(),
            judged: 0,
            end: EndOfSpeech::new(SILENCE_SECONDS, NO_SPEECH_SECONDS),
            energy: (0.0, 0),
            level_sent: None,
        }
    }
}

impl Take {
    fn record_microphone(&mut self, stopped: &mpsc::Receiver<()>) -> Result<(), String> {
        let device = cpal::default_host()
            .default_input_device()
            .ok_or("no microphone")?;
        let supported = device.default_input_config().map_err(|e| e.to_string())?;
        let config = supported.config();
        let channels = config.channels as usize;
        let mut resampler = Resampler::new(config.sample_rate);
        // The stream's callback hands mono samples over; this thread does the rest.
        let incoming: Arc<Mutex<Vec<f32>>> = Arc::default();
        let stream = match supported.sample_format() {
            cpal::SampleFormat::F32 => {
                build(&device, config, channels, incoming.clone(), |s: f32| s)
            }
            cpal::SampleFormat::I16 => {
                build(&device, config, channels, incoming.clone(), |s: i16| {
                    s as f32 / 32768.0
                })
            }
            cpal::SampleFormat::U16 => {
                build(&device, config, channels, incoming.clone(), |s: u16| {
                    (s as f32 - 32768.0) / 32768.0
                })
            }
            other => return Err(format!("unsupported sample format {other}")),
        }?;
        stream.play().map_err(|e| e.to_string())?;
        let mut mono = Vec::new();
        while let Err(mpsc::RecvTimeoutError::Timeout) =
            stopped.recv_timeout(Duration::from_millis(20))
        {
            std::mem::swap(&mut mono, &mut *incoming.lock().unwrap());
            for &s in &mono {
                self.energy.0 += (s as f64).powi(2);
                self.energy.1 += 1;
            }
            resampler.push(&mono, &mut self.pcm);
            mono.clear();
            self.judge();
        }
        Ok(())
    }

    fn play_clip(&mut self, path: &PathBuf, stopped: &mpsc::Receiver<()>) -> Result<(), String> {
        let bytes = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let clip = read_wav(&bytes).ok_or("the test clip isn't a 16 kHz mono WAV file")?;
        let mut played = 0;
        while let Err(mpsc::RecvTimeoutError::Timeout) =
            stopped.recv_timeout(Duration::from_millis(100))
        {
            let chunk: Vec<i16> = clip.iter().copied().skip(played).take(CHUNK).collect();
            played += CHUNK;
            let silence = vec![0; CHUNK - chunk.len()];
            for &s in chunk.iter().chain(&silence) {
                self.energy.0 += (s as f64 / 32768.0).powi(2);
                self.energy.1 += 1;
                self.pcm.push(s);
            }
            self.judge();
        }
        Ok(())
    }

    /// The level for the page, and, hands-free, whether the speaker has finished.
    fn judge(&mut self) {
        if self
            .level_sent
            .is_none_or(|sent| sent.elapsed() >= LEVEL_EVERY)
            && self.energy.1 > 0
        {
            let rms = (self.energy.0 / self.energy.1 as f64).sqrt() as f32;
            if let Some(app) = &self.app {
                let _ = app.emit_to(self.page, "level", rms);
            }
            self.energy = (0.0, 0);
            self.level_sent = Some(Instant::now());
        }
        while (self.judged + 1) * CHUNK <= self.pcm.len() {
            self.end.add(mean_loudness(
                &self.pcm[self.judged * CHUNK..(self.judged + 1) * CHUNK],
            ));
            self.judged += 1;
        }
        let too_long = self.judged >= MAX_SECONDS * 10;
        let ended = (self.hands_free.load(Ordering::SeqCst) && self.end.done()) || too_long;
        if ended && let Some(finished) = self.finished.take() {
            finished();
        }
    }
}

fn build<T: cpal::SizedSample + Send + 'static>(
    device: &cpal::Device,
    config: cpal::StreamConfig,
    channels: usize,
    incoming: Arc<Mutex<Vec<f32>>>,
    to_f32: fn(T) -> f32,
) -> Result<cpal::Stream, String> {
    device
        .build_input_stream(
            config,
            move |samples: &[T], _: &cpal::InputCallbackInfo| {
                let mut incoming = incoming.lock().unwrap();
                for frame in samples.chunks_exact(channels) {
                    incoming.push(frame.iter().map(|&s| to_f32(s)).sum::<f32>() / channels as f32);
                }
            },
            |e| log::debug!("microphone stream: {e}"),
            None,
        )
        .map_err(|e| e.to_string())
}
