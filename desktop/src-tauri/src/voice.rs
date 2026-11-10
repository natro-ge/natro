//! Natro's voice on the PC: Kokoro through sherpa-onnx, a female English voice, free and offline.
//!
//! Model (not in git): desktop/setup-pc.ps1 downloads kokoro-en-v0_19.tar.bz2 from
//! https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models into `models/`. Without it, replies are
//! only shown. NATRO_VOICE picks the speaker: 0 is "af", an American female voice.
//!
//! On the PC it was measured slower than real time (a 3 s sentence took 5 s or more, though with the CPU
//! busy with another program), so each sentence plays as soon as it's ready, while the next one is
//! generated. The int8 version of the model was slower still.
//!
//! The model is read (about 2 s) when Natro starts if "Speak replies" is on, else the first time she
//! speaks. sherpa-onnx's libraries are downloaded when she's built and put beside the program. They're
//! loaded here too, not when Natro starts (build.rs), so without them she only doesn't speak.

use std::cell::OnceCell;
use std::collections::VecDeque;
use std::env;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use sherpa_onnx::{GenerationConfig, OfflineTts, OfflineTtsConfig, OfflineTtsKokoroModelConfig, OfflineTtsModelConfig};
use windows::Win32::System::LibraryLoader::LoadLibraryW;
use windows::core::HSTRING;

use crate::config::Config;

const MODEL: &str = "kokoro-en-v0_19";
/// In the order they need each other.
const LIBRARIES: [&str; 2] = ["onnxruntime.dll", "sherpa-onnx-c-api.dll"];
/// More threads were no faster.
const THREADS: i32 = 2;
/// How long the last sound takes to play once it's been handed over.
const LAST_SOUND: Duration = Duration::from_millis(300);
/// Beyond the length of what's left to play, in case the speakers stopped taking it (unplugged, say).
const MOST_LATE: Duration = Duration::from_secs(2);

type Sound = Arc<Mutex<VecDeque<f32>>>;

struct Saying {
    text: String,
    /// Called once its first sound plays (not at all if it isn't said).
    on_start: Option<Box<dyn FnOnce() + Send>>,
    /// Called when it comes to this in the queue, once what's before it is said (or can't be).
    then: Option<Box<dyn FnOnce() + Send>>,
}

pub struct Voice {
    queue: mpsc::Sender<Saying>,
    /// The tray's "Speak replies".
    pub speaking: AtomicBool,
}

impl Voice {
    pub fn start(config: &Config, speaking: bool) -> Voice {
        let (queue, sayings) = mpsc::channel();
        let model = config.home.join("models").join(MODEL);
        let speaker = config.get("NATRO_VOICE").and_then(|v| v.parse().ok()).unwrap_or(0);
        thread::spawn(move || run(&model, speaker, speaking, sayings));
        Voice {
            queue,
            speaking: AtomicBool::new(speaking),
        }
    }

    /// Speak, after anything already being said (unless "Speak replies" is off).
    pub fn say(&self, text: &str) {
        if self.speaking.load(Ordering::SeqCst) {
            self.say_anyway(text);
        }
    }

    /// Speak a reply (unless "Speak replies" is off); tell gets the seconds from now until its first sound.
    pub fn say_reply(&self, text: &str, tell: impl FnOnce(f64) + Send + 'static) {
        if self.speaking.load(Ordering::SeqCst) {
            let came = Instant::now();
            self.add(text, Some(Box::new(move || tell(came.elapsed().as_secs_f64()))));
        }
    }

    /// Speak even with "Speak replies" off: he asked for it ("say out loud ...").
    pub fn say_anyway(&self, text: &str) {
        self.add(text, None);
    }

    /// Call this (on the voice's thread: it must be quick) once all that's queued has been said; at once if
    /// nothing is, and it reads no model for that.
    pub fn when_quiet(&self, then: impl FnOnce() + Send + 'static) {
        let then = Some(Box::new(then) as Box<dyn FnOnce() + Send>);
        let _ = self.queue.send(Saying { text: String::new(), on_start: None, then });
    }

    fn add(&self, text: &str, on_start: Option<Box<dyn FnOnce() + Send>>) {
        if !text.trim().is_empty() {
            // sherpa-onnx takes C strings.
            let text = text.replace('\0', " ");
            let _ = self.queue.send(Saying { text, on_start, then: None });
        }
    }
}

/// Speaks texts one after another, until Natro stops.
fn run(model: &Path, speaker: i32, read_ahead: bool, sayings: mpsc::Receiver<Saying>) {
    let kokoro = OnceCell::new();
    if read_ahead {
        kokoro.get_or_init(|| load(model));
    }
    for mut saying in sayings {
        let then = saying.then.take();
        if !saying.text.is_empty()
            && let Some(tts) = kokoro.get_or_init(|| load(model))
            && let Err(e) = speak(tts, speaker, saying)
        {
            log::warn!("couldn't speak: {e}");
        }
        if let Some(then) = then {
            then();
        }
    }
}

fn load(model: &Path) -> Option<OfflineTts> {
    if !model.join("model.onnx").exists() {
        log::warn!(
            "no voice: the Kokoro model isn't in {} (desktop/setup-pc.ps1 downloads it). Replies are shown only.",
            model.display()
        );
        return None;
    }
    if let Err(e) = load_libraries() {
        log::warn!("no voice: {e}. Replies are shown only.");
        return None;
    }
    let tts = OfflineTts::create(&kokoro(model));
    if tts.is_none() {
        log::warn!("no voice: Kokoro wouldn't read {}. Replies are shown only.", model.display());
    }
    tts
}

/// sherpa-onnx's libraries, from beside the program (or above it, for a test): by their whole paths, as
/// Windows has an onnxruntime.dll of its own, of another version, and would otherwise give it to a test.
fn load_libraries() -> Result<(), String> {
    let program = env::current_exe().map_err(|e| e.to_string())?;
    for library in LIBRARIES {
        let path = program
            .ancestors()
            .skip(1)
            .take(2)
            .map(|dir| dir.join(library))
            .find(|path| path.exists())
            .ok_or_else(|| format!("{library} isn't beside the program"))?;
        unsafe { LoadLibraryW(&HSTRING::from(path.as_path())) }.map_err(|e| format!("{}: {e}", path.display()))?;
    }
    Ok(())
}

fn kokoro(model: &Path) -> OfflineTtsConfig {
    let file = |name: &str| Some(model.join(name).to_string_lossy().into_owned());
    OfflineTtsConfig {
        model: OfflineTtsModelConfig {
            kokoro: OfflineTtsKokoroModelConfig {
                model: file("model.onnx"),
                voices: file("voices.bin"),
                tokens: file("tokens.txt"),
                data_dir: file("espeak-ng-data"),
                ..Default::default()
            },
            num_threads: THREADS,
            ..Default::default()
        },
        max_num_sentences: 1, // hand over audio one sentence at a time
        ..Default::default()
    }
}

fn speak(tts: &OfflineTts, speaker: i32, saying: Saying) -> Result<(), String> {
    let Saying { text, mut on_start, .. } = saying;
    let rate = tts.sample_rate() as u32;
    let sound: Sound = Arc::default();
    let stream = speakers(rate, sound.clone())?;
    stream.play().map_err(|e| e.to_string())?;
    let generated = sound.clone();
    let generation = GenerationConfig {
        sid: speaker,
        speed: 1.0,
        ..Default::default()
    };
    tts.generate_with_config(
        &text,
        &generation,
        Some(move |samples: &[f32], _progress: f32| {
            generated.lock().unwrap().extend(samples);
            if !samples.is_empty()
                && let Some(on_start) = on_start.take()
            {
                on_start();
            }
            true // keep going
        }),
    )
    .ok_or("Kokoro made no sound")?;
    let left = sound.lock().unwrap().len();
    let latest = Instant::now() + Duration::from_secs_f64(left as f64 / rate as f64) + MOST_LATE;
    while !sound.lock().unwrap().is_empty() && Instant::now() < latest {
        thread::sleep(Duration::from_millis(20));
    }
    thread::sleep(LAST_SOUND);
    Ok(())
}

/// The speakers, playing what comes into sound, and silence until it does. Windows converts Kokoro's
/// rate and its one channel to what they take (cpal asks it to).
fn speakers(rate: u32, sound: Sound) -> Result<cpal::Stream, String> {
    let device = cpal::default_host().default_output_device().ok_or("no speakers")?;
    let config = cpal::StreamConfig {
        channels: 1,
        sample_rate: rate,
        buffer_size: cpal::BufferSize::Default,
    };
    device
        .build_output_stream(
            config,
            move |out: &mut [f32], _: &cpal::OutputCallbackInfo| {
                let mut sound = sound.lock().unwrap();
                for sample in out {
                    *sample = sound.pop_front().unwrap_or(0.0);
                }
            },
            |e| log::debug!("speakers' stream: {e}"),
            None,
        )
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn voice(speaking: bool) -> (Voice, mpsc::Receiver<Saying>) {
        let (queue, sayings) = mpsc::channel();
        let speaking = AtomicBool::new(speaking);
        (Voice { queue, speaking }, sayings)
    }

    fn said(sayings: &mpsc::Receiver<Saying>) -> Vec<String> {
        sayings.try_iter().map(|saying| saying.text).collect()
    }

    #[test]
    fn with_speak_replies_off_only_what_he_asked_for_is_said() {
        let (voice, sayings) = voice(false);
        voice.say("A reminder.");
        voice.say_reply("A reply.", |_| {});
        voice.say_anyway("Dinner's ready.");
        assert_eq!(said(&sayings), ["Dinner's ready."]);
    }

    #[test]
    fn with_speak_replies_on_all_of_it_is_in_order() {
        let (voice, sayings) = voice(true);
        voice.say("A reminder.");
        voice.say_reply("A reply.", |_| {});
        voice.say_anyway("Dinner's ready.");
        assert_eq!(said(&sayings), ["A reminder.", "A reply.", "Dinner's ready."]);
    }

    #[test]
    fn nothing_to_say_isnt_queued_and_a_nul_cant_stop_it() {
        let (voice, sayings) = voice(true);
        voice.say("  \n");
        voice.say_reply("", |_| {});
        voice.say_anyway("Odd\0text");
        assert_eq!(said(&sayings), ["Odd text"]);
    }

    #[test]
    fn a_reply_tells_how_long_its_first_sound_took() {
        let (voice, sayings) = voice(true);
        let told = Arc::new(Mutex::new(None));
        let telling = told.clone();
        voice.say_reply("A reply.", move |seconds| *telling.lock().unwrap() = Some(seconds));
        assert_eq!(*told.lock().unwrap(), None);
        (sayings.recv().unwrap().on_start.unwrap())();
        assert!(told.lock().unwrap().is_some_and(|seconds| seconds >= 0.0));
    }

    #[test]
    fn quiet_comes_after_whats_queued_and_needs_no_voice() {
        let (voice, sayings) = voice(false);
        let order = Arc::new(Mutex::new(Vec::new()));
        for (text, name) in [("Dinner's ready.", "first"), ("", "second")] {
            if !text.is_empty() {
                voice.say_anyway(text);
            }
            let noting = order.clone();
            voice.when_quiet(move || noting.lock().unwrap().push(name));
        }
        drop(voice);
        let queued: Vec<Saying> = sayings.try_iter().collect();
        assert_eq!(queued.iter().map(|saying| saying.text.as_str()).collect::<Vec<_>>(), ["Dinner's ready.", "", ""]);
        // Nothing can speak here (no model), and it still comes.
        let (queue, sayings) = mpsc::channel();
        queued.into_iter().for_each(|saying| queue.send(saying).unwrap());
        drop(queue);
        run(Path::new("no such folder"), 0, false, sayings);
        assert_eq!(*order.lock().unwrap(), ["first", "second"]);
    }

    #[test]
    fn with_no_model_there_is_no_voice() {
        assert!(load(Path::new("no such folder")).is_none());
    }

    #[test]
    fn kokoro_is_set_up_for_a_sentence_at_a_time() {
        let config = kokoro(Path::new("models"));
        assert_eq!(config.max_num_sentences, 1);
        assert_eq!(config.model.num_threads, 2);
        assert_eq!(config.model.kokoro.tokens.as_deref(), Some("models\\tokens.txt"));
        assert_eq!(config.model.kokoro.data_dir.as_deref(), Some("models\\espeak-ng-data"));
    }

    /// By hand, on the PC (it speaks!): cargo test -- --ignored --nocapture says_it_out_loud
    /// What goes to the speakers is read back from Windows, so this also works with the volume down.
    /// Something else playing counts too.
    #[test]
    #[ignore]
    fn says_it_out_loud() {
        let model = Config::load().home.join("models").join(MODEL);
        let started = Instant::now();
        let tts = load(&model).expect("no voice");
        println!("read in {:.1} s", started.elapsed().as_secs_f64());

        let device = cpal::default_host().default_output_device().unwrap();
        let format = device.default_output_config().unwrap();
        assert_eq!(format.sample_format(), cpal::SampleFormat::F32);
        let loudest = Arc::new(Mutex::new(0f32));
        let hearing = loudest.clone();
        let listening = device
            .build_input_stream(
                format.config(),
                move |samples: &[f32], _: &cpal::InputCallbackInfo| {
                    let mut loudest = hearing.lock().unwrap();
                    *loudest = samples.iter().fold(*loudest, |most, sample| most.max(sample.abs()));
                },
                |e| println!("listening: {e}"),
                None,
            )
            .unwrap();
        listening.play().unwrap();

        let started = Instant::now();
        let first = Arc::new(Mutex::new(None));
        let noting = first.clone();
        let heard = loudest.clone();
        let on_start: Box<dyn FnOnce() + Send> = Box::new(move || {
            *noting.lock().unwrap() = Some((started.elapsed(), *heard.lock().unwrap()));
        });
        let text = "Testing Natro's voice. This sentence comes second.".to_string();
        speak(&tts, 0, Saying { text, on_start: Some(on_start), then: None }).unwrap();
        let (first, before) = first.lock().unwrap().expect("no first sound");
        let loudest = *loudest.lock().unwrap();
        println!("first sound after {first:?}, all of it in {:?}, loudest {before} before it and {loudest} after", started.elapsed());
        assert!(loudest > 0.01, "the speakers got nothing");
    }
}
