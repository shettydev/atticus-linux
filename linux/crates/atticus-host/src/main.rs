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

use std::sync::Arc;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let open_settings = args.first().map(|s| s.as_str()) == Some("--settings");

    if !open_settings {
        if let Some(code) = cli::dispatch_cli(&args) {
            std::process::exit(code);
        }
    }

    single_instance::redirect_stderr_if_detached();
    let Some(_lock) = single_instance::InstanceLock::acquire() else {
        // Another instance is already running
        std::process::exit(0);
    };

    let prefs = Arc::new(prefs::Prefs::load());
    let code = gui::run_gui_daemon(prefs, open_settings);
    std::process::exit(code);
}
