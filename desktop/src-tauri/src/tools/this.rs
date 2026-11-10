//! What "this" and "that" mean on the PC now (the tool this): the window he's using, never
//! Natro's own, the file open in it or those selected in a folder, a browser's page, the text he
//! has selected and what's playing.
//!
//! By name only: what's in the window or the file stays here until she reads it with another
//! tool. The server asks when a request's words point at something (server/natro_agent/this.py),
//! so it's quick: everything here runs on the screen's thread (uia.rs), which has COM.
//!
//! A window doesn't say which file it has open. A browser's address does, Explorer says what's
//! selected, and for the rest the file's name is in the title ("report.docx - Word"): the file
//! Windows lists under that name as recently opened, or else the ones Everything finds by it.

use std::path::{Path, PathBuf};
use std::thread;

use serde_json::{Value, json};
use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSessionManager as Media, GlobalSystemMediaTransportControlsSession as Session,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
};
use windows::Win32::Foundation::HWND;
use windows::Win32::Storage::FileSystem::WIN32_FIND_DATAW;
use windows::Win32::System::Com::{
    CLSCTX_ALL, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx, CoTaskMemFree,
    CoUninitialize, IPersistFile, IServiceProvider, STGM_READ,
};
use windows::Win32::UI::Accessibility::{
    IUIAutomation, IUIAutomationElement, IUIAutomationTextPattern, TreeScope_Descendants, UIA_ControlTypePropertyId,
    UIA_TextPatternId, UIA_ValueValuePropertyId,
};
use windows::Win32::UI::Shell::{
    IFolderView, IPersistFolder2, IShellBrowser, IShellItemArray, IShellLinkW, IShellWindows, SHGetPathFromIDListW,
    SID_STopLevelBrowser, SIGDN_FILESYSPATH, SVGIO_SELECTION, ShellLink, ShellWindows,
};
use windows::Win32::UI::WindowsAndMessaging::{FindWindowExW, GA_ROOT, GetAncestor};
use windows::core::{HSTRING, Interface, PCWSTR, w};

use super::files::Files;
use super::is_space;
use super::spec;
use super::uia::{Front, Uia};
use crate::automation::{Owned, int};

/// More than this in a selection are left out, as a request attaches at most this many.
const MOST_FILES: u32 = 20;
const SELECTED_CHARS: i32 = 4000;
/// Files named as the one a window may have open, when several have its name.
const MOST_MAYBE: usize = 3;
const EXPLORER_CLASS: &str = "CabinetWClass";
const BROWSERS: &[&str] =
    &["msedge.exe", "chrome.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe", "arc.exe"];
/// UI Automation's control types: where a browser's address is, and a window's document.
const EDIT: i32 = 50004;
const DOCUMENT: i32 = 50030;

/// The tool as the brain sees it: in desktop/tools.json, after the screen's.
pub fn specs() -> Vec<Value> {
    vec![spec(
        "this",
        "What \"this\" or \"that\" means on the PC now: the window he's using, the file open in it or those selected in \
         a folder, a browser's page, the text he has selected, and what's playing. By name only: read what's in them \
         with read_file, web_page or screen.",
        &json!({}),
        &[],
        false,
        false,
    )]
}

/// Whether name is this tool.
pub fn offers(name: &str) -> bool {
    name == "this"
}

/// What's in front of him, as text for Natro. Blocking.
pub fn run(uia: &Uia, files: &Files) -> Result<String, String> {
    let seen = uia.on_front(|automation, front| {
        let mut seen = front.map_or_else(Seen::default, |front| window(automation, &front));
        seen.playing = playing();
        seen
    })?;
    // Not on the screen's thread: searching may start Everything.
    Ok(seen.with_file(&|name| files.named(name, MOST_MAYBE + 1)).describe())
}

/// What there is to say.
#[derive(Debug, Default, PartialEq)]
struct Seen {
    window: Option<String>,
    /// The title's words for the file, to look for, when nothing else says.
    title: Option<String>,
    file: Option<String>,
    /// Files with the name in the title, when more than one has it.
    maybe: Vec<String>,
    folder: Option<String>,
    selected_files: Vec<String>,
    page: Option<String>,
    text: Option<String>,
    playing: Option<String>,
}

impl Seen {
    fn with_file(mut self, by_name: &dyn Fn(&str) -> Vec<String>) -> Seen {
        if let Some(title) = self.title.take() {
            (self.file, self.maybe) = file_in(&title, &recent, by_name);
        }
        self
    }

    fn describe(&self) -> String {
        let mut lines = Vec::new();
        let mut say = |what: &str, value: &str| lines.push(format!("{what}: {value}"));
        if let Some(window) = &self.window {
            say("Window", window);
        }
        if let Some(file) = &self.file {
            say("File", file);
        }
        if !self.maybe.is_empty() {
            say("Maybe the file", &self.maybe.join("; "));
        }
        if let Some(folder) = &self.folder {
            say("Folder", folder);
        }
        if !self.selected_files.is_empty() {
            say("Selected files", &self.selected_files.join("; "));
        }
        if let Some(page) = &self.page {
            say("Page", page);
        }
        if let Some(text) = &self.text {
            say("Selected text", &format!("\"{text}\""));
        }
        if let Some(playing) = &self.playing {
            lines.push(playing.clone());
        }
        if lines.is_empty() { "No window of his is open, and nothing's playing.".into() } else { lines.join("\n") }
    }
}

/// What the window itself can say.
fn window(automation: &IUIAutomation, front: &Front) -> Seen {
    let mut seen = Seen { window: Some(front.title.clone()), ..Default::default() };
    let element = unsafe { automation.ElementFromHandle(HWND(front.hwnd as *mut _)) }.ok();
    if BROWSERS.contains(&front.process.as_str()) {
        match element.as_ref().and_then(|element| address_bar(automation, element)).as_deref().map(where_to) {
            Some(Address::File(file)) => seen.file = Some(file),
            Some(Address::Page(page)) => seen.page = Some(page),
            _ => {}
        }
    } else if front.class == EXPLORER_CLASS {
        (seen.folder, seen.selected_files) = explorer(front.hwnd).unwrap_or_default();
    } else {
        seen.title = Some(front.title.clone());
    }
    if front.class != EXPLORER_CLASS {
        seen.text = element.and_then(|element| selected_text(automation, &element, front.pid));
    }
    seen
}

// A browser's page

/// What a browser's address bar says.
fn address_bar(automation: &IUIAutomation, window: &IUIAutomationElement) -> Option<String> {
    unsafe {
        let edit = automation.CreatePropertyCondition(UIA_ControlTypePropertyId, int(EDIT).variant()).ok()?;
        let bar = window.FindFirst(TreeScope_Descendants, &edit).ok()?;
        Owned::from(bar.GetCurrentPropertyValue(UIA_ValueValuePropertyId).ok()?).text()
    }
}

#[derive(Debug, PartialEq)]
enum Address {
    Page(String),
    File(String),
    /// A new tab, the browser's own pages, words being typed.
    Neither,
}

/// Where an address bar's text goes. Chrome leaves out the https://.
fn where_to(bar: &str) -> Address {
    let bar = bar.trim_matches(is_space);
    if let Some(path) = bar.strip_prefix("file:///") {
        return Address::File(unquoted(path).replace('/', "\\"));
    }
    let drive = bar.as_bytes();
    if drive.len() > 3 && drive[0].is_ascii_alphabetic() && drive[1] == b':' && matches!(drive[2], b'/' | b'\\') {
        return Address::File(unquoted(bar).replace('/', "\\"));
    }
    if bar.starts_with("http://") || bar.starts_with("https://") {
        return Address::Page(bar.into());
    }
    let has_a_dot = bar.split('/').next().is_some_and(|host| host.contains('.') && !host.contains(':'));
    if has_a_dot && !bar.contains(is_space) { Address::Page(format!("https://{bar}")) } else { Address::Neither }
}

/// A file address's %20s as the letters they stand for.
fn unquoted(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = (bytes[i] == b'%')
            .then(|| text.get(i + 1..i + 3).and_then(|hex| u8::from_str_radix(hex, 16).ok()))
            .flatten();
        match hex {
            Some(byte) => {
                out.push(byte);
                i += 3;
            }
            None => {
                out.push(bytes[i]);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

// Text he has selected

/// The text selected where he's working: the focused element, when it's in that window, or the
/// window's document (typing to her in the chat takes the focus away from it).
fn selected_text(automation: &IUIAutomation, window: &IUIAutomationElement, pid: u32) -> Option<String> {
    unsafe {
        let focused =
            automation.GetFocusedElement().ok().filter(|focused| focused.CurrentProcessId() == Ok(pid as i32));
        if focused.as_ref().is_some_and(|focused| focused.CurrentIsPassword().is_ok_and(|yes| yes.as_bool())) {
            return None;
        }
        let pattern = focused.and_then(|focused| text_of(&focused)).or_else(|| {
            let document = automation.CreatePropertyCondition(UIA_ControlTypePropertyId, int(DOCUMENT).variant()).ok()?;
            text_of(&window.FindFirst(TreeScope_Descendants, &document).ok()?)
        })?;
        let ranges = pattern.GetSelection().ok()?;
        let mut pieces = Vec::new();
        for i in 0..ranges.Length().unwrap_or(0).min(8) {
            if let Ok(text) = ranges.GetElement(i).and_then(|range| range.GetText(SELECTED_CHARS)) {
                pieces.push(text.to_string());
            }
        }
        let text = pieces.join(" ");
        let text = text.trim_matches(is_space);
        (!text.is_empty()).then(|| text.to_string())
    }
}

fn text_of(element: &IUIAutomationElement) -> Option<IUIAutomationTextPattern> {
    unsafe { element.GetCurrentPatternAs(UIA_TextPatternId) }.ok()
}

// Explorer

/// The folder open in an Explorer window's tab in front, and the files selected in it, asked from
/// a thread of its own: Explorer answers which tab is which only to a single-threaded apartment,
/// which the screen's thread isn't.
fn explorer(hwnd: isize) -> Option<(Option<String>, Vec<String>)> {
    thread::spawn(move || unsafe {
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok();
        let found = explorer_window(hwnd);
        if com {
            CoUninitialize();
        }
        found
    })
    .join()
    .ok()
    .flatten()
}

fn explorer_window(hwnd: isize) -> Option<(Option<String>, Vec<String>)> {
    unsafe {
        let window = HWND(hwnd as *mut _);
        let windows: IShellWindows = CoCreateInstance(&ShellWindows, None, CLSCTX_ALL).ok()?;
        // Each tab is one of them. The first of a window's tabs on screen is the one in front.
        let front_tab = FindWindowExW(Some(window), None, w!("ShellTabWindowClass"), PCWSTR::null()).ok();
        for index in 0..windows.Count().ok()? {
            let Ok(found) = windows.Item(int(index).variant()) else { continue };
            let Ok(browser) =
                found.cast::<IServiceProvider>().and_then(|found| found.QueryService::<IShellBrowser>(&SID_STopLevelBrowser))
            else {
                continue;
            };
            let Ok(tab) = browser.GetWindow() else { continue };
            if GetAncestor(tab, GA_ROOT) != window || front_tab.is_some_and(|front_tab| front_tab != tab) {
                continue;
            }
            let view = browser.QueryActiveShellView().ok()?;
            let folder = view.cast::<IFolderView>().ok().and_then(|view| open_folder(&view));
            let mut selected = Vec::new();
            if let Ok(items) = view.GetItemObject::<IShellItemArray>(SVGIO_SELECTION) {
                for i in 0..items.GetCount().unwrap_or(0).min(MOST_FILES) {
                    let Ok(path) = items.GetItemAt(i).and_then(|item| item.GetDisplayName(SIGDN_FILESYSPATH)) else {
                        continue;
                    };
                    selected.extend(path.to_string().ok());
                    CoTaskMemFree(Some(path.0 as *const _));
                }
            }
            return Some((folder, selected));
        }
        None
    }
}

fn open_folder(view: &IFolderView) -> Option<String> {
    unsafe {
        let folder = view.GetFolder::<IPersistFolder2>().ok()?.GetCurFolder().ok()?;
        let mut path = [0u16; 260];
        let found = SHGetPathFromIDListW(folder, &mut path).as_bool();
        CoTaskMemFree(Some(folder as *const _));
        let length = path.iter().position(|c| *c == 0).unwrap_or(0);
        (found && length > 0).then(|| String::from_utf16_lossy(&path[..length]))
    }
}

// The file a title names

/// The parts of a window's title that could be its file, without the app's name at the end:
/// "report.docx - Word", "*notes.txt - Notepad", "● main.rs - Natro - Visual Studio Code".
fn title_parts(title: &str) -> Vec<String> {
    let mut parts: Vec<&str> = title.split(" - ").flat_map(|part| part.split(" — ")).flat_map(|part| part.split(" – ")).collect();
    if parts.len() > 1 {
        parts.pop();
    }
    parts
        .into_iter()
        .map(|part| {
            let part = part.trim_matches(|c: char| is_space(c) || matches!(c, '*' | '●' | '•'));
            let part = part.split(" [").next().unwrap_or(part);
            part.split(" (Read-Only)").next().unwrap_or(part).trim_matches(is_space).to_string()
        })
        .filter(|part| !part.is_empty())
        .collect()
}

/// Whether it looks like a file's name: a dot, then a few letters or digits.
fn has_extension(name: &str) -> bool {
    name.rsplit_once('.').is_some_and(|(stem, extension)| {
        !stem.is_empty()
            && (1..=8).contains(&extension.len())
            && extension.chars().all(|c| c.is_ascii_alphanumeric())
            && extension.chars().any(|c| c.is_ascii_alphabetic())
    })
}

/// The file a title names, and when several may be it, those. recent: the recently opened file
/// of that name; with no extension in it (Word's title hides it) the stem's. by_name: the files of
/// that name, newest first.
fn file_in(
    title: &str,
    recent: &dyn Fn(&str, bool) -> Option<String>,
    by_name: &dyn Fn(&str) -> Vec<String>,
) -> (Option<String>, Vec<String>) {
    let parts = title_parts(title);
    for part in &parts {
        if Path::new(part).is_absolute() && Path::new(part).exists() {
            return (Some(part.clone()), Vec::new());
        }
    }
    for part in parts.iter().filter(|part| has_extension(part)) {
        if let Some(file) = recent(part, true) {
            return (Some(file), Vec::new());
        }
        let mut named = by_name(part);
        match named.len() {
            0 => {}
            1 => return (named.pop(), Vec::new()),
            _ => {
                named.truncate(MOST_MAYBE);
                return (None, named);
            }
        }
    }
    // "report - Word": only for a whole title's first part, in the recently opened files.
    let stem = parts.first().filter(|first| !has_extension(first) && title.contains(" - "));
    (stem.and_then(|stem| recent(stem, false)), Vec::new())
}

/// The file Windows lists as recently opened under that name (whole: with its extension), if it's
/// still there.
fn recent(name: &str, whole: bool) -> Option<String> {
    let folder = PathBuf::from(std::env::var_os("APPDATA")?).join(r"Microsoft\Windows\Recent");
    if whole {
        return link_target(&folder.join(format!("{name}.lnk")));
    }
    let start = format!("{}.", name.to_lowercase());
    let mut links: Vec<(std::time::SystemTime, PathBuf)> = std::fs::read_dir(folder)
        .ok()?
        .flatten()
        .filter(|entry| {
            let file = entry.file_name().to_string_lossy().to_lowercase();
            file.starts_with(&start) && file.ends_with(".lnk")
        })
        .filter_map(|entry| Some((entry.metadata().ok()?.modified().ok()?, entry.path())))
        .collect();
    links.sort();
    links.iter().rev().find_map(|(_, link)| link_target(link))
}

/// The file a shortcut points at, if it's still there. On a thread with no COM of its own.
fn link_target(shortcut: &Path) -> Option<String> {
    if !shortcut.exists() {
        return None;
    }
    fn read(shortcut: &Path) -> Option<String> {
        unsafe {
            let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER).ok()?;
            link.cast::<IPersistFile>().ok()?.Load(&HSTRING::from(shortcut.as_os_str()), STGM_READ).ok()?;
            let mut path = [0u16; 1024];
            link.GetPath(&mut path, std::ptr::null_mut::<WIN32_FIND_DATAW>(), 0).ok()?;
            let length = path.iter().position(|c| *c == 0).unwrap_or(0);
            let path = String::from_utf16_lossy(&path[..length]);
            Path::new(&path).is_file().then_some(path)
        }
    }
    unsafe {
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok();
        let target = read(shortcut);
        if com {
            CoUninitialize();
        }
        target
    }
}

// What's playing

/// What's playing, as Windows knows it from the apps that say (Spotify, a browser's YouTube...):
/// "Playing: Yellow by Coldplay (Spotify)"; if nothing is, what's paused.
fn playing() -> Option<String> {
    let manager = Media::RequestAsync().ok()?.join().ok()?;
    let sessions = manager.GetSessions().ok()?;
    let mut paused = None;
    for i in 0..sessions.Size().ok()? {
        let Ok(session) = sessions.GetAt(i) else { continue };
        let Ok(status) = session.GetPlaybackInfo().and_then(|info| info.PlaybackStatus()) else { continue };
        if status == Status::Playing {
            return session_line("Playing", &session);
        }
        if status == Status::Paused && paused.is_none() {
            paused = session_line("Paused", &session);
        }
    }
    paused
}

fn session_line(state: &str, session: &Session) -> Option<String> {
    let media = session.TryGetMediaPropertiesAsync().ok()?.join().ok()?;
    let title = media.Title().ok()?.to_string();
    let artist = media.Artist().map(|artist| artist.to_string()).unwrap_or_default();
    let app = session.SourceAppUserModelId().map(|app| app.to_string()).unwrap_or_default();
    (!title.is_empty()).then(|| media_line(state, &title, &artist, &app))
}

fn media_line(state: &str, title: &str, artist: &str, app: &str) -> String {
    let by = if artist.is_empty() { String::new() } else { format!(" by {artist}") };
    let app = app_name(app);
    let on = if app.is_empty() { String::new() } else { format!(" ({app})") };
    format!("{state}: {title}{by}{on}")
}

/// An app as Windows names it to the media controls: "Spotify.exe", "MSEdge",
/// "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify".
fn app_name(id: &str) -> String {
    let name = id.rsplit('!').next().unwrap_or(id);
    let name = name.strip_suffix(".exe").unwrap_or(name);
    name.rsplit('.').next().unwrap_or(name).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_says_what_there_is() {
        let seen = Seen {
            window: Some("report.docx - Word".into()),
            file: Some(r"C:\Users\Tuxa\report.docx".into()),
            text: Some("The second paragraph.".into()),
            playing: Some("Playing: Yellow by Coldplay (Spotify)".into()),
            ..Default::default()
        };
        assert_eq!(
            seen.describe(),
            "Window: report.docx - Word\nFile: C:\\Users\\Tuxa\\report.docx\nSelected text: \"The second paragraph.\"\n\
             Playing: Yellow by Coldplay (Spotify)"
        );
        let folder = Seen {
            window: Some("Downloads - File Explorer".into()),
            folder: Some(r"C:\Users\Tuxa\Downloads".into()),
            selected_files: vec![r"C:\a.pdf".into(), r"C:\b.pdf".into()],
            ..Default::default()
        };
        assert_eq!(
            folder.describe(),
            "Window: Downloads - File Explorer\nFolder: C:\\Users\\Tuxa\\Downloads\nSelected files: C:\\a.pdf; C:\\b.pdf"
        );
        let maybe = Seen { maybe: vec![r"C:\a\x.txt".into(), r"C:\b\x.txt".into()], ..Default::default() };
        assert_eq!(maybe.describe(), "Maybe the file: C:\\a\\x.txt; C:\\b\\x.txt");
        assert_eq!(Seen::default().describe(), "No window of his is open, and nothing's playing.");
    }

    #[test]
    fn a_browsers_address() {
        let page = |text: &str| Address::Page(text.into());
        assert_eq!(where_to("https://example.com/a?b=c"), page("https://example.com/a?b=c"));
        assert_eq!(where_to("github.com/natro-ge/natro"), page("https://github.com/natro-ge/natro"));
        assert_eq!(where_to(" example.com "), page("https://example.com"));
        assert_eq!(
            where_to("file:///C:/Users/Tuxa/Downloads/my%20report.pdf"),
            Address::File(r"C:\Users\Tuxa\Downloads\my report.pdf".into())
        );
        assert_eq!(where_to("C:/Users/Tuxa/my%20report.pdf"), Address::File(r"C:\Users\Tuxa\my report.pdf".into()));
        assert_eq!(where_to("file:///C:/%E1%83%94.pdf"), Address::File("C:\\ე.pdf".into()));
        for neither in ["", "edge://newtab", "chrome://settings", "two words", "search for a.b c", "about:blank"] {
            assert_eq!(where_to(neither), Address::Neither, "{neither}");
        }
    }

    #[test]
    fn the_parts_of_a_title() {
        assert_eq!(title_parts("report.docx - Word"), ["report.docx"]);
        assert_eq!(title_parts("*notes.txt - Notepad"), ["notes.txt"]);
        assert_eq!(title_parts("● main.rs - Natro - Visual Studio Code"), ["main.rs", "Natro"]);
        assert_eq!(title_parts("report.docx [Compatibility Mode] - Word"), ["report.docx"]);
        assert_eq!(title_parts("report.pdf (Read-Only) - Adobe Acrobat"), ["report.pdf"]);
        assert_eq!(title_parts("photo.jpg — Paint"), ["photo.jpg"]);
        assert_eq!(title_parts("Calculator"), ["Calculator"]);
        assert_eq!(title_parts("report.pdf"), ["report.pdf"]);
    }

    #[test]
    fn what_looks_like_a_file() {
        for name in ["report.docx", "a.b.txt", "photo.JPG", "main.rs", "x.7z"] {
            assert!(has_extension(name), "{name}");
        }
        for name in ["Calculator", ".env", "version 1.2", "a.", "Natro 2.0", "Hello. There", "v1.25"] {
            assert!(!has_extension(name), "{name}");
        }
    }

    #[test]
    fn the_file_a_title_names() {
        let nowhere = |_: &str, _: bool| None;
        let nothing = |_: &str| Vec::new();
        let recent_docx = |name: &str, whole: bool| {
            (whole && name == "report.docx" || !whole && name == "report").then(|| r"C:\Docs\report.docx".to_string())
        };
        let (found, maybe) = file_in("report.docx - Word", &recent_docx, &nothing);
        assert_eq!((found.as_deref(), maybe.len()), (Some(r"C:\Docs\report.docx"), 0));
        // Word's title hides the extension when Explorer does.
        assert_eq!(file_in("report - Word", &recent_docx, &nothing).0.as_deref(), Some(r"C:\Docs\report.docx"));
        // By name, when Windows doesn't list it: one is it, several may be.
        let one = |_: &str| vec![r"C:\a\main.rs".to_string()];
        assert_eq!(file_in("main.rs - Natro - Code", &nowhere, &one).0.as_deref(), Some(r"C:\a\main.rs"));
        let many = |_: &str| (0..5).map(|i| format!(r"C:\{i}\main.rs")).collect::<Vec<_>>();
        let (found, maybe) = file_in("main.rs - Natro - Code", &nowhere, &many);
        assert!(found.is_none());
        assert_eq!(maybe, [r"C:\0\main.rs", r"C:\1\main.rs", r"C:\2\main.rs"]);
        // A window with no file in its title, and one whose name isn't found.
        assert_eq!(file_in("Calculator", &nowhere, &many), (None, Vec::new()));
        assert_eq!(file_in("gone.docx - Word", &nowhere, &nothing), (None, Vec::new()));
    }

    #[test]
    fn a_title_with_the_whole_path() {
        let exe = std::env::current_exe().unwrap().display().to_string();
        let title = format!("{exe} - Notepad++");
        assert_eq!(file_in(&title, &|_, _| None, &|_| Vec::new()).0, Some(exe));
    }

    #[test]
    fn whats_playing_as_she_reads_it() {
        assert_eq!(media_line("Playing", "Yellow", "Coldplay", "Spotify.exe"), "Playing: Yellow by Coldplay (Spotify)");
        assert_eq!(media_line("Paused", "A talk", "", "MSEdge"), "Paused: A talk (MSEdge)");
        assert_eq!(media_line("Playing", "A song", "Her", ""), "Playing: A song by Her");
        assert_eq!(app_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"), "Spotify");
        assert_eq!(app_name("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "ZuneMusic");
    }

    #[test]
    #[ignore]
    fn tells_whats_in_front_on_this_pc() {
        let uia = Uia::new();
        uia.warm_up();
        for _ in 0..3 {
            let started = std::time::Instant::now();
            let said = run(&uia, &Files::new()).unwrap();
            println!("in {:?}:\n{said}\n", started.elapsed());
        }
    }
}
