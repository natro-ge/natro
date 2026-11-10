//! Where Natro's files are, and its settings from .env.
//!
//! `.env`, `models/`, `tokens/`, `logs/` and `settings.json` are in %LOCALAPPDATA%\Natro, or
//! NATRO_HOME.

use std::collections::HashMap;
use std::env;
use std::path::PathBuf;

pub struct Config {
    pub home: PathBuf,
    values: HashMap<String, String>,
    /// Why Natro's files couldn't be read ("no folder at ..."), if they couldn't: the app then
    /// runs on its defaults, with no brain to reach and no voice (lib.rs and the tray say so).
    pub problem: Option<String>,
}

impl Config {
    pub fn load() -> Config {
        let home = match env::var_os("NATRO_HOME") {
            Some(dir) => PathBuf::from(dir),
            None => env::var_os("LOCALAPPDATA")
                .map(|dir| PathBuf::from(dir).join("Natro"))
                .unwrap_or_else(|| PathBuf::from(".")),
        };
        Config::at(home)
    }

    fn at(home: PathBuf) -> Config {
        let env_file = home.join(".env");
        let (text, problem) = match std::fs::read_to_string(&env_file) {
            Ok(text) => (text, None),
            Err(_) if !home.is_dir() => (String::new(), Some(format!("no folder at {}", home.display()))),
            Err(e) => (String::new(), Some(format!("{} isn't readable ({e})", env_file.display()))),
        };
        Config {
            home,
            values: parse_env(&text),
            problem,
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

    #[test]
    fn missing_files_are_a_problem() {
        let dir = env::temp_dir().join(format!("natro-config-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let config = Config::at(dir.clone());
        assert_eq!(config.problem, Some(format!("no folder at {}", dir.display())));
        assert_eq!(config.get("NATRO_CONFIG_TEST"), None);

        std::fs::create_dir_all(&dir).unwrap();
        let config = Config::at(dir.clone());
        let problem = config.problem.unwrap();
        assert!(problem.starts_with(&format!("{} isn't readable", dir.join(".env").display())), "{problem}");

        std::fs::write(dir.join(".env"), "NATRO_CONFIG_TEST=abc\n").unwrap();
        let config = Config::at(dir.clone());
        assert_eq!(config.problem, None);
        assert_eq!(config.get("NATRO_CONFIG_TEST").as_deref(), Some("abc"));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
