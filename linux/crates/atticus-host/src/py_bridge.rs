//! Bridge to `atticus.py`: spawning one-shot RPCs, streaming `--wait`, persistent `--agents`,
//! `--dictate-cleanup-wait`, and `--meeting-live`.

use crate::prefs::resources_dir;
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

pub fn atticus_py_path() -> PathBuf {
    resources_dir().join("atticus.py")
}

/// Build a `Command` that invokes `atticus.py` with `ATTICUS_APP` pointing at our binary.
pub fn py_command(args: &[&str], extra_env: &HashMap<String, String>) -> Command {
    let mut cmd = Command::new("/bin/sh");
    cmd.arg(atticus_py_path());
    for a in args {
        cmd.arg(a);
    }
    if let Ok(exe) = std::env::current_exe() {
        cmd.env("ATTICUS_APP", exe);
    }
    cmd.env("ATTICUS_PLATFORM", "linux");
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    cmd
}

/// Run a short one-shot `atticus.py` RPC and return stdout.
pub fn py_run(args: &[&str], stdin_data: Option<&str>) -> std::io::Result<(i32, String, String)> {
    let mut cmd = py_command(args, &HashMap::new());
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    if stdin_data.is_some() {
        cmd.stdin(Stdio::piped());
    } else {
        cmd.stdin(Stdio::null());
    }
    let mut child = cmd.spawn()?;
    if let Some(input) = stdin_data {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(input.as_bytes());
        }
    }
    let out = child.wait_with_output()?;
    Ok((
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    ))
}

/// Parsed streaming line from `atticus.py --wait` stdout protocol.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AskEvent {
    Note(String),        // "· "
    Step(String),        // "▸ "
    Partial(String),     // "… "
    Speak(String),       // "♬ "
    SpeakCancel,         // "♬!"
    Answer(String),      // "✓ "
    Error(String),       // "! "
    Tip(String),         // "i "
    Preview(String),     // "▣ " JSON
    Point(String),       // "@ " x y [answer]
    DrawOrMemory(String),// "✎ " JSON (dict=drawing, list=memory)
    Walkthrough(String), // "☷ " JSON
    MoveReady(String),   // "⇢ " JSON
    Handoff(String),     // "⤴ " JSON
    Replace(String),     // "↺ " JSON
    Setting(String),     // "⚙ " key val
    AgentCard(String),   // "▤ " id
    AgentOpen(String),   // "▥ " id
    Picker(String),      // "◇ " JSON
    Upgrade(String),     // "♧ " JSON
    Approval(String),    // "? " tab-separated
    Raw(String),
}

pub fn parse_ask_line(line: &str) -> AskEvent {
    let unescaped = line.replace('\u{2028}', "\n");
    if let Some(rest) = unescaped.strip_prefix("· ") {
        AskEvent::Note(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("▸ ") {
        AskEvent::Step(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("… ") {
        AskEvent::Partial(rest.to_string())
    } else if unescaped.starts_with("♬!") {
        AskEvent::SpeakCancel
    } else if let Some(rest) = unescaped.strip_prefix("♬ ") {
        AskEvent::Speak(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("✓ ") {
        AskEvent::Answer(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("! ") {
        AskEvent::Error(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("i ") {
        AskEvent::Tip(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("▣ ") {
        AskEvent::Preview(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("@ ") {
        AskEvent::Point(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("✎ ") {
        AskEvent::DrawOrMemory(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("☷ ") {
        AskEvent::Walkthrough(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("⇢ ") {
        AskEvent::MoveReady(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("⤴ ") {
        AskEvent::Handoff(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("↺ ") {
        AskEvent::Replace(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("⚙ ") {
        AskEvent::Setting(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("▤ ") {
        AskEvent::AgentCard(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("▥ ") {
        AskEvent::AgentOpen(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("◇ ") {
        AskEvent::Picker(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("♧ ") {
        AskEvent::Upgrade(rest.to_string())
    } else if let Some(rest) = unescaped.strip_prefix("? ") {
        AskEvent::Approval(rest.to_string())
    } else {
        AskEvent::Raw(unescaped)
    }
}

/// Persistent handle to `atticus.py --agents`.
pub struct AgentsDaemon {
    child: Child,
}

impl AgentsDaemon {
    pub fn spawn<F>(mut on_event: F) -> std::io::Result<Self>
    where
        F: FnMut(Value) + Send + 'static,
    {
        let mut cmd = py_command(&["--agents"], &HashMap::new());
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child = cmd.spawn()?;
        if let Some(stdout) = child.stdout.take() {
            std::thread::spawn(move || {
                let reader = BufReader::new(stdout);
                for line in reader.lines().map_while(Result::ok) {
                    if let Ok(val) = serde_json::from_str::<Value>(&line) {
                        on_event(val);
                    }
                }
            });
        }
        Ok(Self { child })
    }

    pub fn send(&mut self, payload: &Value) -> std::io::Result<()> {
        if let Some(stdin) = self.child.stdin.as_mut() {
            let line = serde_json::to_string(payload)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
            writeln!(stdin, "{line}")?;
            stdin.flush()?;
        }
        Ok(())
    }

    pub fn stop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub fn fire_telemetry(args: &[&str], enabled: bool) {
    if !enabled {
        return;
    }
    let mut full = vec!["--telemetry"];
    full.extend_from_slice(args);
    let mut cmd = py_command(&full, &HashMap::new());
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let _ = cmd.spawn();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_protocol_prefixes_and_newlines() {
        assert_eq!(parse_ask_line("✓ Line 1\u{2028}Line 2"), AskEvent::Answer("Line 1\nLine 2".into()));
        assert_eq!(parse_ask_line("▸ Searching web"), AskEvent::Step("Searching web".into()));
        assert_eq!(parse_ask_line("♬! "), AskEvent::SpeakCancel);
        assert_eq!(parse_ask_line("✎ {\"steps\":[]}"), AskEvent::DrawOrMemory("{\"steps\":[]}".into()));
    }
}
