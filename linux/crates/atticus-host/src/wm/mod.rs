//! Pluggable Linux Window Manager & Display backend:
//! auto-detects Hyprland, Sway, KDE Plasma (KWin), GNOME Shell extension, and X11 (EWMH/XRandR).

pub mod gnome;
pub mod hyprland;
pub mod kwin;
pub mod sway;
pub mod x11;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DisplayInfo {
    pub name: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub visible_x: i32,
    pub visible_y: i32,
    pub visible_width: i32,
    pub visible_height: i32,
    pub built_in: bool,
    pub primary: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowInfo {
    pub id: String,
    pub numeric_id: u64,
    pub pid: i32,
    pub app: String,
    pub wm_class: String,
    pub title: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub focused: bool,
    pub minimized: bool,
    pub fullscreen: bool,
    pub workspace: String,
}

impl WindowInfo {
    pub fn to_target_json(&self, visible: bool) -> Value {
        serde_json::json!({
            "pid": self.pid,
            "windowID": self.numeric_id,
            "windowFrame": [self.x, self.y, self.width, self.height],
            "window": self.title,
            "app": self.app,
            "minimized": self.minimized,
            "visible": visible,
        })
    }
}

pub trait WindowManagerBackend: Send + Sync {
    fn backend_name(&self) -> &'static str;
    fn displays(&self) -> Vec<DisplayInfo>;
    fn windows(&self) -> Vec<WindowInfo>;
    fn active_window(&self) -> Option<WindowInfo>;
    fn pointer_position(&self) -> Option<(i32, i32)>;
    fn focus_window(&self, win: &WindowInfo) -> Result<(), String>;
    fn move_resize_window(
        &self,
        win: &WindowInfo,
        x: i32,
        y: i32,
        w: i32,
        h: i32,
        fullscreen: bool,
    ) -> Result<(), String>;
    fn close_window(&self, win: &WindowInfo) -> Result<(), String>;
}

pub fn detect_backend() -> Box<dyn WindowManagerBackend> {
    if std::env::var("HYPRLAND_INSTANCE_SIGNATURE")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
    {
        return Box::new(hyprland::HyprlandBackend);
    }
    if std::env::var("SWAYSOCK")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
    {
        return Box::new(sway::SwayBackend);
    }
    let desktop = std::env::var("XDG_CURRENT_DESKTOP")
        .unwrap_or_default()
        .to_lowercase();
    let session = std::env::var("XDG_SESSION_TYPE")
        .unwrap_or_default()
        .to_lowercase();
    if session == "wayland" && desktop.contains("kde") {
        return Box::new(kwin::KWinBackend);
    }
    if session == "wayland" && desktop.contains("gnome") {
        return Box::new(gnome::GnomeShellBackend);
    }
    Box::new(x11::X11Backend)
}

pub fn is_builtin_connector(name: &str) -> bool {
    let up = name.to_uppercase();
    up.starts_with("EDP") || up.starts_with("LVDS") || up.starts_with("DSI")
}

/// Find the best matching user window for `app` (and optional title substring), excluding Atticus itself.
pub fn find_window(windows: &[WindowInfo], app: &str, title: &str) -> Option<WindowInfo> {
    let want_app = app.trim().to_lowercase();
    let want_title = title.trim().to_lowercase();
    let mut matches: Vec<&WindowInfo> = windows
        .iter()
        .filter(|w| {
            let low_app = w.app.to_lowercase();
            let low_cls = w.wm_class.to_lowercase();
            if low_cls.contains("atticus") || low_app == "atticus" || low_app == "atticus-host" {
                return false;
            }
            let app_ok = want_app.is_empty()
                || low_app == want_app
                || low_cls == want_app
                || low_app.contains(&want_app)
                || low_cls.contains(&want_app)
                || want_app.contains(&low_cls) && !low_cls.is_empty();
            if !app_ok {
                return false;
            }
            want_title.is_empty() || w.title.to_lowercase().contains(&want_title)
        })
        .collect();
    matches.sort_by_key(|w| (!w.focused, w.minimized));
    matches.first().map(|w| (*w).clone())
}

/// Compute target placement rectangle and move/resize a window, returning the PLACED receipt expected by atticus.py.
pub fn place_window(
    backend: &dyn WindowManagerBackend,
    proc_name: &str,
    where_: &str,
    screen_sel: &str,
    title: &str,
) -> String {
    let native_fs = where_ == "fullscreen";
    let mode = if native_fs { "full" } else { where_ };
    if !matches!(mode, "left" | "right" | "full" | "center") {
        return "where must be left, right, full, fullscreen or center".into();
    }
    let wins = backend.windows();
    let Some(win) = find_window(&wins, proc_name, title) else {
        return format!("no window found for \"{proc_name}\" {title}").trim().to_string();
    };
    let disps = backend.displays();
    if disps.is_empty() {
        return format!("NOT DONE: display geometry unavailable for {proc_name}");
    }
    let cx = win.x + win.width / 2;
    let cy = win.y + win.height / 2;
    let target_disp = match screen_sel {
        "laptop" => disps.iter().find(|d| d.built_in),
        "monitor" => disps.iter().find(|d| !d.built_in),
        "current" | "" => disps
            .iter()
            .find(|d| cx >= d.x && cx < d.x + d.width && cy >= d.y && cy < d.y + d.height),
        exact => disps.iter().find(|d| d.name.eq_ignore_ascii_case(exact)),
    }
    .unwrap_or(&disps[0]);

    let (vx, vy, vw, vh) = (
        target_disp.visible_x,
        target_disp.visible_y,
        target_disp.visible_width.max(1),
        target_disp.visible_height.max(1),
    );
    let cw = win.width.clamp(200, vw);
    let ch = win.height.clamp(200, vh);
    let (bx, by, bw, bh) = match mode {
        "left" => (vx, vy, vw / 2, vh),
        "right" => (vx + vw / 2, vy, vw - vw / 2, vh),
        "full" => (vx, vy, vw, vh),
        "center" => (vx + (vw - cw) / 2, vy + (vh - ch) / 2, cw, ch),
        _ => (vx, vy, vw, vh),
    };

    if let Err(e) = backend.move_resize_window(&win, bx, by, bw, bh, native_fs) {
        return format!("NOT DONE: could not move {proc_name} window: {e}");
    }
    format!(
        "{proc_name} window -> {} of {}",
        if native_fs { "fullscreen" } else { mode },
        target_disp.name
    )
}

/// 20x20 grid occlusion check: returns true if the window is on-screen and <=25% covered by higher windows.
pub fn is_window_visible(target: &WindowInfo, all_in_z_order_top_first: &[WindowInfo]) -> bool {
    if target.minimized || target.width <= 0 || target.height <= 0 {
        return false;
    }
    let mut above = Vec::new();
    for w in all_in_z_order_top_first {
        if w.id == target.id {
            break;
        }
        if !w.minimized && w.workspace == target.workspace {
            above.push(w);
        }
    }
    if above.is_empty() {
        return true;
    }
    let cols = 20;
    let rows = 20;
    let mut covered = 0usize;
    for r in 0..rows {
        let py = target.y + ((r * target.height) + target.height / 2) / rows;
        for c in 0..cols {
            let px = target.x + ((c * target.width) + target.width / 2) / cols;
            if above
                .iter()
                .any(|w| px >= w.x && px < w.x + w.width && py >= w.y && py < w.y + w.height)
            {
                covered += 1;
            }
        }
    }
    covered * 4 <= (cols * rows) as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn occlusion_grid_detects_covered_window() {
        let top = WindowInfo {
            id: "1".into(), numeric_id: 1, pid: 10, app: "Cover".into(), wm_class: "cover".into(),
            title: "Cover".into(), x: 0, y: 0, width: 800, height: 600, focused: true,
            minimized: false, fullscreen: false, workspace: "1".into(),
        };
        let under = WindowInfo {
            id: "2".into(), numeric_id: 2, pid: 20, app: "Back".into(), wm_class: "back".into(),
            title: "Back".into(), x: 100, y: 100, width: 400, height: 300, focused: false,
            minimized: false, fullscreen: false, workspace: "1".into(),
        };
        let list = [top.clone(), under];
        assert!(!is_window_visible(&list[1], &list));
        assert!(is_window_visible(&top, &list));
    }
}
