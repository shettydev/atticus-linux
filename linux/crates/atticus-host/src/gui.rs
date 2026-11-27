//! GTK4 + WebKitGTK6 + optional `gtk4-layer-shell` GUI daemon hosting all Atticus surfaces:
//! - Starts quietly in the background (System Tray + `~/.config/atticus/control.sock` + `atticus-inputd`)
//!   with NO stuck window at startup.
//! - Main Ask Box / Dictation Pill (`ui.html`, handlers `h` & `c`): transparent GTK4 overlay anchored
//!   at top-center on Wayland layer-shell, closable via Esc, `✕`, or `atticus-host --toggle`.
//! - Settings Window (`settings.html`, handler `st`), Onboarding (`onboarding.html`, handler `ob`),
//!   Agents & Chats (`agents.html`, handler `agents`), Meeting Copilot (`meeting.html`, handler `meeting`).

use crate::a11y;
use crate::audio;
use crate::dictation_data::DictationArchive;
use crate::prefs::{atticus_dir, Prefs};
use crate::py_bridge::{py_run, AskEvent};
#[cfg(feature = "gui")]
use crate::py_bridge::{parse_ask_line, py_command};
use crate::stt;
use crate::tts;
use crate::updater;
use serde_json::{json, Value};
#[cfg(feature = "gui")]
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader};
#[cfg(feature = "gui")]
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
#[cfg(feature = "gui")]
use std::path::PathBuf;
use std::process::Command;
#[cfg(feature = "gui")]
use std::process::{Child, Stdio};
use std::sync::{mpsc, Arc};
#[cfg(feature = "gui")]
use std::sync::Mutex;
use std::thread;
#[cfg(feature = "gui")]
use std::time::Duration;

#[derive(Debug)]
pub enum UiCommand {
    ToggleAsk,
    ShowAsk { compose: bool },
    HideAsk,
    StartVoiceTalk,
    StopVoiceTalk,
    StartDictate,
    StopDictate,
    CancelDictate,
    SendText(String),
    AskStreamEvent(AskEvent),
    AskFinished,
    VoiceLevel(f32),
    VoiceTranscribed { text: String, dictate: bool, duration: f64 },
    VoiceError(String),
    DictateDone { text: String, inserted: bool, status: String },
    OpenSettings(Option<String>),
    OpenOnboarding,
    OpenAgents,
    OpenMeeting,
    TogglePause,
    PasteLastDictation,
    SettingsReply(Value),
    OnboardingReply(Value),
    AgentsEvent(Value),
    MeetingReply(Value),
    Quit,
}

/// Compute Linux permission / readiness flags for Onboarding (`ob.set`) and Settings (`st.perms`).
pub fn linux_permissions_json() -> Value {
    let mic = !audio::list_microphones().is_empty()
        || Command::new("which").arg("pw-record").status().map(|s| s.success()).unwrap_or(false)
        || Command::new("which").arg("parec").status().map(|s| s.success()).unwrap_or(false);
    let speech = stt::find_whisper_server_bin().is_some()
        && stt::WHISPER_MODELS.iter().any(|m| stt::whisper_dir().join(m.filename).is_file());
    let screen = Command::new("which").arg("grim").status().map(|s| s.success()).unwrap_or(false)
        || Command::new("which").arg("import").status().map(|s| s.success()).unwrap_or(false)
        || Command::new("which").arg("scrot").status().map(|s| s.success()).unwrap_or(false);
    let ax = a11y::atspi_enabled();
    let chrome = atticus_dir().join("browser.sock").exists();
    json!({
        "mic": mic,
        "speech": speech,
        "screen": screen,
        "ax": ax,
        "auto": true,
        "chrome": chrome,
        "calendar": true,
        "tools": true,
    })
}

/// Build the payload expected by `window.st.load(...)` in `ui/settings.html`.
pub fn build_settings_load_payload(prefs: &Prefs) -> Value {
    let state = py_run(&["--state"], None)
        .ok()
        .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
        .unwrap_or_else(|| json!({}));
    let routines = state.get("routines").cloned().unwrap_or_else(|| json!([]));
    let managed = updater::update_management() != updater::UpdateManagement::Git;
    json!({
        "prefs": prefs.snapshot(),
        "state": state,
        "routines": routines,
        "version": format!("{} ({})", updater::current_version(), updater::current_commit()),
        "managedUpdates": managed,
        "update": "Up to date.",
        "hotkeyTaken": false,
        "dictateHotkeyTaken": false,
    })
}

pub fn build_dictation_payload(prefs: &Prefs) -> Value {
    let archive = DictationArchive::load();
    let now_day = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() / 86400)
        .unwrap_or(0);
    let today_words: usize = archive
        .items
        .iter()
        .filter(|it| (it.ts as u64) / 86400 == now_day)
        .map(|it| it.words)
        .sum();
    let saved_mins = ((archive.total_words as f64 / 40.0) - (archive.total_secs / 60.0)).max(0.0);
    json!({
        "todayWords": today_words,
        "allWords": archive.total_words,
        "savedMinutes": saved_mins.round() as i64,
        "sessions": archive.total_sessions,
        "words": prefs.get("dictateWords").unwrap_or_else(|| json!([])),
        "history": archive.items,
    })
}

/// Dispatch a `st` (Settings) WebKit message handler payload — shared by GUI and headless tests.
pub fn handle_settings_message(prefs: &Prefs, msg: &Value) -> Option<Value> {
    let action = msg["do"].as_str()?;
    match action {
        "set" => {
            if let Some(k) = msg["k"].as_str() {
                prefs.set(k, msg["v"].clone());
                if k == "login" {
                    let on = msg["v"].as_bool().unwrap_or(true);
                    let op = if on { "enable" } else { "disable" };
                    let _ = Command::new("systemctl")
                        .args(["--user", op, "atticus.service"])
                        .status();
                }
            }
            Some(json!({"fn": "st.state", "args": [prefs.snapshot()]}))
        }
        "localServers" => {
            let found = py_run(&["--local-servers"], None)
                .ok()
                .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                .unwrap_or_else(|| json!([]));
            Some(json!({"fn": "st.local", "args": [found]}))
        }
        "models" => {
            let base = prefs.get_str("apiBase", "");
            let key = prefs.get_str("apiKey", "");
            let list = py_run(&["--models"], None)
                .ok()
                .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                .unwrap_or_else(|| json!([]));
            Some(json!({"fn": "st.models", "args": [{"base": base, "key": key, "list": list}]}))
        }
        "connectors" => {
            let found = py_run(&["--connectors"], None)
                .ok()
                .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                .unwrap_or_else(|| json!({"connected": [], "signin": []}));
            Some(json!({"fn": "st.connectors", "args": [found]}))
        }
        "whisperDelete" => {
            if let Some(id) = msg["id"].as_str() {
                stt::delete_model(id);
            }
            Some(json!({"fn": "st.whisper", "args": [stt::whisper_status_json()]}))
        }
        "dictationWord" => {
            if let Some(word) = msg["word"].as_str().map(|s| s.trim()).filter(|s| !s.is_empty()) {
                let mut list: Vec<String> = prefs
                    .get("dictateWords")
                    .and_then(|v| serde_json::from_value(v).ok())
                    .unwrap_or_default();
                if let Some(old) = msg["old"].as_str() {
                    list.retain(|w| w != old);
                }
                if !list.iter().any(|w| w == word) {
                    list.push(word.to_string());
                }
                prefs.set("dictateWords", json!(list));
            }
            Some(json!({"fn": "st.dictation", "args": [build_dictation_payload(prefs)]}))
        }
        "dictationWordDelete" => {
            if let Some(word) = msg["word"].as_str() {
                let mut list: Vec<String> = prefs
                    .get("dictateWords")
                    .and_then(|v| serde_json::from_value(v).ok())
                    .unwrap_or_default();
                list.retain(|w| w != word);
                prefs.set("dictateWords", json!(list));
            }
            Some(json!({"fn": "st.dictation", "args": [build_dictation_payload(prefs)]}))
        }
        "dictationDelete" => {
            if let Some(id) = msg["id"].as_str() {
                let mut a = DictationArchive::load();
                a.delete_item(id);
            }
            Some(json!({"fn": "st.dictation", "args": [build_dictation_payload(prefs)]}))
        }
        "dictationClear" => {
            let mut a = DictationArchive::load();
            a.clear_history();
            Some(json!({"fn": "st.dictation", "args": [build_dictation_payload(prefs)]}))
        }
        "routineRemove" => {
            if let Some(id) = msg["id"].as_str() {
                let _ = py_run(&["--routine", "remove", id], None);
            }
            Some(json!({"fn": "st.load", "args": [build_settings_load_payload(prefs)]}))
        }
        "remember" => {
            if let Some(line) = msg["line"].as_str() {
                let _ = py_run(&["--remember", line], None);
            }
            Some(json!({"fn": "st.load", "args": [build_settings_load_payload(prefs)]}))
        }
        "forget" => {
            if let Some(line) = msg["line"].as_str() {
                let _ = py_run(&["--forget", line], None);
            }
            Some(json!({"fn": "st.load", "args": [build_settings_load_payload(prefs)]}))
        }
        "unlearn" => {
            if let Some(say) = msg["say"].as_str() {
                let _ = py_run(&["--unlearn", say], None);
            }
            Some(json!({"fn": "st.load", "args": [build_settings_load_payload(prefs)]}))
        }
        "meetings" => {
            let q = msg["query"].as_str().unwrap_or("");
            let req = json!({"action": "list", "query": q}).to_string();
            let found = py_run(&["--meetings"], Some(&req))
                .ok()
                .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                .unwrap_or_else(|| json!({"meetings": [], "query": q}));
            Some(json!({"fn": "st.meetings", "args": [found]}))
        }
        "meetingOpen" => {
            if let Some(id) = msg["id"].as_str() {
                let req = json!({"action": "get", "id": id}).to_string();
                if let Ok((0, out, _)) = py_run(&["--meetings"], Some(&req)) {
                    if let Ok(v) = serde_json::from_str::<Value>(&out) {
                        return Some(json!({"fn": "st.meetings", "args": [v]}));
                    }
                }
            }
            None
        }
        "meetingDelete" => {
            if let Some(id) = msg["id"].as_str() {
                let req = json!({"action": "delete", "id": id}).to_string();
                let _ = py_run(&["--meetings"], Some(&req));
                return Some(json!({"fn": "st.meetings", "args": [{"deleted": id}]}));
            }
            None
        }
        "meetingCopy" | "copy" => {
            if let Some(text) = msg["text"].as_str() {
                a11y::write_clipboard(text);
            }
            None
        }
        "feedbackPreview" => {
            let req = json!({
                "include": msg["include"].as_bool().unwrap_or(false),
                "text": msg["text"].as_str().unwrap_or(""),
                "version": format!("{} ({})", updater::current_version(), updater::current_commit()),
            })
            .to_string();
            if let Ok((0, out, _)) = py_run(&["--feedback"], Some(&req)) {
                if let Ok(mut parsed) = serde_json::from_str::<Value>(&out) {
                    parsed["revision"] = msg["revision"].clone();
                    return Some(json!({"fn": "st.feedback", "args": [parsed]}));
                }
            }
            None
        }
        "resetHotkey" => {
            prefs.set("hotkeyCode", json!(57));
            prefs.set("hotkeyMods", json!(3));
            prefs.set("hotkeyLabel", json!("Ctrl+Alt+Space"));
            Some(json!({"fn": "st.load", "args": [build_settings_load_payload(prefs)]}))
        }
        "testVoice" => {
            let text = msg["text"]
                .as_str()
                .unwrap_or("This is how fast I will talk.")
                .to_string();
            let rate = prefs.get_f64("voiceRate", 196.0);
            thread::spawn(move || {
                tts::speak_blocking(&text, "default", rate);
            });
            None
        }
        "preview" => {
            let sound = msg["sound"].as_str().unwrap_or("Glass");
            tts::play_cue(sound, prefs.get_f64("volume", 0.16));
            None
        }
        "github" => {
            let _ = Command::new("xdg-open").arg("https://github.com/SaarthurR/atticus-mac").spawn();
            None
        }
        "kofi" => {
            let _ = Command::new("xdg-open").arg("https://ko-fi.com/saarthr").spawn();
            None
        }
        "quit" => {
            std::process::exit(0);
        }
        _ => None,
    }
}

/// Start control UNIX socket (`~/.config/atticus/control.sock`) so CLI commands like
/// `atticus-host --ask`, `--toggle`, `--settings`, `--quit` drive the running daemon immediately.
fn spawn_control_socket(tx: mpsc::Sender<UiCommand>) {
    thread::spawn(move || {
        let dir = atticus_dir();
        let _ = fs::create_dir_all(&dir);
        let sock = dir.join("control.sock");
        let _ = fs::remove_file(&sock);
        let Ok(listener) = UnixListener::bind(&sock) else {
            return;
        };
        let _ = fs::set_permissions(&sock, fs::Permissions::from_mode(0o600));
        for stream in listener.incoming().flatten() {
            let mut reader = BufReader::new(stream);
            let mut line = String::new();
            if reader.read_line(&mut line).is_ok() {
                let cmd = match line.trim() {
                    "ask" => Some(UiCommand::ShowAsk { compose: true }),
                    "toggle" => Some(UiCommand::ToggleAsk),
                    "dictate" => Some(UiCommand::StartDictate),
                    "settings" => Some(UiCommand::OpenSettings(None)),
                    "onboarding" => Some(UiCommand::OpenOnboarding),
                    "agents" => Some(UiCommand::OpenAgents),
                    "meeting" => Some(UiCommand::OpenMeeting),
                    "quit" => Some(UiCommand::Quit),
                    _ => None,
                };
                if let Some(c) = cmd {
                    let _ = tx.send(c);
                }
            }
        }
    });
}

#[cfg(feature = "gui")]
pub fn run_gui_daemon(prefs: Arc<Prefs>, startup_action: Option<&str>) -> i32 {
    use gtk4::prelude::*;
    use webkit6::prelude::*;

    // Avoid blank/black WebKitGTK frames on Wayland compositors (Hyprland/Sway/NVIDIA/AMD)
    if std::env::var("WEBKIT_DISABLE_DMABUF_RENDERER").is_err()
        && std::env::var("ATTICUS_DMABUF").ok().as_deref() != Some("1")
    {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    const BASE_URI: &str = "http://atticus.local/";

    fn eval_js(web: &webkit6::WebView, fn_name: &str, args: &Value) {
        let args_json = serde_json::to_string(args).unwrap_or_else(|_| "[]".into());
        let js = format!("if (typeof {fn_name} === 'function') {{ {fn_name}(...{args_json}); }}");
        web.evaluate_javascript(&js, None, None, None::<&gtk4::gio::Cancellable>, |_| {});
    }

    struct ActiveVoice {
        capture: audio::PcmCapture,
        samples: Arc<Mutex<Vec<i16>>>,
        started: std::time::Instant,
        dictate: bool,
    }

    struct RuntimeState {
        front_app: String,
        active_ask: Option<Child>,
        active_answer_file: Option<PathBuf>,
        voice: Option<ActiveVoice>,
        whisper: Arc<Mutex<Option<stt::WhisperServer>>>,
        agents: Option<crate::py_bridge::AgentsDaemon>,
    }

    let app = gtk4::Application::builder()
        .application_id("com.atticus.app")
        .build();

    let startup = startup_action.map(|s| s.to_string());
    let prefs_init = prefs.clone();

    app.connect_activate(move |gtk_app| {
        // Keep the background GTK4 daemon alive even when all windows are hidden!
        let hold_guard = gtk_app.hold();
        let _hold_leak = Box::leak(Box::new(hold_guard));

        // Transparent CSS for borderless overlay windows (prevents solid GTK dark frame)
        if let Some(display) = gtk4::gdk::Display::default() {
            let css = gtk4::CssProvider::new();
            css.load_from_data(
                "window.atticus-overlay, window.atticus-overlay > * { background-color: transparent; background-image: none; box-shadow: none; }",
            );
            gtk4::style_context_add_provider_for_display(
                &display,
                &css,
                gtk4::STYLE_PROVIDER_PRIORITY_APPLICATION,
            );
        }

        let prefs = prefs_init.clone();
        let lang = prefs.ui_language();
        let auto_lang = prefs.get_str("uiLanguage", "auto") == "auto";
        let (tx, rx) = mpsc::channel::<UiCommand>();

        // 1. Background System Tray icon (StatusNotifierItem)
        crate::tray::spawn_system_tray(prefs.clone(), tx.clone());

        // 2. Control UNIX socket (`atticus-host --ask / --toggle / --settings / --quit`)
        spawn_control_socket(tx.clone());

        // 3. Global Hotkeys (`atticus-inputd` evdev)
        let tx_hk = tx.clone();
        let prefs_hk = prefs.clone();
        let hk_state = Arc::new(Mutex::new(crate::hotkeys::ShortcutState::default()));
        crate::hotkeys::spawn_inputd_listener(
            prefs.get_f64("hotkeyCode", 57.0) as u16,
            prefs.get_f64("hotkeyMods", 3.0) as u32,
            prefs.get_f64("dictateHotkeyCode", 100.0) as u16,
            prefs.get_f64("dictateHotkeyMods", 0.0) as u32,
            move |ev| {
                use crate::hotkeys::{HotkeyEvent, ShortcutAction};
                let toggle_mode = prefs_hk.get_str("talkMode", "hold") == "toggle";
                let type_right = prefs_hk.get_bool("typeRight", true);
                match ev {
                    HotkeyEvent::TalkDown => {
                        match hk_state.lock().unwrap().on_talk_down(toggle_mode) {
                            ShortcutAction::StartTalk => { let _ = tx_hk.send(UiCommand::StartVoiceTalk); }
                            ShortcutAction::StopTalk => { let _ = tx_hk.send(UiCommand::StopVoiceTalk); }
                            _ => {}
                        }
                    }
                    HotkeyEvent::TalkUp => {
                        match hk_state.lock().unwrap().on_talk_up(toggle_mode, false, type_right) {
                            ShortcutAction::KeepTypingBox => { let _ = tx_hk.send(UiCommand::ShowAsk { compose: true }); }
                            ShortcutAction::StopTalk => { let _ = tx_hk.send(UiCommand::StopVoiceTalk); }
                            _ => {}
                        }
                    }
                    HotkeyEvent::DictateDown => {
                        match hk_state.lock().unwrap().on_dictate_down() {
                            ShortcutAction::StartDictate => { let _ = tx_hk.send(UiCommand::StartDictate); }
                            ShortcutAction::StopDictate => { let _ = tx_hk.send(UiCommand::StopDictate); }
                            _ => {}
                        }
                    }
                    HotkeyEvent::DictateUp => {
                        if hk_state.lock().unwrap().on_dictate_up(true) == ShortcutAction::StopDictate {
                            let _ = tx_hk.send(UiCommand::StopDictate);
                        }
                    }
                    HotkeyEvent::DictateChordCancel => {
                        if hk_state.lock().unwrap().on_dictate_chord_cancel(false) == ShortcutAction::CancelDictateSilently {
                            let _ = tx_hk.send(UiCommand::CancelDictate);
                        }
                    }
                    HotkeyEvent::Escape => { let _ = tx_hk.send(UiCommand::HideAsk); }
                    HotkeyEvent::PasteLastDictation => { let _ = tx_hk.send(UiCommand::PasteLastDictation); }
                    _ => {}
                }
            },
        );

        // 4. Persistent `atticus.py --agents` daemon
        let tx_ag = tx.clone();
        let agents_daemon = crate::py_bridge::AgentsDaemon::spawn(move |ev| {
            let _ = tx_ag.send(UiCommand::AgentsEvent(ev));
        })
        .ok();

        let runtime = Arc::new(Mutex::new(RuntimeState {
            front_app: String::new(),
            active_ask: None,
            active_answer_file: None,
            voice: None,
            whisper: Arc::new(Mutex::new(None)),
            agents: agents_daemon,
        }));

        // ------------------------------------------------------------------
        // Window 1: Main Floating Ask Box (starts HIDDEN!)
        // ------------------------------------------------------------------
        let box_w = prefs.get_f64("boxW", 500.0).clamp(340.0, 900.0) as i32;
        let main_win = gtk4::ApplicationWindow::builder()
            .application(gtk_app)
            .title("Atticus")
            .default_width(box_w)
            .default_height(130)
            .decorated(false)
            .resizable(true)
            .visible(false)
            .build();
        main_win.add_css_class("atticus-overlay");

        #[cfg(feature = "layer-shell")]
        if gtk4_layer_shell::is_supported() {
            gtk4_layer_shell::LayerShell::init_layer_shell(&main_win);
            gtk4_layer_shell::LayerShell::set_layer(&main_win, gtk4_layer_shell::Layer::Overlay);
            gtk4_layer_shell::LayerShell::set_anchor(&main_win, gtk4_layer_shell::Edge::Top, true);
            gtk4_layer_shell::LayerShell::set_margin(&main_win, gtk4_layer_shell::Edge::Top, 32);
            gtk4_layer_shell::LayerShell::set_keyboard_mode(
                &main_win,
                gtk4_layer_shell::KeyboardMode::OnDemand,
            );
            gtk4_layer_shell::LayerShell::set_namespace(&main_win, "atticus-overlay");
        }

        // Escape key on main overlay immediately hides it
        let key_ctl = gtk4::EventControllerKey::new();
        let tx_esc = tx.clone();
        key_ctl.connect_key_pressed(move |_, key, _, _| {
            if key == gtk4::gdk::Key::Escape {
                let _ = tx_esc.send(UiCommand::HideAsk);
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        main_win.add_controller(key_ctl);

        let tx_close_main = tx.clone();
        main_win.connect_close_request(move |_| {
            let _ = tx_close_main.send(UiCommand::HideAsk);
            glib::Propagation::Stop
        });

        let main_ucc = webkit6::UserContentManager::new();
        for handler in ["h", "c", "atticusError"] {
            main_ucc.register_script_message_handler(handler, None);
        }
        let main_web = webkit6::WebView::builder()
            .user_content_manager(&main_ucc)
            .build();
        main_web.set_background_color(&gtk4::gdk::RGBA::new(0.0, 0.0, 0.0, 0.0));

        // Resize handler (`h`)
        let mw_resize = main_win.clone();
        main_ucc.connect_script_message_received(Some("h"), move |_, val| {
            if let Some(json_str) = val.to_json(0) {
                if let Ok(v) = serde_json::from_str::<Value>(json_str.as_str()) {
                    if let Some(arr) = v.as_array() {
                        let w = arr.first().and_then(|x| x.as_f64()).unwrap_or(180.0).clamp(36.0, 900.0) as i32;
                        let h = arr.get(1).and_then(|x| x.as_f64()).unwrap_or(44.0).clamp(36.0, 900.0) as i32;
                        mw_resize.set_default_size(w, h);
                    } else if let Some(h) = v.as_f64() {
                        let clamped = h.clamp(64.0, 900.0) as i32;
                        let w = mw_resize.width().max(500);
                        mw_resize.set_default_size(w, clamped);
                    }
                }
            }
        });

        // Main command handler (`c`)
        let tx_c = tx.clone();
        let rt_c = runtime.clone();
        let web_c = main_web.clone();
        main_ucc.connect_script_message_received(Some("c"), move |_, val| {
            let Some(json_str) = val.to_json(0) else { return };
            let Ok(v) = serde_json::from_str::<Value>(json_str.as_str()) else { return };
            if v["close"].as_bool() == Some(true) {
                let _ = tx_c.send(UiCommand::HideAsk);
            }
            if let Some(text) = v["send"].as_str() {
                let _ = tx_c.send(UiCommand::SendText(text.to_string()));
            }
            if v["mic"].as_bool() == Some(true) || v["mic"].as_i64() == Some(1) {
                let is_rec = rt_c.lock().unwrap().voice.is_some();
                if is_rec {
                    let _ = tx_c.send(UiCommand::StopVoiceTalk);
                } else {
                    let _ = tx_c.send(UiCommand::StartVoiceTalk);
                }
            }
            if let Some(text) = v["copy"].as_str() {
                a11y::write_clipboard(text);
                eval_js(&web_c, "window.ui.copied", &json!([]));
            }
            if let Some(url) = v["link"].as_str() {
                let _ = Command::new("xdg-open").arg(url).spawn();
            }
            if v["setup"].as_bool() == Some(true) {
                let _ = tx_c.send(UiCommand::OpenOnboarding);
            }
            if v["agents"].as_bool() == Some(true) {
                let _ = tx_c.send(UiCommand::OpenAgents);
            }
            if v["dictationHistory"].as_bool() == Some(true) {
                let _ = tx_c.send(UiCommand::OpenSettings(Some("dictation".into())));
            }
            if v["kofi"].as_bool() == Some(true) {
                let _ = Command::new("xdg-open").arg("https://ko-fi.com/saarthr").spawn();
            }
            if let Some(allow) = v.get("allow") {
                if let Some(path) = rt_c.lock().unwrap().active_answer_file.take() {
                    let reply = if allow.as_bool() == Some(true) {
                        "y"
                    } else if let Some(s) = allow.as_str() {
                        s
                    } else {
                        "n"
                    };
                    let _ = fs::write(path, reply);
                }
            }
            if let Some(pick) = v.get("pick") {
                if let Some(path) = rt_c.lock().unwrap().active_answer_file.take() {
                    let reply = pick
                        .as_str()
                        .map(|s| s.to_string())
                        .or_else(|| pick.as_i64().map(|n| n.to_string()))
                        .unwrap_or_default();
                    let _ = fs::write(path, reply);
                }
            }
        });

        if let Ok(html) = crate::prefs::load_ui_page("ui.html", &lang, auto_lang) {
            main_web.load_html(&html, Some(BASE_URI));
        }
        main_win.set_child(Some(&main_web));

        // ------------------------------------------------------------------
        // Window 2: Settings Window (standard closable titled window, starts HIDDEN)
        // ------------------------------------------------------------------
        let st_win = gtk4::ApplicationWindow::builder()
            .application(gtk_app)
            .title("Atticus Settings")
            .default_width(780)
            .default_height(560)
            .visible(false)
            .build();
        st_win.connect_close_request(|w| {
            w.set_visible(false);
            glib::Propagation::Stop
        });
        let st_ucc = webkit6::UserContentManager::new();
        st_ucc.register_script_message_handler("st", None);
        st_ucc.register_script_message_handler("atticusError", None);
        let st_web = webkit6::WebView::builder()
            .user_content_manager(&st_ucc)
            .build();

        let prefs_st_load = prefs.clone();
        let tx_st_load = tx.clone();
        st_web.connect_load_changed(move |_, ev| {
            if ev == webkit6::LoadEvent::Finished {
                let p = prefs_st_load.clone();
                let t = tx_st_load.clone();
                thread::spawn(move || {
                    let _ = t.send(UiCommand::SettingsReply(json!({
                        "fn": "window.st.load",
                        "args": [build_settings_load_payload(&p)]
                    })));
                    let _ = t.send(UiCommand::SettingsReply(json!({
                        "fn": "window.st.perms",
                        "args": [linux_permissions_json()]
                    })));
                    let mics: Vec<Value> = audio::list_microphones()
                        .into_iter()
                        .map(|m| json!({"uid": m.uid, "name": m.name}))
                        .collect();
                    let _ = t.send(UiCommand::SettingsReply(json!({
                        "fn": "window.st.mics",
                        "args": [mics]
                    })));
                    let _ = t.send(UiCommand::SettingsReply(json!({
                        "fn": "window.st.dictation",
                        "args": [build_dictation_payload(&p)]
                    })));
                    let _ = t.send(UiCommand::SettingsReply(json!({
                        "fn": "window.st.whisper",
                        "args": [stt::whisper_status_json()]
                    })));
                });
            }
        });

        let prefs_st_msg = prefs.clone();
        let tx_st_msg = tx.clone();
        st_ucc.connect_script_message_received(Some("st"), move |_, val| {
            let Some(json_str) = val.to_json(0) else { return };
            let Ok(msg) = serde_json::from_str::<Value>(json_str.as_str()) else { return };
            match msg["do"].as_str().unwrap_or("") {
                "setup" => { let _ = tx_st_msg.send(UiCommand::OpenOnboarding); return; }
                "agents" => { let _ = tx_st_msg.send(UiCommand::OpenAgents); return; }
                "quit" => { let _ = tx_st_msg.send(UiCommand::Quit); return; }
                "whisperDownload" => {
                    if let Some(id) = msg["id"].as_str().map(|s| s.to_string()) {
                        let t = tx_st_msg.clone();
                        thread::spawn(move || {
                            let id_p = id.clone();
                            let t_p = t.clone();
                            let res = stt::download_model(&id, move |pct| {
                                let mut status = stt::whisper_status_json();
                                if let Some(arr) = status["models"].as_array_mut() {
                                    for m in arr {
                                        if m["id"].as_str() == Some(&id_p) {
                                            m["progress"] = json!(pct);
                                        }
                                    }
                                }
                                let _ = t_p.send(UiCommand::SettingsReply(json!({
                                    "fn": "window.st.whisper",
                                    "args": [status]
                                })));
                            });
                            let mut status = stt::whisper_status_json();
                            if let Err(e) = res {
                                status["error"] = json!(e);
                            }
                            let _ = t.send(UiCommand::SettingsReply(json!({
                                "fn": "window.st.whisper",
                                "args": [status]
                            })));
                        });
                    }
                    return;
                }
                _ => {}
            }
            let p = prefs_st_msg.clone();
            let t = tx_st_msg.clone();
            thread::spawn(move || {
                if let Some(mut reply) = handle_settings_message(&p, &msg) {
                    if let Some(f) = reply["fn"].as_str() {
                        if !f.starts_with("window.") {
                            reply["fn"] = json!(format!("window.{f}"));
                        }
                    }
                    let _ = t.send(UiCommand::SettingsReply(reply));
                }
            });
        });
        if let Ok(html) = crate::prefs::load_ui_page("settings.html", &lang, auto_lang) {
            st_web.load_html(&html, Some(BASE_URI));
        }
        st_win.set_child(Some(&st_web));

        // ------------------------------------------------------------------
        // Window 3: Onboarding Window (standard closable titled window, starts HIDDEN)
        // ------------------------------------------------------------------
        let ob_win = gtk4::ApplicationWindow::builder()
            .application(gtk_app)
            .title("Atticus Setup")
            .default_width(640)
            .default_height(640)
            .visible(false)
            .build();
        ob_win.connect_close_request(|w| {
            w.set_visible(false);
            glib::Propagation::Stop
        });
        let ob_ucc = webkit6::UserContentManager::new();
        ob_ucc.register_script_message_handler("ob", None);
        ob_ucc.register_script_message_handler("atticusError", None);
        let ob_web = webkit6::WebView::builder()
            .user_content_manager(&ob_ucc)
            .build();

        let prefs_ob_load = prefs.clone();
        let tx_ob_load = tx.clone();
        ob_web.connect_load_changed(move |_, ev| {
            if ev == webkit6::LoadEvent::Finished {
                let p = prefs_ob_load.clone();
                let t = tx_ob_load.clone();
                thread::spawn(move || {
                    let _ = t.send(UiCommand::OnboardingReply(json!({
                        "fn": "window.ob.prefs",
                        "args": [p.snapshot()]
                    })));
                    let _ = t.send(UiCommand::OnboardingReply(json!({
                        "fn": "window.ob.set",
                        "args": [linux_permissions_json()]
                    })));
                    if let Ok((0, out, _)) = py_run(&["--check"], None) {
                        if let Ok(plan) = serde_json::from_str::<Value>(&out) {
                            let _ = t.send(UiCommand::OnboardingReply(json!({
                                "fn": "window.ob.plan",
                                "args": [plan]
                            })));
                        }
                    }
                });
            }
        });

        let ob_win_done = ob_win.clone();
        let prefs_ob_msg = prefs.clone();
        let tx_ob_msg = tx.clone();
        ob_ucc.connect_script_message_received(Some("ob"), move |_, val| {
            let Some(json_str) = val.to_json(0) else { return };
            let Ok(msg) = serde_json::from_str::<Value>(json_str.as_str()) else { return };
            match msg["do"].as_str().unwrap_or("") {
                "done" => {
                    prefs_ob_msg.set("onboarded", json!(true));
                    ob_win_done.set_visible(false);
                }
                "set" => {
                    if let Some(k) = msg["k"].as_str() {
                        prefs_ob_msg.set(k, msg["v"].clone());
                    }
                }
                "provider" => {
                    for key in ["client", "apiBase", "apiModel", "apiKey"] {
                        if let Some(v) = msg.get(key) {
                            prefs_ob_msg.set(key, v.clone());
                        }
                    }
                    let t = tx_ob_msg.clone();
                    thread::spawn(move || {
                        if let Ok((0, out, _)) = py_run(&["--check"], None) {
                            if let Ok(plan) = serde_json::from_str::<Value>(&out) {
                                let _ = t.send(UiCommand::OnboardingReply(json!({
                                    "fn": "window.ob.plan",
                                    "args": [plan]
                                })));
                            }
                        }
                    });
                }
                "localServers" => {
                    let t = tx_ob_msg.clone();
                    thread::spawn(move || {
                        let found = py_run(&["--local-servers"], None)
                            .ok()
                            .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                            .unwrap_or_else(|| json!([]));
                        let _ = t.send(UiCommand::OnboardingReply(json!({
                            "fn": "window.ob.local",
                            "args": [found]
                        })));
                    });
                }
                "kofi" => { let _ = Command::new("xdg-open").arg("https://ko-fi.com/saarthr").spawn(); }
                "feedback" => { let _ = tx_ob_msg.send(UiCommand::OpenSettings(Some("feedback".into()))); }
                _ => {
                    let _ = tx_ob_msg.send(UiCommand::OnboardingReply(json!({
                        "fn": "window.ob.set",
                        "args": [linux_permissions_json()]
                    })));
                }
            }
        });
        if let Ok(html) = crate::prefs::load_ui_page("onboarding.html", &lang, auto_lang) {
            ob_web.load_html(&html, Some(BASE_URI));
        }
        ob_win.set_child(Some(&ob_web));

        // ------------------------------------------------------------------
        // Window 4: Agents & Chats Window (standard closable titled window, starts HIDDEN)
        // ------------------------------------------------------------------
        let ag_win = gtk4::ApplicationWindow::builder()
            .application(gtk_app)
            .title("Atticus Agents")
            .default_width(760)
            .default_height(580)
            .visible(false)
            .build();
        ag_win.connect_close_request(|w| {
            w.set_visible(false);
            glib::Propagation::Stop
        });
        let ag_ucc = webkit6::UserContentManager::new();
        ag_ucc.register_script_message_handler("agents", None);
        ag_ucc.register_script_message_handler("atticusError", None);
        let ag_web = webkit6::WebView::builder()
            .user_content_manager(&ag_ucc)
            .build();
        let rt_ag_load = runtime.clone();
        ag_web.connect_load_changed(move |web, ev| {
            if ev == webkit6::LoadEvent::Finished {
                eval_js(web, "window.agents.mode", &json!(["view"]));
                if let Some(d) = rt_ag_load.lock().unwrap().agents.as_mut() {
                    let _ = d.send(&json!({"action": "list"}));
                }
            }
        });
        let ag_win_c = ag_win.clone();
        let rt_ag_msg = runtime.clone();
        ag_ucc.connect_script_message_received(Some("agents"), move |_, val| {
            let Some(json_str) = val.to_json(0) else { return };
            let Ok(msg) = serde_json::from_str::<Value>(json_str.as_str()) else { return };
            if msg["action"].as_str() == Some("close") {
                ag_win_c.set_visible(false);
                return;
            }
            if let Some(d) = rt_ag_msg.lock().unwrap().agents.as_mut() {
                let _ = d.send(&msg);
            }
        });
        if let Ok(html) = crate::prefs::load_ui_page("agents.html", &lang, auto_lang) {
            ag_web.load_html(&html, Some(BASE_URI));
        }
        ag_win.set_child(Some(&ag_web));

        // ------------------------------------------------------------------
        // Window 5: Meeting Copilot Window (closable floating window, starts HIDDEN)
        // ------------------------------------------------------------------
        let mt_win = gtk4::ApplicationWindow::builder()
            .application(gtk_app)
            .title("Meeting Copilot")
            .default_width(420)
            .default_height(520)
            .visible(false)
            .build();
        mt_win.connect_close_request(|w| {
            w.set_visible(false);
            glib::Propagation::Stop
        });
        let mt_ucc = webkit6::UserContentManager::new();
        mt_ucc.register_script_message_handler("meeting", None);
        mt_ucc.register_script_message_handler("atticusError", None);
        let mt_web = webkit6::WebView::builder()
            .user_content_manager(&mt_ucc)
            .build();
        let mt_win_c = mt_win.clone();
        let tx_mt = tx.clone();
        mt_ucc.connect_script_message_received(Some("meeting"), move |_, val| {
            let Some(json_str) = val.to_json(0) else { return };
            let Ok(msg) = serde_json::from_str::<Value>(json_str.as_str()) else { return };
            match msg["do"].as_str().unwrap_or("") {
                "delete" => mt_win_c.set_visible(false),
                "meetings" => { let _ = tx_mt.send(UiCommand::OpenSettings(Some("meetings".into()))); }
                "copy" => {
                    if let Some(t) = msg["text"].as_str() {
                        a11y::write_clipboard(t);
                    }
                }
                _ => {}
            }
        });
        if let Ok(html) = crate::prefs::load_ui_page("meeting.html", &lang, auto_lang) {
            mt_web.load_html(&html, Some(BASE_URI));
        }
        mt_win.set_child(Some(&mt_web));

        // ------------------------------------------------------------------
        // Main-Thread Event Loop Pump (drains UiCommand every 25ms)
        // ------------------------------------------------------------------
        let app_quit = gtk_app.clone();
        let tx_loop = tx.clone();
        let prefs_loop = prefs.clone();
        let rt_loop = runtime.clone();
        let mw = main_win.clone();
        let mweb = main_web.clone();
        let sw = st_win.clone();
        let sweb = st_web.clone();
        let ow = ob_win.clone();
        let oweb = ob_web.clone();
        let aw = ag_win.clone();
        let aweb = ag_web.clone();
        let mtw = mt_win.clone();
        let mtweb = mt_web.clone();

        glib::timeout_add_local(Duration::from_millis(25), move || {
            while let Ok(cmd) = rx.try_recv() {
                match cmd {
                    UiCommand::ToggleAsk => {
                        if mw.is_visible() {
                            let _ = tx_loop.send(UiCommand::HideAsk);
                        } else {
                            let _ = tx_loop.send(UiCommand::ShowAsk { compose: true });
                        }
                    }
                    UiCommand::ShowAsk { compose } => {
                        let front = crate::wm::detect_backend()
                            .active_window()
                            .map(|w| w.app)
                            .unwrap_or_default();
                        rt_loop.lock().unwrap().front_app = front;
                        #[cfg(feature = "layer-shell")]
                        if gtk4_layer_shell::is_supported() {
                            gtk4_layer_shell::LayerShell::set_keyboard_mode(
                                &mw,
                                gtk4_layer_shell::KeyboardMode::Exclusive,
                            );
                        }
                        mw.set_visible(true);
                        mw.present();
                        mweb.grab_focus();
                        eval_js(&mweb, "window.ui.visible", &json!([true]));
                        if compose {
                            eval_js(&mweb, "window.ui.compose", &json!([true]));
                        }
                    }
                    UiCommand::HideAsk => {
                        {
                            let mut rt = rt_loop.lock().unwrap();
                            if let Some(mut child) = rt.active_ask.take() {
                                let _ = child.kill();
                                let _ = child.wait();
                            }
                            if let Some(mut v) = rt.voice.take() {
                                v.capture.stop();
                            }
                            rt.active_answer_file = None;
                        }
                        eval_js(&mweb, "window.ui.visible", &json!([false]));
                        #[cfg(feature = "layer-shell")]
                        if gtk4_layer_shell::is_supported() {
                            gtk4_layer_shell::LayerShell::set_keyboard_mode(
                                &mw,
                                gtk4_layer_shell::KeyboardMode::None,
                            );
                        }
                        mw.set_visible(false);
                    }
                    UiCommand::StartVoiceTalk => {
                        let front = crate::wm::detect_backend()
                            .active_window()
                            .map(|w| w.app)
                            .unwrap_or_default();
                        let mic_pref = prefs_loop.get_str("microphone", "automatic");
                        let dev = audio::choose_microphone(&mic_pref).map(|m| m.uid);
                        let samples = Arc::new(Mutex::new(Vec::<i16>::new()));
                        let samples_cb = samples.clone();
                        let tx_lvl = tx_loop.clone();
                        mw.set_visible(true);
                        mw.present();
                        eval_js(&mweb, "window.ui.visible", &json!([true]));
                        eval_js(&mweb, "window.ui.listen", &json!([false]));
                        tts::play_cue("Pop", prefs_loop.get_f64("volume", 0.16));
                        match audio::PcmCapture::start(dev.as_deref(), move |chunk, rms| {
                            if let Ok(mut buf) = samples_cb.lock() {
                                buf.extend_from_slice(chunk);
                            }
                            let _ = tx_lvl.send(UiCommand::VoiceLevel(rms));
                        }) {
                            Ok(capture) => {
                                let mut rt = rt_loop.lock().unwrap();
                                rt.front_app = front;
                                rt.voice = Some(ActiveVoice {
                                    capture,
                                    samples,
                                    started: std::time::Instant::now(),
                                    dictate: false,
                                });
                            }
                            Err(e) => {
                                eval_js(&mweb, "window.ui.msg", &json!([e]));
                                eval_js(&mweb, "window.ui.compose", &json!([true]));
                            }
                        }
                    }
                    UiCommand::StartDictate => {
                        let front = crate::wm::detect_backend()
                            .active_window()
                            .map(|w| w.app)
                            .unwrap_or_default();
                        let mic_pref = prefs_loop.get_str("microphone", "automatic");
                        let dev = audio::choose_microphone(&mic_pref).map(|m| m.uid);
                        let samples = Arc::new(Mutex::new(Vec::<i16>::new()));
                        let samples_cb = samples.clone();
                        let tx_lvl = tx_loop.clone();
                        mw.set_visible(true);
                        mw.present();
                        eval_js(&mweb, "window.ui.visible", &json!([true]));
                        eval_js(&mweb, "window.ui.listen", &json!(["dict"]));
                        match audio::PcmCapture::start(dev.as_deref(), move |chunk, rms| {
                            if let Ok(mut buf) = samples_cb.lock() {
                                buf.extend_from_slice(chunk);
                            }
                            let _ = tx_lvl.send(UiCommand::VoiceLevel(rms));
                        }) {
                            Ok(capture) => {
                                let mut rt = rt_loop.lock().unwrap();
                                rt.front_app = front;
                                rt.voice = Some(ActiveVoice {
                                    capture,
                                    samples,
                                    started: std::time::Instant::now(),
                                    dictate: true,
                                });
                            }
                            Err(e) => {
                                eval_js(&mweb, "window.ui.dictated", &json!([{"text": e, "ok": false}]));
                            }
                        }
                    }
                    UiCommand::StopVoiceTalk | UiCommand::StopDictate => {
                        let active = rt_loop.lock().unwrap().voice.take();
                        let Some(mut v) = active else { continue };
                        v.capture.stop();
                        let pcm = v.samples.lock().unwrap().clone();
                        let dur = v.started.elapsed().as_secs_f64();
                        let is_dict = v.dictate;
                        if is_dict {
                            eval_js(&mweb, "window.ui.finishing", &json!(["Cleaning up…"]));
                        } else {
                            tts::play_cue("Bottle", prefs_loop.get_f64("volume", 0.16));
                            eval_js(&mweb, "window.ui.think", &json!([]));
                        }
                        let whisper_arc = rt_loop.lock().unwrap().whisper.clone();
                        let model_id = prefs_loop.get_str("whisperModel", "turbo");
                        let lang_pref = prefs_loop.get_str("whisperLanguage", "auto");
                        let tx_tr = tx_loop.clone();
                        thread::spawn(move || {
                            let mut guard = whisper_arc.lock().unwrap();
                            if guard.is_none() {
                                match stt::WhisperServer::start(&model_id) {
                                    Ok(srv) => {
                                        // Give whisper-server a brief moment to bind HTTP port
                                        thread::sleep(Duration::from_millis(600));
                                        *guard = Some(srv);
                                    }
                                    Err(e) => {
                                        let _ = tx_tr.send(UiCommand::VoiceError(format!(
                                            "{e} Open Settings → Voice & Sound to install/download Whisper, or type below."
                                        )));
                                        return;
                                    }
                                }
                            }
                            if let Some(srv) = guard.as_mut() {
                                match srv.transcribe(&pcm, &lang_pref, "") {
                                    Ok(text) => {
                                        let _ = tx_tr.send(UiCommand::VoiceTranscribed {
                                            text,
                                            dictate: is_dict,
                                            duration: dur,
                                        });
                                    }
                                    Err(e) => {
                                        let _ = tx_tr.send(UiCommand::VoiceError(e));
                                    }
                                }
                            }
                        });
                    }
                    UiCommand::CancelDictate => {
                        if let Some(mut v) = rt_loop.lock().unwrap().voice.take() {
                            v.capture.stop();
                        }
                        eval_js(&mweb, "window.ui.dictate", &json!([false]));
                        mw.set_visible(false);
                    }
                    UiCommand::VoiceLevel(rms) => {
                        eval_js(&mweb, "window.ui.level", &json!([rms]));
                    }
                    UiCommand::VoiceTranscribed { text, dictate, duration } => {
                        let clean = text.trim().to_string();
                        if clean.is_empty() {
                            eval_js(&mweb, "window.ui.msg", &json!(["Didn’t catch that."]));
                            eval_js(&mweb, "window.ui.compose", &json!([true]));
                        } else if dictate {
                            let p = prefs_loop.clone();
                            let front = rt_loop.lock().unwrap().front_app.clone();
                            let tx_d = tx_loop.clone();
                            thread::spawn(move || {
                                let out = crate::dictation::finish_dictation(&p, &clean, "", &front, duration);
                                let _ = tx_d.send(UiCommand::DictateDone {
                                    text: out.text,
                                    inserted: out.inserted,
                                    status: out.status,
                                });
                            });
                        } else {
                            let _ = tx_loop.send(UiCommand::SendText(clean));
                        }
                    }
                    UiCommand::VoiceError(err) => {
                        eval_js(&mweb, "window.ui.msg", &json!([err]));
                        eval_js(&mweb, "window.ui.compose", &json!([true]));
                    }
                    UiCommand::DictateDone { text, inserted, status } => {
                        if inserted {
                            eval_js(&mweb, "window.ui.dictated", &json!([{"ok": true}]));
                        } else {
                            eval_js(
                                &mweb,
                                "window.ui.dictated",
                                &json!([{"text": status, "ok": false, "words": text, "history": true}]),
                            );
                        }
                    }
                    UiCommand::SendText(task) => {
                        let trimmed = task.trim().to_string();
                        if trimmed.is_empty() {
                            continue;
                        }
                        mw.set_visible(true);
                        eval_js(&mweb, "window.ui.visible", &json!([true]));
                        eval_js(&mweb, "window.ui.listen", &json!([true]));
                        eval_js(&mweb, "window.ui.words", &json!([trimmed]));
                        eval_js(&mweb, "window.ui.think", &json!([]));

                        let front = {
                            let mut rt = rt_loop.lock().unwrap();
                            if let Some(mut old) = rt.active_ask.take() {
                                let _ = old.kill();
                                let _ = old.wait();
                            }
                            rt.front_app.clone()
                        };

                        let ask_dir = std::env::temp_dir().join(format!(
                            "atticus-ask-{}",
                            std::time::SystemTime::now()
                                .duration_since(std::time::UNIX_EPOCH)
                                .map(|d| d.as_nanos())
                                .unwrap_or(0)
                        ));
                        let _ = fs::create_dir_all(&ask_dir);
                        let commit_file = ask_dir.join("commit");
                        let _ = fs::write(&commit_file, "go");

                        let mut env = HashMap::new();
                        env.insert("ATTICUS_FRONT".into(), front.clone());
                        env.insert("ATTICUS_COMMIT".into(), commit_file.to_string_lossy().to_string());
                        let mut cmd = py_command(&["--wait"], &env);
                        cmd.stdin(Stdio::piped())
                            .stdout(Stdio::piped())
                            .stderr(Stdio::inherit());

                        match cmd.spawn() {
                            Ok(mut child) => {
                                if let Some(mut stdin) = child.stdin.take() {
                                    let req = json!({"front": front, "task": trimmed});
                                    let _ = writeln!(stdin, "{req}");
                                }
                                let stdout = child.stdout.take();
                                rt_loop.lock().unwrap().active_ask = Some(child);
                                let tx_out = tx_loop.clone();
                                thread::spawn(move || {
                                    if let Some(out) = stdout {
                                        let reader = BufReader::new(out);
                                        for line in reader.lines().map_while(Result::ok) {
                                            let ev = parse_ask_line(&line);
                                            let _ = tx_out.send(UiCommand::AskStreamEvent(ev));
                                        }
                                    }
                                    let _ = fs::remove_dir_all(&ask_dir);
                                    let _ = tx_out.send(UiCommand::AskFinished);
                                });
                            }
                            Err(e) => {
                                eval_js(&mweb, "window.ui.msg", &json!([format!("Could not start atticus.py: {e}")]));
                            }
                        }
                    }
                    UiCommand::AskStreamEvent(ev) => match ev {
                        AskEvent::Note(s) => eval_js(&mweb, "window.ui.line", &json!([format!("· {s}")])),
                        AskEvent::Step(s) => eval_js(&mweb, "window.ui.line", &json!([format!("▸ {s}")])),
                        AskEvent::Partial(s) => eval_js(&mweb, "window.ui.line", &json!([format!("… {s}")])),
                        AskEvent::Answer(s) => {
                            eval_js(&mweb, "window.ui.line", &json!([format!("✓ {s}")]));
                            eval_js(&mweb, "window.ui.finished", &json!([]));
                            tts::play_cue("Glass", prefs_loop.get_f64("volume", 0.16));
                        }
                        AskEvent::Tip(s) => eval_js(&mweb, "window.ui.line", &json!([format!("i {s}")])),
                        AskEvent::Preview(j) => eval_js(&mweb, "window.ui.line", &json!([format!("▣ {j}")])),
                        AskEvent::Point(p) => eval_js(&mweb, "window.ui.line", &json!([format!("@ {p}")])),
                        AskEvent::DrawOrMemory(j) => {
                            if j.trim_start().starts_with('[') {
                                eval_js(&mweb, "window.ui.line", &json!([format!("✎ {j}")]));
                            }
                        }
                        AskEvent::Walkthrough(j) => {
                            if let Ok(v) = serde_json::from_str::<Value>(&j) {
                                eval_js(&mweb, "window.ui.walkthrough", &json!([v]));
                            }
                        }
                        AskEvent::Error(e) => {
                            if e.trim() == "setup" {
                                let _ = tx_loop.send(UiCommand::OpenOnboarding);
                            } else if e.trim() == "feedback" {
                                let _ = tx_loop.send(UiCommand::OpenSettings(Some("feedback".into())));
                            } else {
                                eval_js(&mweb, "window.ui.msg", &json!([e]));
                            }
                        }
                        AskEvent::Approval(raw) => {
                            let parts: Vec<&str> = raw.split('\t').collect();
                            if parts.len() >= 3 {
                                rt_loop.lock().unwrap().active_answer_file = Some(PathBuf::from(parts[2]));
                                eval_js(&mweb, "window.ui.ask", &json!([parts[0], parts[1]]));
                            }
                        }
                        AskEvent::Picker(raw) => {
                            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                                if let Some(a) = v["a"].as_str() {
                                    rt_loop.lock().unwrap().active_answer_file = Some(PathBuf::from(a));
                                }
                                eval_js(&mweb, "window.ui.pick", &json!([v["q"], v["options"]]));
                            }
                        }
                        AskEvent::Speak(s) => {
                            if prefs_loop.get_bool("speak", true) {
                                let rate = prefs_loop.get_f64("voiceRate", 196.0);
                                thread::spawn(move || {
                                    tts::speak_blocking(&s, "default", rate);
                                });
                            }
                        }
                        AskEvent::Handoff(j) => {
                            if let Ok(v) = serde_json::from_str::<Value>(&j) {
                                if let Some(d) = rt_loop.lock().unwrap().agents.as_mut() {
                                    let _ = d.send(&v);
                                }
                            }
                        }
                        AskEvent::Setting(s) => {
                            if let Some((k, v)) = s.split_once(' ') {
                                let val = match v {
                                    "true" | "on" => json!(true),
                                    "false" | "off" => json!(false),
                                    other => json!(other),
                                };
                                prefs_loop.set(k, val);
                            }
                        }
                        _ => {}
                    },
                    UiCommand::AskFinished => {
                        if let Some(mut child) = rt_loop.lock().unwrap().active_ask.take() {
                            let _ = child.wait();
                        }
                        eval_js(&mweb, "window.ui.finished", &json!([]));
                    }
                    UiCommand::OpenSettings(tab) => {
                        sw.set_visible(true);
                        sw.present();
                        if let Some(t) = tab {
                            eval_js(&sweb, "window.st.tab", &json!([t]));
                        }
                    }
                    UiCommand::OpenOnboarding => {
                        ow.set_visible(true);
                        ow.present();
                    }
                    UiCommand::OpenAgents => {
                        aw.set_visible(true);
                        aw.present();
                        if let Some(d) = rt_loop.lock().unwrap().agents.as_mut() {
                            let _ = d.send(&json!({"action": "list"}));
                        }
                    }
                    UiCommand::OpenMeeting => {
                        mtw.set_visible(true);
                        mtw.present();
                        eval_js(&mtweb, "window.meeting.prep", &json!([{}]));
                    }
                    UiCommand::TogglePause => {
                        let next = !prefs_loop.get_bool("paused", false);
                        prefs_loop.set("paused", json!(next));
                    }
                    UiCommand::PasteLastDictation => {
                        let front = crate::wm::detect_backend()
                            .active_window()
                            .map(|w| w.app)
                            .unwrap_or_default();
                        thread::spawn(move || {
                            let _ = crate::dictation::paste_last_dictation(&front);
                        });
                    }
                    UiCommand::SettingsReply(reply) => {
                        if let Some(f) = reply["fn"].as_str() {
                            eval_js(&sweb, f, &reply["args"]);
                        }
                    }
                    UiCommand::OnboardingReply(reply) => {
                        if let Some(f) = reply["fn"].as_str() {
                            eval_js(&oweb, f, &reply["args"]);
                        }
                    }
                    UiCommand::AgentsEvent(ev) => match ev["event"].as_str().unwrap_or("") {
                        "list" => eval_js(&aweb, "window.agents.list", &json!([ev["rows"]])),
                        "chats" => eval_js(&aweb, "window.agents.chats", &json!([ev["chats"]])),
                        "named" => eval_js(&aweb, "window.agents.named", &json!([ev["named"]])),
                        "state" => eval_js(&aweb, "window.agents.state", &json!([ev["row"]])),
                        "text" => eval_js(&aweb, "window.agents.text", &json!([ev["id"], ev["text"]])),
                        _ => {}
                    },
                    UiCommand::MeetingReply(reply) => {
                        if let Some(f) = reply["fn"].as_str() {
                            eval_js(&mtweb, f, &reply["args"]);
                        }
                    }
                    UiCommand::Quit => {
                        let _ = fs::remove_file(atticus_dir().join("control.sock"));
                        if let Some(mut d) = rt_loop.lock().unwrap().agents.take() {
                            d.stop();
                        }
                        app_quit.quit();
                        return glib::ControlFlow::Break;
                    }
                }
            }
            glib::ControlFlow::Continue
        });

        // Handle explicit startup action if launched via `--ask`, `--settings`, `--onboarding`, etc.
        // If started with NO args (e.g. systemd service at login), stay quietly in the tray!
        match startup.as_deref() {
            Some("ask") | Some("toggle") => { let _ = tx.send(UiCommand::ShowAsk { compose: true }); }
            Some("dictate") => { let _ = tx.send(UiCommand::StartDictate); }
            Some("settings") => { let _ = tx.send(UiCommand::OpenSettings(None)); }
            Some("onboarding") => { let _ = tx.send(UiCommand::OpenOnboarding); }
            Some("agents") => { let _ = tx.send(UiCommand::OpenAgents); }
            Some("meeting") => { let _ = tx.send(UiCommand::OpenMeeting); }
            _ => {
                // Background startup: stay hidden in system tray with no blocking window!
                crate::notify::send_notification(
                    "Atticus is running",
                    "Use the tray icon, Ctrl+Alt+Space, or 'atticus-host --ask' to open Atticus.",
                );
            }
        }
    });

    app.run_with_args::<&str>(&[]).into()
}

#[cfg(not(feature = "gui"))]
pub fn run_gui_daemon(_prefs: Arc<Prefs>, _startup_action: Option<&str>) -> i32 {
    eprintln!(
        "atticus-host was built without the `gui` feature (GTK4 + WebKitGTK6).\n\
         Rebuild with `cargo build --release --features gui` on Linux to launch the desktop daemon,\n\
         or pass a CLI flag (see `atticus-host --help`)."
    );
    1
}
