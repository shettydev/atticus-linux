//! KDE Plasma 6 (KWin) Wayland backend via `kscreen-doctor` and KWin D-Bus scripting.

use super::{is_builtin_connector, DisplayInfo, WindowInfo, WindowManagerBackend};
use serde_json::Value;
use std::process::Command;

pub struct KWinBackend;

fn run_kwin_script(js: &str) -> Result<String, String> {
    let dir = std::env::temp_dir();
    let script = dir.join(format!("atticus-kwin-{}.js", std::process::id()));
    std::fs::write(&script, js).map_err(|e| e.to_string())?;
    let load = Command::new("gdbus")
        .args([
            "call", "--session",
            "--dest", "org.kde.KWin",
            "--object-path", "/Scripting",
            "--method", "org.kde.kwin.Scripting.loadScript",
            &script.to_string_lossy(),
        ])
        .output()
        .map_err(|e| e.to_string())?;
    let id_str = String::from_utf8_lossy(&load.stdout)
        .chars()
        .filter(|c| c.is_ascii_digit())
        .collect::<String>();
    if id_str.is_empty() {
        let _ = std::fs::remove_file(&script);
        return Err("KWin loadScript failed".into());
    }
    let obj = format!("/Scripting/Script{id_str}");
    let _ = Command::new("gdbus")
        .args(["call", "--session", "--dest", "org.kde.KWin", "--object-path", &obj, "--method", "org.kde.kwin.Script.run"])
        .output();
    let _ = Command::new("gdbus")
        .args(["call", "--session", "--dest", "org.kde.KWin", "--object-path", &obj, "--method", "org.kde.kwin.Script.stop"])
        .output();
    let _ = std::fs::remove_file(&script);
    Ok(id_str)
}

impl WindowManagerBackend for KWinBackend {
    fn backend_name(&self) -> &'static str {
        "kwin"
    }

    fn displays(&self) -> Vec<DisplayInfo> {
        let Ok(out) = Command::new("kscreen-doctor").arg("-j").output() else {
            return vec![];
        };
        let Ok(v) = serde_json::from_slice::<Value>(&out.stdout) else {
            return vec![];
        };
        let Some(outputs) = v["outputs"].as_array() else {
            return vec![];
        };
        outputs
            .iter()
            .filter(|o| o["enabled"].as_bool().unwrap_or(false))
            .map(|o| {
                let name = o["name"].as_str().unwrap_or("").to_string();
                let scale = o["scale"].as_f64().unwrap_or(1.0).max(0.1);
                let x = o["pos"]["x"].as_i64().unwrap_or(0) as i32;
                let y = o["pos"]["y"].as_i64().unwrap_or(0) as i32;
                let w = (o["size"]["width"].as_f64().unwrap_or(0.0) / scale).round() as i32;
                let h = (o["size"]["height"].as_f64().unwrap_or(0.0) / scale).round() as i32;
                DisplayInfo {
                    built_in: is_builtin_connector(&name),
                    primary: o["primary"].as_bool().unwrap_or(false),
                    name,
                    x,
                    y,
                    width: w,
                    height: h,
                    visible_x: x,
                    visible_y: y,
                    visible_width: w,
                    visible_height: h,
                }
            })
            .collect()
    }

    fn windows(&self) -> Vec<WindowInfo> {
        // KWin window list queried via org.kde.KWin /KWin or Atticus KWin helper D-Bus if loaded;
        // falls back gracefully to X11/XWayland windows if available.
        super::x11::X11Backend.windows()
    }

    fn active_window(&self) -> Option<WindowInfo> {
        self.windows().into_iter().find(|w| w.focused)
    }

    fn pointer_position(&self) -> Option<(i32, i32)> {
        super::x11::X11Backend.pointer_position()
    }

    fn focus_window(&self, win: &WindowInfo) -> Result<(), String> {
        let js = format!(
            "for (var w of workspace.windowList()) {{ if (w.pid === {}) {{ workspace.activeWindow = w; break; }} }}",
            win.pid
        );
        run_kwin_script(&js).map(|_| ())
    }

    fn move_resize_window(
        &self,
        win: &WindowInfo,
        x: i32,
        y: i32,
        w: i32,
        h: i32,
        fullscreen: bool,
    ) -> Result<(), String> {
        let js = format!(
            "for (var w of workspace.windowList()) {{ if (w.pid === {}) {{ w.fullScreen = {}; if (!{}) {{ w.frameGeometry = {{x:{x}, y:{y}, width:{w}, height:{h}}}; }} break; }} }}",
            win.pid,
            if fullscreen { "true" } else { "false" },
            if fullscreen { "true" } else { "false" },
        );
        run_kwin_script(&js).map(|_| ())
    }

    fn close_window(&self, win: &WindowInfo) -> Result<(), String> {
        let js = format!(
            "for (var w of workspace.windowList()) {{ if (w.pid === {}) {{ w.closeWindow(); break; }} }}",
            win.pid
        );
        run_kwin_script(&js).map(|_| ())
    }
}
