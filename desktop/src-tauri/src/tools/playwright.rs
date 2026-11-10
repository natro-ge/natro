//! Playwright's MCP server (`@playwright/mcp`, run with npx), which drives Edge for browser.rs:
//! started on first use, started again if it stopped, one call at a time.
//!
//! It speaks MCP over its stdin and stdout: JSON-RPC, a message a line. Natro needs only the
//! handshake, tools/call and answering its pings (it closes when they go unanswered for 5 s), so
//! that much of an MCP client is here (the Python helper used the mcp package). Like that package
//! on Windows, the server runs in a job object of its own: stopping it ends npx, Node and what
//! they started, and so does Natro's exit.
//!
//! Ported from the Python helper's browser.py (cleanup step 5.5).

use std::ffi::c_void;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation, SetInformationJobObject,
    TerminateJobObject,
};
use windows::core::PCWSTR;

const PACKAGE: &str = "@playwright/mcp@0.0.83";
const CALL: Duration = Duration::from_secs(60);
/// The first start downloads the package.
const START: Duration = Duration::from_secs(90);
/// After its stdin is closed, before it's ended (the mcp package's wait).
const QUIT: Duration = Duration::from_secs(2);
/// The newest MCP version it speaks, as the mcp package asked for.
const PROTOCOL: &str = "2025-11-25";
/// Playwright keeps a copy of each page it reads; they go in %TEMP%\natro-playwright, at most this much.
const OUTPUT_BYTES: u64 = 20 << 20;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// What runs Playwright's tools (the server below, or a stand-in in tests).
pub trait Mcp: Send {
    /// Its text answer; Err: why it failed, for Natro.
    fn call(&self, name: &str, args: Value) -> Result<String, String>;
}

/// Why a call failed: the tool said so, or the server couldn't be reached (it's started again).
enum Failure {
    Tool(String),
    Server(String),
}

pub struct Playwright {
    /// Its own, after the package.
    args: Vec<String>,
    /// Added to the environment.
    env: Vec<(String, String)>,
    running: Mutex<Option<Server>>,
}

impl Playwright {
    pub fn new(args: Vec<String>, env: Vec<(String, String)>) -> Playwright {
        Playwright { args, env, running: Mutex::new(None) }
    }

    #[cfg(test)]
    pub fn args(&self) -> &[String] {
        &self.args
    }

    fn start(&self) -> Result<Server, String> {
        let npx = which("npx").unwrap_or_else(|| PathBuf::from("npx"));
        let output = std::env::temp_dir().join("natro-playwright");
        let mut command = Command::new(&npx);
        command
            .args(["-y", PACKAGE])
            .args(&self.args)
            .args(["--codegen", "none", "--output-dir"])
            .arg(output)
            .args(["--output-max-size", &OUTPUT_BYTES.to_string()])
            .envs(self.env.iter().map(|(key, value)| (key, value)));
        Server::start(command, START)
    }

    fn try_call(&self, running: &mut Option<Server>, name: &str, args: &Value) -> Result<String, Failure> {
        if running.is_none() {
            *running = Some(self.start().map_err(Failure::Server)?);
        }
        let server = running.as_mut().expect("started");
        let result = server.request("tools/call", json!({"name": name, "arguments": args}), CALL)?;
        let text = result["content"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|item| item["type"] == "text")
            .filter_map(|item| item["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n");
        if result["isError"] == true {
            return Err(Failure::Tool(if text.is_empty() { format!("{name} didn't work.") } else { text }));
        }
        Ok(text)
    }
}

impl Mcp for Playwright {
    fn call(&self, name: &str, args: Value) -> Result<String, String> {
        let mut running = self.running.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let mut attempt = 1;
        loop {
            let problem = match self.try_call(&mut running, name, &args) {
                Ok(text) => return Ok(text),
                Err(Failure::Tool(text)) => return Err(text),
                Err(Failure::Server(problem)) => problem,
            };
            // It stopped, or never started: once more, in a new one.
            if let Some(server) = running.take() {
                server.stop();
            }
            if attempt == 2 {
                return Err(format!("Natro couldn't reach Edge through Playwright: {problem}"));
            }
            attempt += 1;
        }
    }
}

impl Drop for Playwright {
    fn drop(&mut self) {
        if let Some(server) = self.running.get_mut().unwrap_or_else(|poisoned| poisoned.into_inner()).take() {
            server.stop();
        }
    }
}

/// An MCP server on stdio, in a job object of its own.
struct Server {
    child: Child,
    job: Job,
    /// Shared with its reader, which answers its pings; None once closed.
    stdin: Arc<Mutex<Option<ChildStdin>>>,
    /// Its answers to Natro's requests; None once it ended.
    answers: Receiver<Option<Value>>,
    /// Its last line on stderr, to say why it stopped, and when stderr has ended.
    said: Arc<Mutex<String>>,
    said_all: Receiver<()>,
    next: u64,
}

impl Server {
    /// Start it and shake hands, within wait.
    fn start(mut command: Command, wait: Duration) -> Result<Server, String> {
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| format!("npx didn't start ({e}): Node is needed on the PC."))?;
        let job = Job::new();
        if let Err(e) = job.add(&child) {
            log::warn!("Playwright's server isn't in a job of its own ({e}): what it starts may outlive it");
        }
        let stdin = Arc::new(Mutex::new(child.stdin.take()));
        let (stdout, stderr) = (child.stdout.take().expect("piped"), child.stderr.take().expect("piped"));
        let (answer, answers) = mpsc::channel();
        let answering = stdin.clone();
        thread::spawn(move || {
            for line in BufReader::new(stdout).split(b'\n') {
                let Ok(line) = line else { break };
                let Ok(message) = serde_json::from_slice::<Value>(&line) else { continue };
                if let Some(method) = message["method"].as_str() {
                    // Its own requests: pings are answered, anything else isn't something Natro does.
                    // Its notifications need nothing.
                    if !message["id"].is_null() {
                        let reply = if method == "ping" {
                            json!({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                        } else {
                            json!({"jsonrpc": "2.0", "id": message["id"],
                                   "error": {"code": -32601, "message": format!("Method not found: {method}")}})
                        };
                        write(&answering, &reply);
                    }
                } else if answer.send(Some(message)).is_err() {
                    return;
                }
            }
            let _ = answer.send(None); // it ended
        });
        let said = Arc::new(Mutex::new(String::new()));
        let (last, (ended, said_all)) = (said.clone(), mpsc::channel());
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                log::info!("playwright: {line}");
                if !line.trim().is_empty() {
                    *last.lock().unwrap() = line;
                }
            }
            let _ = ended.send(());
        });
        let mut server = Server { child, job, stdin, answers, said, said_all, next: 1 };
        // The mcp package's name for itself, as before: the extension shows it in his Edge.
        let hello = json!({"protocolVersion": PROTOCOL, "capabilities": {},
                           "clientInfo": {"name": "mcp", "version": "0.1.0"}});
        let started = server
            .request("initialize", hello, wait)
            .and_then(|_| server.send(json!({"jsonrpc": "2.0", "method": "notifications/initialized"})));
        match started {
            Ok(()) => Ok(server),
            Err(Failure::Server(problem) | Failure::Tool(problem)) => {
                server.stop();
                Err(problem)
            }
        }
    }

    fn send(&self, message: Value) -> Result<(), Failure> {
        if write(&self.stdin, &message) { Ok(()) } else { Err(self.stopped()) }
    }

    /// A request's result, within wait.
    fn request(&mut self, method: &str, params: Value, wait: Duration) -> Result<Value, Failure> {
        let id = self.next;
        self.next += 1;
        self.send(json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}))?;
        let until = Instant::now() + wait;
        loop {
            match self.answers.recv_timeout(until.saturating_duration_since(Instant::now())) {
                Ok(Some(answer)) if answer["id"] == id => {
                    return match answer.get("error") {
                        Some(error) => {
                            let message = error["message"].as_str().map_or_else(|| error.to_string(), String::from);
                            Err(Failure::Server(format!("it answered {method} with an error: {message}")))
                        }
                        None => Ok(answer["result"].clone()),
                    };
                }
                Ok(Some(_)) => {} // an answer to a request given up on
                Ok(None) | Err(RecvTimeoutError::Disconnected) => return Err(self.stopped()),
                Err(RecvTimeoutError::Timeout) => {
                    return Err(Failure::Server(format!("it didn't answer within {} seconds.", wait.as_secs())));
                }
            }
        }
    }

    fn stopped(&self) -> Failure {
        // What it said last may still be on its way.
        let _ = self.said_all.recv_timeout(Duration::from_millis(500));
        match self.said.lock().unwrap().as_str() {
            "" => Failure::Server("it stopped.".into()),
            said => Failure::Server(format!("it stopped ({said}).")),
        }
    }

    /// Close its stdin, give it QUIT to end, then end everything in its job.
    fn stop(mut self) {
        self.stdin.lock().unwrap().take();
        let until = Instant::now() + QUIT;
        while matches!(self.child.try_wait(), Ok(None)) && Instant::now() < until {
            thread::sleep(Duration::from_millis(50));
        }
        self.job.end();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Write one message, a line; false once its stdin is closed or broken.
fn write(stdin: &Mutex<Option<ChildStdin>>, message: &Value) -> bool {
    let mut stdin = stdin.lock().unwrap();
    let Some(pipe) = stdin.as_mut() else { return false };
    let ok = writeln!(pipe, "{message}").and_then(|_| pipe.flush()).is_ok();
    if !ok {
        *stdin = None;
    }
    ok
}

/// A job object that ends its processes when it's closed (here, or by Natro's exit).
struct Job(Option<HANDLE>);

// The handle is only used to add a process and to end them all.
unsafe impl Send for Job {}

impl Job {
    fn new() -> Job {
        let created = unsafe { CreateJobObjectW(None, PCWSTR::null()) };
        let Ok(job) = created else {
            return Job(None);
        };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = unsafe {
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const c_void,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if set.is_err() {
            unsafe {
                let _ = CloseHandle(job);
            }
            return Job(None);
        }
        Job(Some(job))
    }

    fn add(&self, child: &Child) -> Result<(), String> {
        let job = self.0.ok_or("no job object")?;
        unsafe { AssignProcessToJobObject(job, HANDLE(child.as_raw_handle())) }.map_err(|e| e.to_string())
    }

    fn end(&mut self) {
        if let Some(job) = self.0.take() {
            unsafe {
                let _ = TerminateJobObject(job, 1);
                let _ = CloseHandle(job);
            }
        }
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        self.end();
    }
}

/// A program on the PATH, with PATHEXT's extensions (npx is npx.cmd), as Python's shutil.which.
pub fn which(name: &str) -> Option<PathBuf> {
    let extensions = std::env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into());
    std::env::split_paths(&std::env::var_os("PATH")?).find_map(|dir| {
        extensions
            .split(';')
            .filter(|extension| !extension.is_empty())
            .map(|extension| dir.join(format!("{name}{extension}")))
            .find(|path| path.is_file())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// A stand-in MCP server: PowerShell reading requests and answering them as Playwright's
    /// would, with a ping of its own first, which must be answered before it goes on.
    const FAKE: &str = r#"
$in = [Console]::In
function Say($line) { [Console]::Out.WriteLine($line); [Console]::Out.Flush() }
$hello = $in.ReadLine() | ConvertFrom-Json
Say ('{"jsonrpc":"2.0","method":"notifications/message","params":{"level":"info","data":"hi"}}')
Say ('{"jsonrpc":"2.0","id":"p1","method":"ping"}')
$pong = $in.ReadLine() | ConvertFrom-Json
if ($pong.id -ne 'p1') { exit 3 }
Say ('{"jsonrpc":"2.0","id":' + $hello.id + ',"result":{"protocolVersion":"2025-11-25","capabilities":{},"serverInfo":{"name":"fake","version":"1"}}}')
$initialized = $in.ReadLine()
while ($null -ne ($line = $in.ReadLine())) {
    $request = $line | ConvertFrom-Json
    $name = $request.params.name
    if ($name -eq 'quit') { [Console]::Error.WriteLine('Error: the browser went away'); exit 1 }
    if ($name -eq 'slow') { Start-Sleep 5 }
    $failed = if ($name -eq 'broken') { 'true' } else { 'false' }
    $text = ('Ran ' + $name + ' at ' + $request.params.arguments.url) | ConvertTo-Json
    Say ('{"jsonrpc":"2.0","id":' + $request.id + ',"result":{"content":[{"type":"text","text":' + $text + '},{"type":"image","data":""}],"isError":' + $failed + '}}')
}
"#;

    fn fake(wait: Duration) -> Result<Server, String> {
        static MADE: AtomicUsize = AtomicUsize::new(0);
        let made = MADE.fetch_add(1, Ordering::SeqCst);
        let script = std::env::temp_dir().join(format!("natro-fake-mcp-{}-{made}.ps1", std::process::id()));
        std::fs::write(&script, FAKE).unwrap();
        let mut command = Command::new("powershell.exe");
        command.args(["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]).arg(script);
        Server::start(command, wait)
    }

    fn call(server: &mut Server, name: &str, wait: Duration) -> Result<Value, Failure> {
        server.request("tools/call", json!({"name": name, "arguments": {"url": "https://example.com"}}), wait)
    }

    fn problem(failure: Failure) -> String {
        match failure {
            Failure::Tool(text) => format!("tool: {text}"),
            Failure::Server(text) => format!("server: {text}"),
        }
    }

    #[test]
    fn an_mcp_server_on_stdio() {
        let mut server = fake(Duration::from_secs(30)).unwrap();
        let answer = call(&mut server, "browser_navigate", CALL).map_err(problem).unwrap();
        assert_eq!(answer["content"][0]["text"], "Ran browser_navigate at https://example.com");
        let slow = call(&mut server, "slow", Duration::from_millis(300)).map_err(problem).unwrap_err();
        assert_eq!(slow, "server: it didn't answer within 0 seconds.");
        // The late answer to the call given up on is passed over.
        assert_eq!(call(&mut server, "next", CALL).map_err(problem).unwrap()["content"][0]["text"], "Ran next at https://example.com");
        let gone = call(&mut server, "quit", CALL).map_err(problem).unwrap_err();
        assert_eq!(gone, "server: it stopped (Error: the browser went away).");
        server.stop();
    }

    #[test]
    fn a_server_that_doesnt_start() {
        let missing = Server::start(Command::new("no-such-npx"), START).err();
        assert!(missing.unwrap().starts_with("npx didn't start ("));
        let mut silent = Command::new("powershell.exe");
        silent.args(["-NoProfile", "-Command", "Start-Sleep 30"]);
        let started = Instant::now();
        let quiet = Server::start(silent, Duration::from_millis(500)).err();
        assert_eq!(quiet.unwrap(), "it didn't answer within 0 seconds.");
        assert!(started.elapsed() < Duration::from_secs(10), "stopped at once, with its job");
    }

    #[test]
    fn tool_results_and_failures() {
        let playwright = Playwright::new(Vec::new(), Vec::new());
        let mut running = Some(fake(Duration::from_secs(30)).unwrap());
        let ok = playwright.try_call(&mut running, "browser_snapshot", &json!({"url": "x"}));
        assert_eq!(ok.map_err(problem).unwrap(), "Ran browser_snapshot at x");
        let refused = playwright.try_call(&mut running, "broken", &json!({"url": "y"}));
        assert_eq!(refused.map_err(problem).unwrap_err(), "tool: Ran broken at y");
        running.take().unwrap().stop();
    }

    #[test]
    fn npx_is_found_as_python_found_it() {
        let found = which("powershell").unwrap();
        assert!(found.to_string_lossy().to_lowercase().ends_with(r"windowspowershell\v1.0\powershell.exe"));
        assert_eq!(which("no-such-program-anywhere"), None);
    }
}
