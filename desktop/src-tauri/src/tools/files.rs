//! Files on the PC: find them by name or by what's in them, read them, open them (the tools
//! search_files, search_file_contents, read_file, open_file and open_link).
//!
//! - Names: Everything (voidtools; installed with winget, starts at login), which indexes every
//!   drive and answers instantly. Windows, programs, AppData, the Recycle Bin and code-library
//!   folders are left out unless asked for.
//! - Contents: the Windows Search index, which covers his whole user folder (Desktop,
//!   Documents, Downloads, Pictures, Videos, OneDrive...), asked through ADO as before.
//! - Reading: text files, Word documents (.docx) and PDFs (pdf-extract).
//! - Opening: with the program Windows uses for that kind of file. Programs and scripts are
//!   never opened from here (open_app opens apps).
//!
//! Ported from the Python helper's files.py (cleanup step 5.2) with the same words.

use std::io::Read;
use std::os::windows::process::CommandExt;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use windows::Win32::Storage::FileSystem::{FILE_ATTRIBUTE_DIRECTORY, GetFileAttributesW, INVALID_FILE_ATTRIBUTES};
use windows::Win32::System::Com::{COINIT_APARTMENTTHREADED, CoInitializeEx, CoUninitialize};
use windows::Win32::UI::Shell::ShellExecuteW;
use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
use windows::core::{HSTRING, PCWSTR, w};

use super::{home, is_space, number_arg, quoted, spec, text_arg, truthy};
use crate::automation::{Automation, bstr, int};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// Left out of name searches unless he asks to search everywhere.
const NOISE: [&str; 13] = [
    "\\$Recycle.Bin\\", "\\AppData\\", "C:\\Windows\\", "C:\\ProgramData\\", "\\Program Files\\",
    "\\Program Files (x86)\\", "\\node_modules\\", "\\.git\\", "\\.venv\\", "\\site-packages\\", "\\__pycache__\\",
    "\\System Volume Information\\", "\\WindowsApps\\",
];
const CONTENT_NOISE: [&str; 6] =
    ["\\AppData\\", "\\.venv\\", "\\site-packages\\", "\\node_modules\\", "\\.git\\", "\\__pycache__\\"];
const TEXT_TYPES: [&str; 32] = [
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".ini", ".cfg", ".yaml", ".yml", ".py",
    ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".sql", ".srt", ".tex", ".rtf", ".bat", ".ps1",
    ".sh", ".toml", ".env.example",
];
/// Opening these would run something (commands.rs's Start-Process and explorer too).
pub(super) const PROGRAMS: [&str; 24] = [
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".scr",
    ".pif", ".reg", ".lnk", ".url", ".hta", ".cpl", ".jar", ".appref-ms", ".psm1", ".sh",
];
/// Per read; a longer text is read on with start.
const READ_CHARS: usize = 15000;
const TEXT_BYTES: u64 = 5 << 20;
const PDF_PAGES: usize = 40;
const SEARCH_WAIT: Duration = Duration::from_secs(20);
/// Everything started (it wasn't running): searched again after this.
const EVERYTHING_START: Duration = Duration::from_secs(3);

/// A match: its path, size (None for a folder) and when it was changed ("2026-10-01T02:26").
type Row = (String, Option<u64>, String);
/// es's exit code, what it printed and what it printed as errors.
struct Ran {
    code: Option<i32>,
    stdout: String,
    stderr: String,
}
type Search = Box<dyn Fn(&[String], usize) -> Result<Ran, String> + Send + Sync>;
type Index = Box<dyn Fn(&str, &str, usize) -> Result<Vec<Row>, String> + Send + Sync>;

/// The tools as the brain sees them: in desktop/tools.json, after the apps'.
pub fn specs() -> Vec<Value> {
    let home = home();
    let path = json!({"type": "string", "description": format!("A full path. His home folder is {home}.")});
    vec![
        spec(
            "search_files",
            "Find files and folders on the PC by name, newest first, with Everything's search syntax: words must \
             all appear in the name or path (\"cv\", \"invoice 2026\"); ext:pdf;docx for kinds; dm:today, \
             dm:thisweek or dm:last7days for when changed; size:>100mb; folder: for folders only; a term with a \
             backslash matches the path (\"\\Downloads\\ ext:mp4\"). Windows, programs, AppData and the Recycle Bin \
             are left out unless everywhere is true.",
            &json!({"query": {"type": "string"}, "everywhere": {"type": "boolean"},
                    "max_results": {"type": "integer", "description": "Default 15, up to 50."}}),
            &["query"],
            false,
            false,
        ),
        spec(
            "search_file_contents",
            &format!(
                "Find files whose text contains all these words (documents, PDFs, notes; from the Windows Search \
                 index of his user folder, {home}). Best match first."
            ),
            &json!({"text": {"type": "string"}, "folder": path,
                    "max_results": {"type": "integer", "description": "Default 10."}}),
            &["text"],
            false,
            false,
        ),
        spec(
            "read_file",
            &format!(
                "Read a text file, Word document (.docx) or PDF on the PC, {READ_CHARS} characters at a time: a \
                 longer text says where it was cut, and start reads on from there."
            ),
            &json!({"path": path, "start": {"type": "integer", "description": "Character to start from (default 0)."}}),
            &["path"],
            false,
            false,
        ),
        spec(
            "open_file",
            "Open a file or folder on the PC with its usual program (a PDF in the PDF viewer, a folder in File \
             Explorer). Not for programs: open_app opens apps.",
            &json!({"path": path}),
            &["path"],
            false,
            false,
        ),
        spec(
            "open_link",
            "Open a web page in the PC's browser.",
            &json!({"url": {"type": "string", "description": "An http or https link."}}),
            &["url"],
            false,
            true,
        ),
    ]
}

/// Whether name is one of these tools.
pub fn offers(name: &str) -> bool {
    matches!(name, "search_files" | "search_file_contents" | "read_file" | "open_file" | "open_link")
}

pub struct Files {
    search: Search,
    index: Index,
}

impl Files {
    pub fn new() -> Files {
        Files { search: Box::new(run_es), index: Box::new(windows_search) }
    }

    /// Run one of the tools (offers). Blocking. Ok: its result for Natro; Err: why it failed, for her.
    pub fn run(&self, name: &str, args: &Value) -> Result<String, String> {
        let needs = |key: &str| text_arg(args, key).ok_or_else(|| format!("{name} needs {key}."));
        match name {
            "search_files" => self.search(
                needs("query")?,
                truthy(&args["everywhere"]),
                number_arg(name, args, "max_results", 15)?,
            ),
            "search_file_contents" => self.search_contents(
                needs("text")?,
                text_arg(args, "folder").filter(|folder| !folder.is_empty()),
                number_arg(name, args, "max_results", 10)?,
            ),
            "read_file" => read(needs("path")?, number_arg(name, args, "start", 0)?),
            "open_file" => open(needs("path")?),
            "open_link" => open_link(needs("url")?),
            _ => Err(format!("The PC has no tool called {}.", quoted(name))),
        }
    }

    fn search(&self, query: &str, everywhere: bool, max_results: i64) -> Result<String, String> {
        let count = max_results.clamp(1, 50) as usize;
        let terms = search_terms(query, everywhere)?;
        let mut ran = (self.search)(&terms, count)?;
        let everything = everything_exe();
        if ran.code == Some(8) && everything.exists() {
            // Everything isn't running: start it, once.
            let _ = Command::new(&everything).arg("-startup").spawn();
            thread::sleep(EVERYTHING_START);
            ran = (self.search)(&terms, count)?;
        }
        if ran.code != Some(0) {
            let said = if ran.stderr.is_empty() { &ran.stdout } else { &ran.stderr };
            let said = said.trim();
            let said =
                if said.is_empty() { ran.code.map_or("killed".into(), |code| code.to_string()) } else { said.into() };
            return Err(format!("The file search failed: {said}"));
        }
        let rows = parse_es(&ran.stdout);
        Ok(if rows.is_empty() { "No files match.".into() } else { describe_rows(&rows, count, "newest") })
    }

    /// The files called exactly name, newest first, without the folders he'd never mean
    /// (this.rs). None if it can't tell, as with nothing found.
    pub fn named(&self, name: &str, count: usize) -> Vec<String> {
        if name.contains('"') {
            return Vec::new();
        }
        let Ok(terms) = search_terms(&format!("file: wfn:\"{name}\""), false) else { return Vec::new() };
        match (self.search)(&terms, count) {
            Ok(ran) if ran.code == Some(0) => parse_es(&ran.stdout).into_iter().map(|(path, _, _)| path).collect(),
            _ => Vec::new(),
        }
    }

    fn search_contents(&self, text: &str, folder: Option<&str>, max_results: i64) -> Result<String, String> {
        let count = max_results.clamp(1, 30) as usize;
        let scope = folder.map_or_else(home, windows_path);
        let rows = (self.index)(text, &scope, count)?;
        Ok(if rows.is_empty() {
            format!("No indexed files in {scope} contain that.")
        } else {
            describe_rows(&rows, count, "best")
        })
    }
}

// Paths

/// A path as Python's pathlib writes it: ~ for his home folder, backslashes, no doubled or
/// trailing ones, no "." parts.
fn windows_path(path: &str) -> String {
    let path = expand_user(path).replace('/', "\\");
    let unc = path.starts_with("\\\\");
    let parts: Vec<&str> = path.split('\\').filter(|part| !part.is_empty() && *part != ".").collect();
    let mut out = parts.join("\\");
    if unc {
        out = format!("\\\\{out}");
    } else if path.starts_with('\\') {
        out = format!("\\{out}");
    }
    // A drive alone keeps its backslash ("C:\").
    if out.len() == 2 && out.ends_with(':') && path.len() > 2 {
        out.push('\\');
    }
    out
}

/// ~ or ~name at the start, as Python's expanduser does on Windows.
fn expand_user(path: &str) -> String {
    let Some(rest) = path.strip_prefix('~') else {
        return path.to_string();
    };
    let end = rest.find(['\\', '/']).unwrap_or(rest.len());
    let (user, rest) = rest.split_at(end);
    let home = home();
    let base = if user.is_empty() || std::env::var("USERNAME").is_ok_and(|me| me == user) {
        home
    } else {
        Path::new(&home).parent().map_or(home.clone(), |users| users.join(user).display().to_string())
    };
    format!("{base}{rest}")
}

/// A file's last extension, lowercase, as Python's suffix (".pdf"; "" for ".env" or "file.").
fn suffix(file: &Path) -> String {
    let name = file.file_name().map(|name| name.to_string_lossy().to_lowercase()).unwrap_or_default();
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_string(),
        _ => String::new(),
    }
}

// Names (Everything)

fn everything_exe() -> PathBuf {
    let programs = std::env::var("ProgramFiles").unwrap_or_else(|_| r"C:\Program Files".into());
    Path::new(&programs).join("Everything").join("Everything.exe")
}

fn es_path() -> Result<PathBuf, String> {
    let extensions = std::env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into());
    for folder in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
        for extension in extensions.split(';').filter(|e| !e.is_empty()) {
            let found = folder.join(format!("es{}", extension.to_lowercase()));
            if found.is_file() {
                return Ok(found);
            }
        }
    }
    let local = std::env::var("LOCALAPPDATA").unwrap_or_default();
    let linked = Path::new(&local).join("Microsoft").join("WinGet").join("Links").join("es.exe");
    if linked.is_file() {
        return Ok(linked);
    }
    Err("Everything's command-line tool (es) isn't installed: winget install voidtools.Everything.Cli".into())
}

/// The query as Everything search terms. es reads its own command line, so each term goes as
/// written ("a word", a "quoted phrase", or !"one left out"); terms that look like es options
/// ("-export-csv") are dropped.
fn search_terms(query: &str, everywhere: bool) -> Result<Vec<String>, String> {
    let chars: Vec<char> = query.chars().collect();
    let mut terms = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if is_space(chars[i]) {
            i += 1;
            continue;
        }
        let quote = if chars[i] == '!' { i + 1 } else { i };
        let closing = (chars.get(quote) == Some(&'"'))
            .then(|| chars[quote + 1..].iter().position(|c| *c == '"').map(|at| quote + 1 + at))
            .flatten();
        let end = match closing {
            Some(closing) => closing + 1,
            None => (i..chars.len()).find(|&at| is_space(chars[at])).unwrap_or(chars.len()),
        };
        terms.push(chars[i..end].iter().collect::<String>());
        i = end;
    }
    terms.retain(|term| !term.starts_with(['-', '/']));
    if terms.is_empty() {
        return Err("Say what to search for.".into());
    }
    if !everywhere {
        let left_out = |noise: &&str| if noise.contains(' ') { format!("!\"{noise}\"") } else { format!("!{noise}") };
        terms.extend(NOISE.iter().map(left_out));
    }
    Ok(terms)
}

fn run_es(terms: &[String], count: usize) -> Result<Ran, String> {
    let es = es_path()?;
    let arguments = format!(
        "-n {count} -sort date-modified-descending -size -dm -tsv -date-format 1 -size-format 1 {}",
        terms.join(" ")
    );
    let mut child = Command::new(&es)
        .raw_arg(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|e| format!("The file search didn't start: {e}"))?;
    let read = |mut pipe: Box<dyn Read + Send>| {
        thread::spawn(move || {
            let mut out = Vec::new();
            let _ = pipe.read_to_end(&mut out);
            String::from_utf8_lossy(&out).into_owned()
        })
    };
    let stdout = read(Box::new(child.stdout.take().expect("piped")));
    let stderr = read(Box::new(child.stderr.take().expect("piped")));
    let deadline = Instant::now() + SEARCH_WAIT;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            return Err(format!("The file search took over {} s.", SEARCH_WAIT.as_secs()));
        }
        thread::sleep(Duration::from_millis(20));
    };
    Ok(Ran {
        code: status.code(),
        stdout: stdout.join().unwrap_or_default(),
        stderr: stderr.join().unwrap_or_default(),
    })
}

/// es's TSV (a header, then path, size, date modified), as rows.
fn parse_es(output: &str) -> Vec<Row> {
    output
        .lines()
        .skip(1)
        .filter_map(|line| {
            let parts: Vec<&str> = line.split('\t').collect();
            if parts.len() < 3 || parts[0].is_empty() {
                return None;
            }
            let size = parts[1].trim();
            let digits = !size.is_empty() && size.chars().all(|c| c.is_ascii_digit());
            let size = if digits { size.parse().ok() } else { None };
            Some((parts[0].to_string(), size, parts[2].chars().take(16).collect()))
        })
        .collect()
}

fn human_size(size: Option<u64>) -> String {
    let Some(size) = size else {
        return "folder".into();
    };
    let mut size = size as f64;
    for unit in ["bytes", "KB", "MB"] {
        if size < 1024.0 {
            return if unit == "bytes" { format!("{size:.0} {unit}") } else { format!("{size:.1} {unit}") };
        }
        size /= 1024.0;
    }
    format!("{size:.1} GB")
}

fn describe_rows(rows: &[Row], count: usize, order: &str) -> String {
    let head = if rows.len() >= count {
        format!("The {order} {count} matches (there may be more):")
    } else {
        format!("{} matches:", rows.len())
    };
    let lines = rows
        .iter()
        .map(|(path, size, date)| format!("{}  {}  {path}", date.replace('T', " "), human_size(*size)));
    std::iter::once(head).chain(lines).collect::<Vec<_>>().join("\n")
}

// Contents (Windows Search)

/// The words of what to look for, as Python's [\w'-]+ finds them.
fn index_terms(text: &str) -> Vec<String> {
    text.split(|c: char| !(c.is_alphanumeric() || c == '_' || c == '\'' || c == '-'))
        .filter(|term| !term.is_empty())
        .map(String::from)
        .collect()
}

/// The Windows Search SQL for files under scope that contain every term, best first.
fn index_query(terms: &[String], scope: &str, count: usize) -> String {
    let condition: Vec<String> = terms.iter().map(|term| format!("\"{}*\"", term.replace('\'', "''"))).collect();
    let scope = scope.replace('\\', "/").replace('\'', "''");
    // Code libraries and app data under his folder are indexed too, and only crowd out his own files.
    let skip: String =
        CONTENT_NOISE.iter().map(|part| format!(" AND NOT System.ItemPathDisplay LIKE '%{part}%'")).collect();
    format!(
        "SELECT TOP {count} System.ItemPathDisplay, System.Size, System.DateModified FROM SystemIndex \
         WHERE SCOPE='file:{scope}' AND CONTAINS(*, '{}'){skip} ORDER BY System.Search.Rank DESC",
        condition.join(" AND ")
    )
}

/// Files under scope whose contents (or names) contain every word of text, from the Windows Search index.
fn windows_search(text: &str, scope: &str, count: usize) -> Result<Vec<Row>, String> {
    let terms = index_terms(text);
    if terms.is_empty() {
        return Err("Say what text to look for.".into());
    }
    let sql = index_query(&terms, scope, count);
    unsafe {
        // Tools run on threads of their own.
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok();
        let rows = ask_index(&sql).map_err(|e| format!("Windows Search failed: {e}"));
        if com {
            CoUninitialize();
        }
        rows
    }
}

fn ask_index(sql: &str) -> Result<Vec<Row>, String> {
    let connection = Automation::create(w!("ADODB.Connection"))?;
    connection.call("Open", vec![bstr("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")])?;
    let rows = (|| -> Result<Vec<Row>, String> {
        let records = connection.call("Execute", vec![bstr(sql)])?.object()?;
        let mut rows = Vec::new();
        while !records.get("EOF")?.boolean() {
            let fields = records.get("Fields")?.object()?;
            let value = |i: i32| fields.call("Item", vec![int(i)])?.object()?.get("Value");
            rows.push((value(0)?.string(), value(1)?.number(), value(2)?.date()));
            records.call("MoveNext", vec![])?;
        }
        Ok(rows)
    })();
    let _ = connection.call("Close", vec![]);
    rows
}

// Reading

/// Whether there's a file (Some(true)) or a folder (Some(false)) at a path. Windows' attributes, as
/// Python's stat fell back on: they're there for files no one may read too.
pub(super) fn kind_at(path: &Path) -> Option<bool> {
    let attributes = unsafe { GetFileAttributesW(&HSTRING::from(path.as_os_str())) };
    (attributes != INVALID_FILE_ATTRIBUTES).then_some(attributes & FILE_ATTRIBUTE_DIRECTORY.0 == 0)
}

fn read(path: &str, start: i64) -> Result<String, String> {
    let file = PathBuf::from(expand_user(path));
    if kind_at(&file) != Some(true) {
        return Err(format!("There's no file {}.", quoted(path)));
    }
    let kind = suffix(&file);
    let name = file.file_name().map(|name| name.to_string_lossy().to_lowercase()).unwrap_or_default();
    let text = if kind == ".docx" {
        docx_text(&file)?
    } else if kind == ".pdf" {
        pdf_text(&file)?
    } else if TEXT_TYPES.contains(&kind.as_str()) || TEXT_TYPES.iter().any(|kind| name.ends_with(kind)) {
        let mut data = Vec::new();
        std::fs::File::open(&file)
            .and_then(|opened| opened.take(TEXT_BYTES).read_to_end(&mut data))
            .map_err(|e| format!("Couldn't read {}: {e}", quoted(path)))?;
        decode(&data)
    } else {
        let what = if kind.is_empty() { "those" } else { &kind };
        return Err(format!("Natro can't read {what} files; she can open it for him instead."));
    };
    let text = text.replace("\r\n", "\n");
    let chars: Vec<char> = text.trim_matches(is_space).chars().collect();
    if chars.is_empty() {
        return Ok("The file has no text (a scanned PDF, maybe).".into());
    }
    let start = start.max(0) as usize;
    let end = start + READ_CHARS;
    if end < chars.len() {
        let part: String = chars[start..end].iter().collect();
        return Ok(format!(
            "{part}\n[cut at character {end} of {}: read again with start={end} for more]",
            chars.len()
        ));
    }
    let rest: String = chars.get(start..).unwrap_or_default().iter().collect();
    Ok(if rest.is_empty() { format!("The text ends at character {}.", chars.len()) } else { rest })
}

/// A text file's bytes: UTF-16 with its byte order mark, else UTF-8.
fn decode(data: &[u8]) -> String {
    let units = |big: bool| -> Vec<u16> {
        data[2..]
            .chunks(2)
            .map(|pair| match pair {
                [a, b] if big => u16::from_be_bytes([*a, *b]),
                [a, b] => u16::from_le_bytes([*a, *b]),
                _ => 0xFFFD,
            })
            .collect()
    };
    match data.get(..2) {
        Some([0xFF, 0xFE]) => String::from_utf16_lossy(&units(false)),
        Some([0xFE, 0xFF]) => String::from_utf16_lossy(&units(true)),
        _ => String::from_utf8_lossy(data).into_owned(),
    }
}

/// A Word document's text: its paragraphs and line breaks as lines, its tabs as tabs.
fn docx_text(file: &Path) -> Result<String, String> {
    let failed = |e: String| format!("Couldn't read that Word document: {e}");
    let opened = std::fs::File::open(file).map_err(|e| failed(e.to_string()))?;
    let mut archive = zip::ZipArchive::new(opened).map_err(|e| failed(e.to_string()))?;
    let mut markup = Vec::new();
    archive
        .by_name("word/document.xml")
        .map_err(|e| failed(e.to_string()))?
        .read_to_end(&mut markup)
        .map_err(|e| failed(e.to_string()))?;
    Ok(docx_markup_text(&String::from_utf8_lossy(&markup)))
}

fn docx_markup_text(markup: &str) -> String {
    let markup = markup.replace("</w:p>", "\n").replace("<w:br/>", "\n").replace("<w:tab/>", "\t");
    // Tags out (a "<" followed by at least one character before its ">").
    let mut text = String::with_capacity(markup.len());
    let mut rest = markup.as_str();
    while let Some(open) = rest.find('<') {
        text.push_str(&rest[..open]);
        match rest[open + 1..].find('>') {
            Some(close) if close > 0 => rest = &rest[open + close + 2..],
            _ => {
                text.push('<');
                rest = &rest[open + 1..];
            }
        }
    }
    text.push_str(rest);
    unescape(&text)
}

/// XML's entities (&amp; &lt; &gt; &quot; &apos; and numbered ones), as html.unescape reads them.
fn unescape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(at) = rest.find('&') {
        out.push_str(&rest[..at]);
        rest = &rest[at..];
        let entity = rest.find(';').map(|end| &rest[1..end]);
        let character = entity.and_then(|entity| match entity {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            _ => {
                let number = entity.strip_prefix('#')?;
                let code = match number.strip_prefix(['x', 'X']) {
                    Some(hex) => u32::from_str_radix(hex, 16).ok()?,
                    None => number.parse().ok()?,
                };
                char::from_u32(code)
            }
        });
        match (character, entity) {
            (Some(character), Some(entity)) => {
                out.push(character);
                rest = &rest[entity.len() + 2..];
            }
            _ => {
                out.push('&');
                rest = &rest[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

/// A PDF's text, its first PDF_PAGES pages. Unreadable PDFs say why.
fn pdf_text(file: &Path) -> Result<String, String> {
    let read = || -> Result<(Vec<String>, usize), pdf_extract::OutputError> {
        let mut document = pdf_extract::Document::load(file)?;
        if document.is_encrypted() {
            document.decrypt("")?; // as pypdf did: only with no password
        }
        let pages = document.get_pages().len();
        let mut texts = Vec::new();
        for page in 1..=pages.min(PDF_PAGES) as u32 {
            let mut text = String::new();
            pdf_extract::output_doc_page(&document, &mut pdf_extract::PlainTextOutput::new(&mut text), page)?;
            texts.push(text);
        }
        Ok((texts, pages))
    };
    let (texts, pages) = match catch_unwind(AssertUnwindSafe(read)) {
        Ok(Ok(read)) => read,
        Ok(Err(e)) => return Err(format!("Couldn't read that PDF: {e}")),
        Err(_) => return Err("Couldn't read that PDF: its contents aren't ones Natro can read.".into()),
    };
    let more =
        if pages > PDF_PAGES { format!("\n[only the first {PDF_PAGES} of {pages} pages]") } else { String::new() };
    Ok(texts.join("\n") + &more)
}

// Opening

fn open(path: &str) -> Result<String, String> {
    let target = PathBuf::from(windows_path(path));
    let Some(is_file) = kind_at(&target) else {
        return Err(format!("There's no file or folder {}.", quoted(path)));
    };
    if is_file && PROGRAMS.contains(&suffix(&target).as_str()) {
        return Err("That's a program or script; Natro doesn't run those from here. open_app opens apps.".into());
    }
    shell_open(&target.display().to_string())?;
    let name = target.file_name().map_or_else(|| target.display().to_string(), |name| name.to_string_lossy().into());
    Ok(format!("Opened {name}."))
}

fn open_link(url: &str) -> Result<String, String> {
    let url = url.trim_matches(is_space);
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err("Only web links (http or https) can be opened this way.".into());
    }
    shell_open(url)?;
    Ok("Opened it in the browser.".into())
}

/// Open it as a double-click would (os.startfile).
fn shell_open(target: &str) -> Result<(), String> {
    unsafe {
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok();
        let target = HSTRING::from(target);
        let done = ShellExecuteW(None, PCWSTR::null(), &target, PCWSTR::null(), PCWSTR::null(), SW_SHOWNORMAL);
        if com {
            CoUninitialize();
        }
        // Above 32 is success; below, Windows' reason.
        let code = done.0 as isize;
        if code > 32 { Ok(()) } else { Err(format!("Windows couldn't open it (error {code}).")) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    #[test]
    fn search_terms_leave_out_noise_and_es_options() {
        let terms = search_terms("invoice \"march 2026\" -export-csv out.csv ext:pdf", false).unwrap();
        assert_eq!(terms[..4], ["invoice", "\"march 2026\"", "out.csv", "ext:pdf"]);
        assert!(terms.contains(&"!\\AppData\\".to_string()) && terms.contains(&"!\"\\Program Files\\\"".to_string()));
        assert_eq!(search_terms("cv", true).unwrap(), ["cv"]);
        assert_eq!(search_terms("-export-csv", false).unwrap_err(), "Say what to search for.");
        // A quote with no end is a word; !"a phrase" is one term.
        assert_eq!(search_terms("a\"b c !\"x y\" !z", true).unwrap(), ["a\"b", "c", "!\"x y\"", "!z"]);
    }

    fn files(output: &'static str, calls: Arc<Mutex<Vec<(Vec<String>, usize)>>>) -> Files {
        Files {
            search: Box::new(move |terms, count| {
                calls.lock().unwrap().push((terms.to_vec(), count));
                Ok(Ran { code: Some(0), stdout: output.into(), stderr: String::new() })
            }),
            index: Box::new(|_, _, _| Ok(Vec::new())),
        }
    }

    #[test]
    fn files_by_their_whole_name() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let output = "Filename\tSize\tDate Modified\nC:\\a\\x y.pdf\t12\t2026-10-01T02:26:00\n";
        let found = files(output, calls.clone()).named("x y.pdf", 4);
        assert_eq!(found, [r"C:\a\x y.pdf"]);
        let (terms, count) = calls.lock().unwrap().remove(0);
        assert_eq!(count, 4);
        assert_eq!(terms[..3].join(" "), "file: wfn:\"x y.pdf\"");
        assert!(files(output, Arc::default()).named("a\"b.pdf", 4).is_empty());
    }

    #[test]
    fn name_search_output() {
        let output = "Filename\tSize\tDate Modified\n\
                      C:\\Users\\Tuxa\\Downloads\\cv.pdf\t36201\t2026-10-01T02:26:21\n\
                      C:\\Users\\Tuxa\\Documents\\CV\t\t2026-09-01T10:00:00\n";
        assert_eq!(
            parse_es(output),
            [
                ("C:\\Users\\Tuxa\\Downloads\\cv.pdf".to_string(), Some(36201), "2026-10-01T02:26".to_string()),
                ("C:\\Users\\Tuxa\\Documents\\CV".to_string(), None, "2026-09-01T10:00".to_string()),
            ]
        );
        let calls = Arc::default();
        let found = files(output, Arc::clone(&calls)).run("search_files", &json!({"query": "cv", "max_results": 2}));
        assert_eq!(
            found.unwrap(),
            "The newest 2 matches (there may be more):\n\
             2026-10-01 02:26  35.4 KB  C:\\Users\\Tuxa\\Downloads\\cv.pdf\n\
             2026-09-01 10:00  folder  C:\\Users\\Tuxa\\Documents\\CV"
        );
        assert_eq!(calls.lock().unwrap()[0].1, 2);
        let empty = files("Filename\tSize\tDate Modified\n", Arc::default());
        assert_eq!(empty.run("search_files", &json!({"query": "zzz"})).unwrap(), "No files match.");
        let failed = Files {
            search: Box::new(|_, _| Ok(Ran { code: Some(2), stdout: "  \n".into(), stderr: String::new() })),
            index: Box::new(|_, _, _| Ok(Vec::new())),
        };
        assert_eq!(failed.run("search_files", &json!({"query": "cv"})).unwrap_err(), "The file search failed: 2");
    }

    #[test]
    fn sizes_as_python_wrote_them() {
        assert_eq!(human_size(Some(1023)), "1023 bytes");
        assert_eq!(human_size(Some(1280)), "1.2 KB"); // 1.25: Python rounds half to even
        assert_eq!(human_size(Some(1_610_612_736)), "1.5 GB");
        assert_eq!(human_size(Some(5 << 40)), "5120.0 GB");
        assert_eq!(human_size(None), "folder");
    }

    #[test]
    fn content_search_uses_the_index() {
        let asked: Arc<Mutex<Vec<(String, String, usize)>>> = Arc::default();
        let seen = Arc::clone(&asked);
        let files = Files {
            search: Box::new(|_, _| unreachable!()),
            index: Box::new(move |text, scope, count| {
                seen.lock().unwrap().push((text.into(), scope.into(), count));
                Ok(vec![("C:\\Users\\Tuxa\\Documents\\lease.docx".into(), Some(20480), "2026-05-02T09:30".into())])
            }),
        };
        let found = files.run("search_file_contents", &json!({"text": "rent deposit", "folder": "D:/Papers/"}));
        assert_eq!(found.unwrap(), "1 matches:\n2026-05-02 09:30  20.0 KB  C:\\Users\\Tuxa\\Documents\\lease.docx");
        assert_eq!(*asked.lock().unwrap(), [("rent deposit".to_string(), "D:\\Papers".to_string(), 10)]);
    }

    #[test]
    fn the_index_is_asked_for_every_word() {
        let terms = index_terms("Nino's rent, deposit-2026 ბინა");
        assert_eq!(terms, ["Nino's", "rent", "deposit-2026", "ბინა"]);
        let sql = index_query(&terms[..2], "C:\\Users\\O'Neil", 10);
        let columns = "SELECT TOP 10 System.ItemPathDisplay, System.Size, System.DateModified FROM SystemIndex ";
        assert!(sql.starts_with(columns));
        assert!(sql.contains("WHERE SCOPE='file:C:/Users/O''Neil' AND CONTAINS(*, '\"Nino''s*\" AND \"rent*\"')"));
        assert!(sql.contains(" AND NOT System.ItemPathDisplay LIKE '%\\AppData\\%'"));
        assert!(sql.ends_with(" ORDER BY System.Search.Rank DESC"));
    }

    fn temp(name: &str) -> PathBuf {
        let folder = std::env::temp_dir().join(format!("natro-files-test-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        folder.join(name)
    }

    #[test]
    fn reading_files() {
        let note = temp("note.md");
        std::fs::write(&note, "# Ideas\r\nA trip to Kazbegi.\n\n").unwrap();
        let path = note.display().to_string();
        assert_eq!(read(&path, 0).unwrap(), "# Ideas\nA trip to Kazbegi.");
        let document = temp("letter.docx");
        {
            let mut archive = zip::ZipWriter::new(std::fs::File::create(&document).unwrap());
            archive.start_file("word/document.xml", zip::write::SimpleFileOptions::default()).unwrap();
            std::io::Write::write_all(
                &mut archive,
                b"<w:document><w:body><w:p><w:r><w:t>Dear Nino,</w:t></w:r></w:p>\
                  <w:p><w:r><w:t>See you &amp; Gio at 7.</w:t><w:tab/><w:t>&#x10D0;</w:t></w:r></w:p>\
                  </w:body></w:document>",
            )
            .unwrap();
            archive.finish().unwrap();
        }
        assert_eq!(read(&document.display().to_string(), 0).unwrap(), "Dear Nino,\nSee you & Gio at 7.\tა");
        let long = temp("plan.md");
        std::fs::write(&long, "a".repeat(15000) + "phase 5: Android" + &"b".repeat(10)).unwrap();
        let long = long.display().to_string();
        let cut = "[cut at character 15000 of 15026: read again with start=15000 for more]";
        assert!(read(&long, 0).unwrap().ends_with(cut));
        assert_eq!(read(&long, 15000).unwrap(), "phase 5: Android".to_string() + &"b".repeat(10));
        assert_eq!(read(&long, 20000).unwrap(), "The text ends at character 15026.");
        let wide = temp("wide.txt");
        let mut bytes = vec![0xFF, 0xFE];
        bytes.extend("ნატრო".encode_utf16().flat_map(u16::to_le_bytes));
        std::fs::write(&wide, bytes).unwrap();
        assert_eq!(read(&wide.display().to_string(), 0).unwrap(), "ნატრო");
        let photo = temp("photo.jpg");
        std::fs::write(&photo, b"\xff\xd8").unwrap();
        assert_eq!(
            read(&photo.display().to_string(), 0).unwrap_err(),
            "Natro can't read .jpg files; she can open it for him instead."
        );
        assert_eq!(read("C:\\nowhere\\missing.txt", 0).unwrap_err(), "There's no file 'C:\\\\nowhere\\\\missing.txt'.");
        let pdf = temp("broken.pdf");
        std::fs::write(&pdf, b"%PDF-1.4 not really").unwrap();
        assert!(read(&pdf.display().to_string(), 0).unwrap_err().starts_with("Couldn't read that PDF: "));
    }

    #[test]
    fn programs_and_odd_links_are_never_opened() {
        let program = temp("setup.exe");
        std::fs::write(&program, b"MZ").unwrap();
        assert!(open(&program.display().to_string()).unwrap_err().contains("program or script"));
        assert_eq!(open("C:\\nowhere\\cv.pdf").unwrap_err(), "There's no file or folder 'C:\\\\nowhere\\\\cv.pdf'.");
        assert_eq!(
            open_link("file:///C:/Windows/System32/cmd.exe").unwrap_err(),
            "Only web links (http or https) can be opened this way."
        );
        assert!(open_link("HTTPS://example.com").is_err()); // as Python's re.match: lowercase only
    }

    #[test]
    fn paths_as_pathlib_writes_them() {
        let home = home();
        assert_eq!(windows_path("~"), home);
        assert_eq!(windows_path("~/Documents/"), format!("{home}\\Documents"));
        assert_eq!(windows_path("D:/Papers//2026/./"), "D:\\Papers\\2026");
        assert_eq!(windows_path("C:\\"), "C:\\");
        assert_eq!(windows_path("\\\\nas\\share\\"), "\\\\nas\\share");
        assert_eq!(suffix(Path::new("a\\Report.PDF")), ".pdf");
        assert_eq!(suffix(Path::new(".env")), "");
        assert_eq!(suffix(Path::new("file.")), "");
    }

    /// By hand, on the PC: cargo test -- --ignored --nocapture answers_on_this_pc prints what the
    /// tools answer here, as JSON ({"ok", "result"} each). NATRO_TEST_CALLS names a JSON file of
    /// other calls to make ([["read_file", {"path": "..."}], ...]), to compare with the helper's.
    #[test]
    #[ignore]
    fn answers_on_this_pc() {
        let calls: Vec<(String, Value)> = match std::env::var("NATRO_TEST_CALLS") {
            Ok(file) => serde_json::from_str(&std::fs::read_to_string(file).unwrap()).unwrap(),
            Err(_) => vec![
                ("search_files".into(), json!({"query": "ext:pdf", "max_results": 3})),
                ("search_file_contents".into(), json!({"text": "natro", "max_results": 3})),
            ],
        };
        let files = Files::new();
        let answers: Vec<Value> = calls
            .iter()
            .map(|(tool, args)| match files.run(tool, args) {
                Ok(result) => json!({"ok": true, "result": result}),
                Err(problem) => json!({"ok": false, "result": problem}),
            })
            .collect();
        println!("ANSWERS {}", serde_json::to_string(&answers).unwrap());
    }
}
