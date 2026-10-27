//! Where Natro's files are, and its settings from .env.
//!
//! As the Python helper (desktop/helper/natro_pc/config.py): `.env`, `models/`, `tokens/`,
//! `logs/` and `settings.json` are in %LOCALAPPDATA%\Natro, or NATRO_HOME. The
//! helper's `.venv/` is in the repository, found above the program.

use std::collections::HashMap;
use std::env;
use std::path::{Path, PathBuf};

pub struct Config {
    pub home: PathBuf,
    /// The repository: the folder with the helper's `.venv`.
    pub repo: PathBuf,
    values: HashMap<String, String>,
}

impl Config {
    pub fn load() -> Config {
        let home = match env::var_os("NATRO_HOME") {
            Some(dir) => PathBuf::from(dir),
            None => env::var_os("LOCALAPPDATA")
                .map(|dir| PathBuf::from(dir).join("Natro"))
                .unwrap_or_else(|| PathBuf::from(".")),
        };
        let repo = find_repo().unwrap_or_else(|| home.clone());
        let text = std::fs::read_to_string(home.join(".env")).unwrap_or_default();
        Config {
            home,
            repo,
            values: parse_env(&text),
        }
    }

    /// A setting: the environment first, then .env.
    pub fn get(&self, key: &str) -> Option<String> {
        env::var(key)
            .ok()
            .or_else(|| self.values.get(key).cloned())
            .filter(|v| !v.is_empty())
    }
}

/// The nearest folder with a .venv, above the program or the current folder.
fn find_repo() -> Option<PathBuf> {
    let starts = [env::current_exe().ok(), env::current_dir().ok()];
    starts.into_iter().flatten().find_map(|start| {
        start
            .ancestors()
            .find(|dir| dir.join(".venv").is_dir())
            .map(Path::to_path_buf)
    })
}

fn parse_env(text: &str) -> HashMap<String, String> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .filter_map(|line| line.split_once('='))
        .map(|(key, value)| (key.trim().to_string(), value.trim().to_string()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_lines() {
        let values = parse_env("# comment\nNATRO_SERVER = ws://1.2.3.4:8700\n\nEMPTY=\nA=b=c\n");
        assert_eq!(values["NATRO_SERVER"], "ws://1.2.3.4:8700");
        assert_eq!(values["A"], "b=c");
        assert_eq!(values["EMPTY"], "");
        assert_eq!(values.len(), 3);
    }
}
