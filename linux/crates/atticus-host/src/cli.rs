//! CLI subcommand router for `atticus-host` (handles all 16+ subcommands called by `atticus.py`
//! and system helpers before acquiring the GUI single-instance lock).

use crate::{a11y, audio, calendar, capture, contacts, dictation_data, notify, spoken, tts, wm};
use serde_json::json;

/// Returns `Some(exit_code)` if CLI arguments matched a subcommand, or `None` to launch GUI daemon.
pub fn dispatch_cli(args: &[String]) -> Option<i32> {
    let flag = args.first()?.as_str();
    match flag {
        "--version" => {
            println!("atticus-host {}", crate::updater::current_version());
            Some(0)
        }
        "--help" | "-h" => {
            println!(
                "Atticus Linux host daemon & CLI helper.\n\
                 Run with no arguments to start the desktop daemon.\n\
                 Subcommands: --screens [--visible] | --place <app> <where> <screen> [title] |\n\
                 --window-target <app> [title] | --target-app <app> | --user-app | --front-window |\n\
                 --focus <app> | --quit-app <app> | --system-ui | --draw-target | --raise [user] |\n\
                 --hands <verb> ... | --type | --look <n> <out.jpg> | --step-screen |\n\
                 --guide-watch <json> <cancel> | --text <app> [title] | --calendar | --contacts |\n\
                 --profile-import | --notify <msg> | --speak <voice> <text> |\n\
                 --restore-audio <snap> <pid> | --settings | --selftest"
            );
            Some(0)
        }
        "--screens" => {
            let visible = args.iter().any(|a| a == "--visible");
            let backend = wm::detect_backend();
            let rows: Vec<_> = backend
                .displays()
                .into_iter()
                .map(|d| {
                    if visible {
                        json!([d.name, d.visible_x, d.visible_y, d.visible_width, d.visible_height, d.built_in])
                    } else {
                        json!([d.name, d.x, d.y, d.width, d.height, d.built_in])
                    }
                })
                .collect();
            println!("{}", serde_json::to_string(&rows).unwrap_or_else(|_| "[]".into()));
            Some(0)
        }
        "--place" => {
            let proc_name = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let where_ = args.get(2).map(|s| s.as_str()).unwrap_or("full");
            let screen = args.get(3).map(|s| s.as_str()).unwrap_or("current");
            let title = args.get(4).map(|s| s.as_str()).unwrap_or("");
            let backend = wm::detect_backend();
            println!("{}", wm::place_window(backend.as_ref(), proc_name, where_, screen, title));
            Some(0)
        }
        "--window-target" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let title = args.get(2).map(|s| s.as_str()).unwrap_or("");
            let backend = wm::detect_backend();
            let wins = backend.windows();
            if let Some(w) = wm::find_window(&wins, app, title) {
                let visible = wm::is_window_visible(&w, &wins);
                println!("{}", w.to_target_json(visible));
                Some(0)
            } else {
                println!("{{}}");
                Some(1)
            }
        }
        "--target-app" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let title = args.get(2).map(|s| s.as_str()).unwrap_or("");
            let backend = wm::detect_backend();
            let wins = backend.windows();
            if let Some(w) = wm::find_window(&wins, app, title) {
                println!("{}", w.app);
                Some(0)
            } else {
                Some(1)
            }
        }
        "--user-app" => {
            let backend = wm::detect_backend();
            if let Some(w) = backend.active_window() {
                if !w.wm_class.to_lowercase().contains("atticus") {
                    println!("{}", w.app);
                    return Some(0);
                }
            }
            Some(0)
        }
        "--front-window" => {
            let backend = wm::detect_backend();
            if let Some(w) = backend.active_window() {
                println!("{}", json!({"app": w.app, "window": w.title, "pid": w.pid}));
            } else {
                println!("{}", json!({"app": "", "window": "", "pid": 0}));
            }
            Some(0)
        }
        "--focus" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let backend = wm::detect_backend();
            let wins = backend.windows();
            if let Some(w) = wm::find_window(&wins, app, "") {
                Some(if backend.focus_window(&w).is_ok() { 0 } else { 1 })
            } else {
                Some(1)
            }
        }
        "--quit-app" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let backend = wm::detect_backend();
            let wins = backend.windows();
            if let Some(w) = wm::find_window(&wins, app, "") {
                Some(if backend.close_window(&w).is_ok() { 0 } else { 1 })
            } else {
                Some(0)
            }
        }
        "--system-ui" => {
            println!("{{}}");
            Some(0)
        }
        "--draw-target" => {
            let backend = wm::detect_backend();
            let wins = backend.windows();
            if let Some(w) = backend.active_window().or_else(|| wins.first().cloned()) {
                println!("{}", w.to_target_json(true));
                Some(0)
            } else {
                println!("{{}}");
                Some(1)
            }
        }
        "--raise" => {
            // Signal focus guard pause via marker timestamp in ~/.config/atticus/raise.ts
            let p = crate::prefs::atticus_dir().join("raise.ts");
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            let _ = std::fs::write(p, format!("{now:.3}"));
            Some(0)
        }
        "--hands" => Some(a11y::run_hands_cli(&args[1..])),
        "--type" => Some(a11y::run_type_cli()),
        "--look" => Some(capture::run_look_cli(&args[1..])),
        "--step-screen" => Some(capture::run_step_screen_cli()),
        "--guide-watch" => Some(capture::run_guide_watch_cli(&args[1..])),
        "--text" => Some(capture::run_text_ocr_cli(&args[1..])),
        "--calendar" => Some(calendar::run_calendar_cli()),
        "--contacts" => Some(contacts::run_contacts_cli()),
        "--profile-import" => Some(contacts::run_profile_import_cli()),
        "--notify" => {
            let msg = args[1..].join(" ");
            Some(if notify::send_notification("Atticus", &msg) { 0 } else { 1 })
        }
        "--speak" => {
            let voice = args.get(1).map(|s| s.as_str()).unwrap_or("default");
            let text = if args.len() > 2 { args[2..].join(" ") } else { String::new() };
            Some(if tts::speak_blocking(&text, voice, 196.0) { 0 } else { 1 })
        }
        "--restore-audio" => {
            let snap = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let pid = args.get(2).and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
            Some(audio::run_restore_audio_watchdog(snap, pid))
        }
        "--selftest" => {
            assert_eq!(a11y::mask_secrets("123-45-6789"), "[redacted]");
            assert_eq!(dictation_data::detect_correction("open attic us", "open Atticus"), Some(("attic us".into(), "Atticus".into())));
            assert!(spoken::read_aloud_text("Check $x^2$").contains("squared"));
            println!("atticus-host selftest: PASS");
            Some(0)
        }
        _ => None,
    }
}
