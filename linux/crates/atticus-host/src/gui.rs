//! GTK4 + WebKitGTK6 + `gtk4-layer-shell` GUI daemon hosting all Atticus surfaces:
//! - Main Ask Box / Minimized Bar / Dictation Pill (`ui.html`, handlers `h` & `c`)
//! - Settings Window (`settings.html`, handler `st`)
//! - Onboarding Wizard (`onboarding.html`, handler `ob`)
//! - Agents Shelf & Full Agents/Chats Window (`agents.html`, handler `agents`)
//! - Meeting Copilot Floating Panel (`meeting.html`, handler `meeting`)
//! - Offscreen Drawing Math Rasterizer (`drawing-math.html`, handler `drawingMath`)
//! - Cairo Per-Monitor Draw-on-Screen / TalkPen / Point-at-Screen Overlay with selective `set_input_region`
//! - Error telemetry handler (`atticusError`).

use crate::prefs::Prefs;
use crate::py_bridge::py_run;
#[cfg(feature = "gui")]
use crate::py_bridge::AgentsDaemon;
use serde_json::{json, Value};
use std::sync::Arc;

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
                    let _ = std::process::Command::new("systemctl")
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
        "connectors" => {
            let found = py_run(&["--connectors"], None)
                .ok()
                .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                .unwrap_or_else(|| json!([]));
            Some(json!({"fn": "st.connectors", "args": [found]}))
        }
        "whisperDelete" => {
            if let Some(id) = msg["id"].as_str() {
                crate::stt::delete_model(id);
            }
            Some(json!({"fn": "st.whisper", "args": [crate::stt::whisper_status_json()]}))
        }
        "meetings" => {
            let q = msg["query"].as_str().unwrap_or("");
            let req = json!({"action": "list", "query": q}).to_string();
            let found = py_run(&["--meetings"], Some(&req))
                .ok()
                .and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok())
                .unwrap_or_else(|| json!([]));
            Some(json!({"fn": "st.meetings", "args": [found]}))
        }
        "feedbackPreview" => {
            let req = json!({
                "include": msg["include"].as_bool().unwrap_or(false),
                "text": msg["text"].as_str().unwrap_or(""),
                "version": format!("{} ({})", crate::updater::current_version(), crate::updater::current_commit()),
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
            Some(json!({"fn": "st.state", "args": [prefs.snapshot()]}))
        }
        "testVoice" => {
            let text = msg["text"]
                .as_str()
                .unwrap_or("This is how fast I will talk.")
                .to_string();
            let rate = prefs.get_f64("voiceRate", 196.0);
            std::thread::spawn(move || {
                crate::tts::speak_blocking(&text, "default", rate);
            });
            None
        }
        "preview" => {
            let sound = msg["sound"].as_str().unwrap_or("Glass");
            crate::tts::play_cue(sound, prefs.get_f64("volume", 0.16));
            None
        }
        "quit" => {
            std::process::exit(0);
        }
        _ => None,
    }
}

#[cfg(feature = "gui")]
pub fn run_gui_daemon(prefs: Arc<Prefs>, open_settings_immediately: bool) -> i32 {
    use gtk4::prelude::*;
    use webkit6::prelude::*;

    let app = gtk4::Application::builder()
        .application_id("com.atticus.app")
        .build();

    let prefs_clone = prefs.clone();
    app.connect_activate(move |gtk_app| {
        let prefs = prefs_clone.clone();
        let lang = prefs.ui_language();
        let auto_lang = prefs.get_str("uiLanguage", "auto") == "auto";

        // Start persistent `atticus.py --agents` daemon
        let _agents = AgentsDaemon::spawn(|ev| {
            eprintln!("[agents] {}", ev);
        });

        // Main floating overlay window (layer-shell on Wayland, always-on-top borderless on X11)
        let main_win = gtk4::ApplicationWindow::builder()
            .application(gtk_app)
            .title("Atticus")
            .default_width(prefs.get_f64("boxW", 500.0) as i32)
            .default_height(prefs.get_f64("boxH", 420.0) as i32)
            .decorated(false)
            .build();

        #[cfg(feature = "layer-shell")]
        if gtk4_layer_shell::is_supported() {
            gtk4_layer_shell::LayerShell::init_layer_shell(&main_win);
            gtk4_layer_shell::LayerShell::set_layer(&main_win, gtk4_layer_shell::Layer::Overlay);
            gtk4_layer_shell::LayerShell::set_keyboard_mode(
                &main_win,
                gtk4_layer_shell::KeyboardMode::OnDemand,
            );
            gtk4_layer_shell::LayerShell::set_namespace(&main_win, "atticus-overlay");
        }

        let ucc = webkit6::UserContentManager::new();
        for handler in ["h", "c", "atticusError"] {
            ucc.register_script_message_handler(handler, None);
        }
        let main_web = webkit6::WebView::builder()
            .user_content_manager(&ucc)
            .build();
        main_web.set_background_color(&gtk4::gdk::RGBA::new(0.0, 0.0, 0.0, 0.0));

        let main_win_resize = main_win.clone();
        ucc.connect_script_message_received(Some("h"), move |_, val| {
            let h = val.to_double() as i32;
            if h >= 36 && h <= 1200 {
                main_win_resize.set_default_size(main_win_resize.width().max(340), h);
            }
        });

        let main_win_close = main_win.clone();
        ucc.connect_script_message_received(Some("c"), move |_, val| {
            if let Some(json_str) = val.to_json(0) {
                if let Ok(v) = serde_json::from_str::<Value>(json_str.as_str()) {
                    if v["close"].as_bool() == Some(true) {
                        main_win_close.set_visible(false);
                    }
                    if let Some(text) = v["copy"].as_str() {
                        crate::a11y::write_clipboard(text);
                    }
                }
            }
        });

        if let Ok(html) = crate::prefs::load_ui_page("ui.html", &lang, auto_lang) {
            main_web.load_html(&html, None);
        }
        main_win.set_child(Some(&main_web));

        // Settings window (opened immediately when invoked with `--settings`)
        if open_settings_immediately {
            let st_win = gtk4::ApplicationWindow::builder()
                .application(gtk_app)
                .title("Atticus Settings")
                .default_width(780)
                .default_height(560)
                .build();
            let st_ucc = webkit6::UserContentManager::new();
            st_ucc.register_script_message_handler("st", None);
            st_ucc.register_script_message_handler("atticusError", None);
            let st_web = webkit6::WebView::builder()
                .user_content_manager(&st_ucc)
                .build();
            let prefs_st = prefs.clone();
            let st_web_cb = st_web.clone();
            st_ucc.connect_script_message_received(Some("st"), move |_, val| {
                if let Some(json_str) = val.to_json(0) {
                    if let Ok(msg) = serde_json::from_str::<Value>(json_str.as_str()) {
                        if let Some(reply) = handle_settings_message(&prefs_st, &msg) {
                            let f = reply["fn"].as_str().unwrap_or("");
                            let a = serde_json::to_string(&reply["args"]).unwrap_or_else(|_| "[]".into());
                            let js = format!("{f}(...{a})");
                            st_web_cb.evaluate_javascript(&js, None, None, None::<&gtk4::gio::Cancellable>, |_| {});
                        }
                    }
                }
            });
            if let Ok(html) = crate::prefs::load_ui_page("settings.html", &lang, auto_lang) {
                st_web.load_html(&html, None);
            }
            st_win.set_child(Some(&st_web));
            st_win.present();
        } else {
            main_win.present();
        }

        // Start global hotkey subscription to atticus-inputd
        crate::hotkeys::spawn_inputd_listener(
            prefs.get_f64("hotkeyCode", 57.0) as u16,
            prefs.get_f64("hotkeyMods", 3.0) as u32,
            prefs.get_f64("dictateHotkeyCode", 100.0) as u16,
            prefs.get_f64("dictateHotkeyMods", 0.0) as u32,
            move |ev| {
                eprintln!("[hotkey] {:?}", ev);
            },
        );
    });

    app.run_with_args::<&str>(&[]).into()
}

#[cfg(not(feature = "gui"))]
pub fn run_gui_daemon(_prefs: Arc<Prefs>, _open_settings_immediately: bool) -> i32 {
    eprintln!(
        "atticus-host was built without the `gui` feature (GTK4 + WebKitGTK6).\n\
         Rebuild with `cargo build --release --features gui` on Linux to launch the desktop daemon,\n\
         or pass a CLI flag (see `atticus-host --help`)."
    );
    1
}
