//! Atticus Linux native host daemon & CLI helper binary.
#![allow(dead_code)]

mod a11y;
mod audio;
mod calendar;
mod capture;
mod cli;
mod contacts;
mod dictation;
mod dictation_data;
mod gui;
mod hotkeys;
mod notify;
mod overlay;
mod prefs;
mod py_bridge;
mod single_instance;
mod spoken;
mod stt;
mod tray;
mod tts;
mod updater;
mod wake;
mod wm;

use std::io::Write;
use std::os::unix::net::UnixStream;
use std::sync::Arc;
use std::time::Duration;

fn send_control_command(cmd: &str) -> bool {
    let sock = prefs::atticus_dir().join("control.sock");
    let Ok(mut stream) = UnixStream::connect(sock) else {
        return false;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    writeln!(stream, "{cmd}").is_ok()
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let first = args.first().map(|s| s.as_str());

    let startup_action = match first {
        Some("--ask") => Some("ask"),
        Some("--toggle") => Some("toggle"),
        Some("--dictate") => Some("dictate"),
        Some("--settings") => Some("settings"),
        Some("--onboarding") => Some("onboarding"),
        Some("--agents") => Some("agents"),
        Some("--meeting") => Some("meeting"),
        Some("--quit") => Some("quit"),
        _ => None,
    };

    if let Some(action) = startup_action {
        // Forward to already-running daemon if present
        if send_control_command(action) {
            std::process::exit(0);
        }
        if action == "quit" {
            std::process::exit(0);
        }
    } else if let Some(code) = cli::dispatch_cli(&args) {
        std::process::exit(code);
    }

    single_instance::redirect_stderr_if_detached();
    let Some(_lock) = single_instance::InstanceLock::acquire() else {
        if let Some(action) = startup_action {
            let _ = send_control_command(action);
        }
        std::process::exit(0);
    };

    let prefs = Arc::new(prefs::Prefs::load());
    let code = gui::run_gui_daemon(prefs, startup_action);
    std::process::exit(code);
}
