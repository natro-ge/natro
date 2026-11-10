//! Whether this PC is making sound right now, from Windows' meter on the default speakers: music, a video,
//! a call, whatever it comes from. The dock doesn't listen for him to go on while it is, as the microphone
//! would hear it and recognizing that costs money.

use windows::Win32::Media::Audio::Endpoints::IAudioMeterInformation;
use windows::Win32::Media::Audio::{IMMDeviceEnumerator, MMDeviceEnumerator, eConsole, eRender};
use windows::Win32::System::Com::{CLSCTX_ALL, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx};

/// The loudest sample (of 1) that still counts as nothing playing.
const NOTHING: f32 = 0.005;

/// The meter, for the thread that opened it.
pub struct Speakers(IAudioMeterInformation);

impl Speakers {
    /// None with no speakers, which then count as nothing playing.
    pub fn open() -> Option<Speakers> {
        unsafe {
            // Already set up on this thread some other way is fine too.
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let devices: IMMDeviceEnumerator = CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL).ok()?;
            let speakers = devices.GetDefaultAudioEndpoint(eRender, eConsole).ok()?;
            speakers.Activate(CLSCTX_ALL, None).ok().map(Speakers)
        }
    }

    pub fn playing(&self) -> bool {
        unsafe { self.0.GetPeakValue() }.is_ok_and(|peak| peak > NOTHING)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// By hand, on the PC (a second of a very quiet tone): cargo test -- --ignored --nocapture tells_if_this_pc_plays
    #[test]
    #[ignore]
    fn tells_if_this_pc_plays() {
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
        use std::time::Duration;

        let speakers = Speakers::open().expect("no speakers");
        let read = |what: &str| {
            for _ in 0..5 {
                println!("{what}: {:.4} {}", unsafe { speakers.0.GetPeakValue() }.unwrap(), speakers.playing());
                std::thread::sleep(Duration::from_millis(200));
            }
        };
        read("as it is");

        let device = cpal::default_host().default_output_device().unwrap();
        let format = device.default_output_config().unwrap();
        assert_eq!(format.sample_format(), cpal::SampleFormat::F32);
        let (channels, step) = (format.channels() as usize, 440.0 / format.sample_rate() as f32);
        let mut phase = 0f32;
        let tone = device
            .build_output_stream(
                format.config(),
                move |out: &mut [f32], _: &cpal::OutputCallbackInfo| {
                    for frame in out.chunks_mut(channels) {
                        phase = (phase + step) % 1.0;
                        frame.fill((phase * std::f32::consts::TAU).sin() * 0.02);
                    }
                },
                |e| println!("the tone: {e}"),
                None,
            )
            .unwrap();
        tone.play().unwrap();
        std::thread::sleep(Duration::from_millis(100));
        read("with the tone");
        assert!(speakers.playing(), "it should hear the tone");
    }
}
