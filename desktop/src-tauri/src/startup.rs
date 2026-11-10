//! Starting with Windows: Settings' "Start with Windows" switch gives Natro a task in Task
//! Scheduler ("Natro", in its top folder) that starts her when the user signs in, or takes it
//! out. A task rather than the Run key (HKCU\Software\Microsoft\Windows\CurrentVersion\Run),
//! which she used before: Explorer starts the Run key's programs one at a time, waiting up to
//! 30 s for each, and Natro, the last of five, started 2 min 40 s after sign-in (2026-10-30).
//! The task starts her as the user signs in. Turning it on takes the Run key's value out, so
//! she isn't started twice.
//!
//! Windows starts her in another folder, which doesn't matter: Natro finds its files from where
//! the program is (config.rs).
//!
//! A debug build loads its pages from `npm run dev`, which isn't running at sign-in, so it
//! puts the release build beside it (target/release) in the task instead, once there is one.

use std::path::{Path, PathBuf};

use windows::Win32::Foundation::ERROR_FILE_NOT_FOUND;
use windows::Win32::System::Com::{CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize};
use windows::Win32::System::Registry::{HKEY_CURRENT_USER, RegDeleteKeyValueW};
use windows::Win32::System::TaskScheduler::{ITaskService, TASK_CREATE_OR_UPDATE, TASK_LOGON_INTERACTIVE_TOKEN, TaskScheduler};
use windows::Win32::System::Variant::VARIANT;
use windows::core::{BSTR, PCWSTR, w};

const TASK: &str = "Natro";
const RUN: PCWSTR = w!(r"Software\Microsoft\Windows\CurrentVersion\Run");
const RUN_NAME: PCWSTR = w!("Natro");

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
    let task = if on {
        let program = program().map_err(|e| e.to_string())?;
        Some(task(&program, &user()?))
    } else {
        None
    };
    scheduled(task.as_deref()).map_err(|e| e.message())?;
    // The way before the task (see above).
    match unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, RUN, RUN_NAME) } {
        result if result == ERROR_FILE_NOT_FOUND => Ok(()), // not there already
        result => result.ok().map_err(|e| e.message()),
    }
}

/// Puts the task (its XML) in Task Scheduler, or takes it out (none).
fn scheduled(task: Option<&str>) -> windows::core::Result<()> {
    unsafe {
        // Whichever thread asks; the main one has COM already.
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok();
        let result = (|| {
            let service: ITaskService = CoCreateInstance(&TaskScheduler, None, CLSCTX_INPROC_SERVER)?;
            let none = VARIANT::default();
            service.Connect(&none, &none, &none, &none)?;
            let folder = service.GetFolder(&BSTR::from(r"\"))?;
            match task {
                Some(xml) => folder
                    .RegisterTask(
                        &BSTR::from(TASK),
                        &BSTR::from(xml),
                        TASK_CREATE_OR_UPDATE.0,
                        &none,
                        &none,
                        TASK_LOGON_INTERACTIVE_TOKEN,
                        &none,
                    )
                    .map(drop),
                None => match folder.DeleteTask(&BSTR::from(TASK), 0) {
                    Err(e) if e.code() == ERROR_FILE_NOT_FOUND.to_hresult() => Ok(()), // not there already
                    result => result,
                },
            }
        })();
        if com {
            CoUninitialize();
        }
        result
    }
}

/// The one signed in, as Task Scheduler names them (DOMAIN\name). The task starts at their
/// sign-in alone, which needs no administrator.
fn user() -> Result<String, String> {
    match (std::env::var("USERDOMAIN"), std::env::var("USERNAME")) {
        (Ok(domain), Ok(name)) => Ok(format!(r"{domain}\{name}")),
        _ => Err("Windows didn't say who's signed in (no USERDOMAIN or USERNAME)".into()),
    }
}

/// The task: the program at the user's sign-in, as them. On battery too, for as long as it
/// runs (a task's own are only on mains power, stopped after 3 days), at normal priority (a
/// task's own is below normal, which would slow her down).
fn task(program: &Path, user: &str) -> String {
    let program = escape(&program.display().to_string());
    let user = escape(user);
    format!(
        r#"<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts Natro when you sign in. Natro's Settings, Start with Windows, turns it off.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{program}</Command>
    </Exec>
  </Actions>
</Task>"#
    )
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_task_starts_the_program_at_the_users_sign_in() {
        let xml = task(Path::new(r"C:\Tom & Jerry\natro.exe"), r"PC\Tom");
        assert!(xml.contains(r"<Command>C:\Tom &amp; Jerry\natro.exe</Command>"));
        assert!(xml.contains("<LogonTrigger>\n      <UserId>PC\\Tom</UserId>"));
        assert!(xml.contains("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"));
        assert!(xml.contains("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>"));
    }
}
