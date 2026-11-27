//! GNOME Shell Wayland backend talking to our companion extension `atticus@atticus.app`
//! over session D-Bus (`org.atticus.GnomeHelper`).

use super::{DisplayInfo, WindowInfo, WindowManagerBackend};
use std::process::Command;

pub struct GnomeShellBackend;

fn dbus_call(method: &str, args: &[&str]) -> Option<String> {
    let mut cmd = Command::new("gdbus");
    cmd.args([
        "call",
        "--session",
        "--dest",
        "org.gnome.Shell",
        "--object-path",
        "/org/atticus/GnomeHelper",
        "--method",
        &format!("org.atticus.GnomeHelper.{method}"),
    ]);
    for a in args {
        cmd.arg(a);
    }
    let out = cmd.output().ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    // Unwrap gdbus GVariant tuple: ('...',)
    let inner = s.strip_prefix("('")?.strip_suffix("',)")?;
    Some(inner.replace("\\'", "'").replace("\\\\", "\\"))
}

impl WindowManagerBackend for GnomeShellBackend {
    fn backend_name(&self) -> &'static str {
        "gnome-shell"
    }

    fn displays(&self) -> Vec<DisplayInfo> {
        if let Some(json_str) = dbus_call("ListDisplays", &[]) {
            if let Ok(list) = serde_json::from_str::<Vec<DisplayInfo>>(&json_str) {
                return list;
            }
        }
        super::x11::X11Backend.displays()
    }

    fn windows(&self) -> Vec<WindowInfo> {
        if let Some(json_str) = dbus_call("ListWindows", &[]) {
            if let Ok(list) = serde_json::from_str::<Vec<WindowInfo>>(&json_str) {
                return list;
            }
        }
        super::x11::X11Backend.windows()
    }

    fn active_window(&self) -> Option<WindowInfo> {
        self.windows().into_iter().find(|w| w.focused)
    }

    fn pointer_position(&self) -> Option<(i32, i32)> {
        let json_str = dbus_call("PointerPosition", &[])?;
        let v: serde_json::Value = serde_json::from_str(&json_str).ok()?;
        Some((v["x"].as_i64()? as i32, v["y"].as_i64()? as i32))
    }

    fn focus_window(&self, win: &WindowInfo) -> Result<(), String> {
        dbus_call("FocusWindow", &[&win.id])
            .map(|_| ())
            .ok_or_else(|| "GNOME Shell Atticus helper extension is not enabled".into())
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
        let args = [
            win.id.as_str(),
            &x.to_string(),
            &y.to_string(),
            &w.to_string(),
            &h.to_string(),
            if fullscreen { "true" } else { "false" },
        ];
        dbus_call("MoveResizeWindow", &args)
            .map(|_| ())
            .ok_or_else(|| "GNOME Shell Atticus helper extension is not enabled".into())
    }

    fn close_window(&self, win: &WindowInfo) -> Result<(), String> {
        dbus_call("CloseWindow", &[&win.id])
            .map(|_| ())
            .ok_or_else(|| "GNOME Shell Atticus helper extension is not enabled".into())
    }
}
