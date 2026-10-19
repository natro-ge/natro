//! Starting with Windows: Settings' "Start with Windows" switch puts Natro in the current
//! user's Run key (HKCU\Software\Microsoft\Windows\CurrentVersion\Run), so that it starts when
//! he signs in, or takes it out. Windows starts it in another folder, which doesn't matter:
//! Natro finds its files from where the program is (config.rs).
//!
//! A debug build loads its pages from `npm run dev`, which isn't running at sign-in, so it
//! puts the release build beside it (target/release) in the key instead, once there is one.

use std::path::PathBuf;

use windows::Win32::Foundation::ERROR_FILE_NOT_FOUND;
use windows::Win32::System::Registry::{HKEY_CURRENT_USER, REG_SZ, RegDeleteKeyValueW, RegSetKeyValueW};
use windows::core::{PCWSTR, w};

const RUN: PCWSTR = w!(r"Software\Microsoft\Windows\CurrentVersion\Run");
const NAME: PCWSTR = w!("Natro");

/// The program that starts at sign-in: this one, or a debug build's release build.
fn program() -> std::io::Result<PathBuf> {
    let exe = std::env::current_exe()?;
    if cfg!(debug_assertions) {
        let release = exe
            .parent()
            .and_then(|debug| debug.parent())
            .zip(exe.file_name())
            .map(|(target, name)| target.join("release").join(name));
        if let Some(release) = release.filter(|path| path.is_file()) {
            return Ok(release);
        }
    }
    Ok(exe)
}

/// Natro starts with Windows (on), or doesn't. Done again at each start while it's on: the
/// program may have moved since.
pub fn set(on: bool) -> Result<(), String> {
    let result = if on {
        let command = format!("\"{}\"", program().map_err(|e| e.to_string())?.display());
        let data: Vec<u16> = command.encode_utf16().chain([0]).collect();
        let bytes = (data.len() * size_of::<u16>()) as u32;
        unsafe { RegSetKeyValueW(HKEY_CURRENT_USER, RUN, NAME, REG_SZ.0, Some(data.as_ptr().cast()), bytes) }
    } else {
        match unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, RUN, NAME) } {
            result if result == ERROR_FILE_NOT_FOUND => return Ok(()), // not there already
            result => result,
        }
    };
    result.ok().map_err(|e| e.message())
}
