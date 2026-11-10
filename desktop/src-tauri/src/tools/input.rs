//! Keys, typing and the mouse, as Windows takes them (SendInput), for the screen's tools (uia.rs).
//! Keys go by the names she writes ("ctrl+shift+s", "page down", "f5"); text is typed as
//! characters, whatever the keyboard's language.

use std::thread;
use std::time::Duration;

use windows::Win32::Foundation::POINT;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBD_EVENT_FLAGS, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE, MOUSE_EVENT_FLAGS, MOUSEINPUT, SendInput, VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{GetCursorPos, SetCursorPos};

use super::{is_space, quoted};

pub const VK_RETURN: u16 = 0x0D;
const VK_SHIFT: u16 = 0x10;
pub const VK_CONTROL: u16 = 0x11;
pub const VK_MENU: u16 = 0x12;
/// Keys Windows needs told are extended, or it takes them for the number pad's.
const EXTENDED: &[u16] = &[0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5D];
pub const WHEEL_DELTA: i32 = 120;

/// A key's virtual-key code, by the name she writes ("ctrl", "page down" as "pagedown", "f5").
fn key_code(name: &str) -> Option<u16> {
    Some(match name {
        "ctrl" | "control" => 0x11,
        "shift" => 0x10,
        "alt" => 0x12,
        "win" | "windows" | "cmd" => 0x5B,
        "enter" | "return" => 0x0D,
        "tab" => 0x09,
        "esc" | "escape" => 0x1B,
        "space" => 0x20,
        "backspace" => 0x08,
        "delete" | "del" => 0x2E,
        "insert" => 0x2D,
        "home" => 0x24,
        "end" => 0x23,
        "pageup" | "pgup" => 0x21,
        "pagedown" | "pgdn" => 0x22,
        "up" => 0x26,
        "down" => 0x28,
        "left" => 0x25,
        "right" => 0x27,
        "capslock" => 0x14,
        "printscreen" => 0x2C,
        "menu" | "apps" => 0x5D,
        "plus" | "=" => 0xBB,
        "minus" | "-" => 0xBD,
        "volumeup" => 0xAF,
        "volumedown" => 0xAE,
        "volumemute" => 0xAD,
        "playpause" => 0xB3,
        "nexttrack" => 0xB0,
        "prevtrack" => 0xB1,
        ";" => 0xBA,
        "," => 0xBC,
        "." => 0xBE,
        "/" => 0xBF,
        "`" => 0xC0,
        "[" => 0xDB,
        "\\" => 0xDC,
        "]" => 0xDD,
        "'" => 0xDE,
        _ => {
            let number: u16 = name.strip_prefix('f')?.parse().ok()?;
            return (1..=24).contains(&number).then_some(0x6F + number);
        }
    })
}

/// The keys of "ctrl+shift+s", as (virtual-key code, extended): the modifiers, then the key.
pub fn parse_keys(text: &str) -> Result<Vec<(u16, bool)>, String> {
    let mut names: Vec<String> =
        text.split('+').map(|part| part.trim_matches(is_space).to_lowercase().replace(' ', "")).collect();
    if text.trim_matches(is_space).ends_with('+') {
        // "ctrl++": the plus key.
        names.retain(|name| !name.is_empty());
        names.push("plus".into());
    }
    let mut keys = Vec::new();
    for name in names {
        let mut chars = name.chars();
        let code = match (key_code(&name), chars.next(), chars.next()) {
            (Some(code), _, _) => code,
            (None, Some(c), None) if c.is_ascii_alphanumeric() => c.to_ascii_uppercase() as u16,
            _ => {
                return Err(format!(
                    "Unknown key {}: use names like ctrl, shift, alt, win, enter, tab, esc, space, backspace, delete, \
                     home, end, page up, page down, up, down, left, right, f1 to f24, or a letter or digit.",
                    quoted(&name)
                ));
            }
        };
        keys.push((code, EXTENDED.contains(&code)));
    }
    if keys.is_empty() {
        return Err("key needs the keys, like ctrl+s or enter.".into());
    }
    Ok(keys)
}

/// Every key down in order, then up in reverse: (code, extended, up).
pub fn key_events(keys: &[(u16, bool)]) -> Vec<(u16, bool, bool)> {
    let downs = keys.iter().map(|&(code, extended)| (code, extended, false));
    downs.chain(keys.iter().rev().map(|&(code, extended)| (code, extended, true))).collect()
}

/// What typing sends.
#[derive(Debug, PartialEq)]
pub enum Typed {
    /// A UTF-16 unit, as a character.
    Char(u16),
    /// A key: code, extended, up.
    Key(u16, bool, bool),
}

/// What typing the text sends: each character, and Shift+Enter for a line break, which starts a
/// new line in a chat instead of sending it.
pub fn typing(text: &str) -> Vec<Typed> {
    let mut events = Vec::new();
    for (number, line) in text.replace("\r\n", "\n").split('\n').enumerate() {
        if number > 0 {
            let shift_enter = key_events(&[(VK_SHIFT, false), (VK_RETURN, false)]);
            events.extend(shift_enter.into_iter().map(|(code, extended, up)| Typed::Key(code, extended, up)));
        }
        events.extend(line.encode_utf16().map(Typed::Char));
    }
    events
}

/// The mouse goes back where he left it.
pub fn mouse_kept(act: impl FnOnce() -> Result<(), String>) -> Result<(), String> {
    let mut was = POINT::default();
    let _ = unsafe { GetCursorPos(&mut was) };
    let done = act();
    thread::sleep(Duration::from_millis(50));
    let _ = unsafe { SetCursorPos(was.x, was.y) };
    done
}

pub fn send(inputs: &[INPUT]) -> Result<(), String> {
    if inputs.is_empty() {
        return Ok(());
    }
    let sent = unsafe { SendInput(inputs, size_of::<INPUT>() as i32) };
    if sent as usize != inputs.len() {
        let why = "Windows didn't take the keys or clicks (a window running as administrator may be in front).";
        return Err(why.into());
    }
    Ok(())
}

pub fn key_input(code: u16, extended: bool, up: bool) -> INPUT {
    let mut flags = KEYBD_EVENT_FLAGS(0);
    if extended {
        flags |= KEYEVENTF_EXTENDEDKEY;
    }
    if up {
        flags |= KEYEVENTF_KEYUP;
    }
    let key = KEYBDINPUT { wVk: VIRTUAL_KEY(code), wScan: 0, dwFlags: flags, time: 0, dwExtraInfo: 0 };
    INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: key } }
}

pub fn char_input(unit: u16, up: bool) -> INPUT {
    let flags = if up { KEYEVENTF_UNICODE | KEYEVENTF_KEYUP } else { KEYEVENTF_UNICODE };
    let key = KEYBDINPUT { wVk: VIRTUAL_KEY(0), wScan: unit, dwFlags: flags, time: 0, dwExtraInfo: 0 };
    INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: key } }
}

pub fn mouse_input(flags: MOUSE_EVENT_FLAGS, data: u32) -> INPUT {
    let mouse = MOUSEINPUT { dx: 0, dy: 0, mouseData: data, dwFlags: flags, time: 0, dwExtraInfo: 0 };
    INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: mouse } }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys() {
        assert_eq!(parse_keys("ctrl+shift+s").unwrap(), [(0x11, false), (0x10, false), (b'S' as u16, false)]);
        assert_eq!(parse_keys("Page Down").unwrap(), [(0x22, true)]);
        assert_eq!(parse_keys("alt+f4").unwrap(), [(0x12, false), (0x73, false)]);
        assert_eq!(parse_keys("ctrl++").unwrap(), [(0x11, false), (0xBB, false)]);
        assert_eq!(parse_keys("win+d").unwrap(), [(0x5B, true), (b'D' as u16, false)]);
        assert!(parse_keys("hyper+s").unwrap_err().starts_with("Unknown key 'hyper': use names like ctrl"));
        assert!(parse_keys("ctrl+ბ").is_err() && parse_keys("f25").is_err());
        assert_eq!(
            key_events(&[(0x11, false), (0x53, false)]),
            [(0x11, false, false), (0x53, false, false), (0x53, false, true), (0x11, false, true)]
        );
    }

    #[test]
    fn typing_sends_characters_and_a_line_break_as_shift_enter() {
        use Typed::*;
        assert_eq!(
            typing("hi\nyo"),
            [
                Char('h' as u16),
                Char('i' as u16),
                Key(0x10, false, false),
                Key(0x0D, false, false),
                Key(0x0D, false, true),
                Key(0x10, false, true),
                Char('y' as u16),
                Char('o' as u16),
            ]
        );
        // Georgian, and a surrogate pair.
        assert_eq!(typing("გ😀"), [Char(0x10D2), Char(0xD83D), Char(0xDE00)]);
    }
}
