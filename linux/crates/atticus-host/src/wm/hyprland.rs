//! Hyprland Wayland IPC backend via UNIX socket / `hyprctl`.

use super::{is_builtin_connector, DisplayInfo, WindowInfo, WindowManagerBackend};
use serde_json::Value;
use std::process::Command;

pub struct HyprlandBackend;

fn hypr_json(args: &[&str]) -> Option<Value> {
    let out = Command::new("hyprctl").args(args).arg("-j").output().ok()?;
    if !out.status.success() {
        return None;
    }
    serde_json::from_slice(&out.stdout).ok()
}

impl WindowManagerBackend for HyprlandBackend {
    fn backend_name(&self) -> &'static str {
        "hyprland"
    }

    fn displays(&self) -> Vec<DisplayInfo> {
        let Some(Value::Array(mons)) = hypr_json(&["monitors"]) else {
            return vec![];
        };
        mons.into_iter()
            .map(|m| {
                let name = m["name"].as_str().unwrap_or("").to_string();
                let scale = m["scale"].as_f64().unwrap_or(1.0).max(0.1);
                let x = m["x"].as_i64().unwrap_or(0) as i32;
                let y = m["y"].as_i64().unwrap_or(0) as i32;
                let w = (m["width"].as_f64().unwrap_or(0.0) / scale).round() as i32;
                let h = (m["height"].as_f64().unwrap_or(0.0) / scale).round() as i32;
                let res = m["reserved"].as_array();
                let (rl, rt, rr, rb) = res
                    .filter(|a| a.len() == 4)
                    .map(|a| {
                        (
                            a[0].as_i64().unwrap_or(0) as i32,
                            a[1].as_i64().unwrap_or(0) as i32,
                            a[2].as_i64().unwrap_or(0) as i32,
                            a[3].as_i64().unwrap_or(0) as i32,
                        )
                    })
                    .unwrap_or((0, 0, 0, 0));
                DisplayInfo {
                    built_in: is_builtin_connector(&name),
                    primary: m["focused"].as_bool().unwrap_or(false),
                    name,
                    x,
                    y,
                    width: w,
                    height: h,
                    visible_x: x + rl,
                    visible_y: y + rt,
                    visible_width: (w - rl - rr).max(1),
                    visible_height: (h - rt - rb).max(1),
                }
            })
            .collect()
    }

    fn windows(&self) -> Vec<WindowInfo> {
        let Some(Value::Array(clients)) = hypr_json(&["clients"]) else {
            return vec![];
        };
        let active_addr = hypr_json(&["activewindow"])
            .and_then(|v| v["address"].as_str().map(|s| s.to_string()))
            .unwrap_or_default();
        clients
            .into_iter()
            .map(|c| {
                let id = c["address"].as_str().unwrap_or("").to_string();
                let numeric_id = u64::from_str_radix(id.trim_start_matches("0x"), 16).unwrap_or(0);
                let at = c["at"].as_array();
                let sz = c["size"].as_array();
                let cls = c["class"].as_str().unwrap_or("").to_string();
                WindowInfo {
                    focused: !active_addr.is_empty() && id == active_addr,
                    id,
                    numeric_id,
                    pid: c["pid"].as_i64().unwrap_or(0) as i32,
                    app: cls.clone(),
                    wm_class: cls,
                    title: c["title"].as_str().unwrap_or("").to_string(),
                    x: at.and_then(|a| a.first()?.as_i64()).unwrap_or(0) as i32,
                    y: at.and_then(|a| a.get(1)?.as_i64()).unwrap_or(0) as i32,
                    width: sz.and_then(|a| a.first()?.as_i64()).unwrap_or(0) as i32,
                    height: sz.and_then(|a| a.get(1)?.as_i64()).unwrap_or(0) as i32,
                    minimized: c["hidden"].as_bool().unwrap_or(false),
                    fullscreen: c["fullscreen"].as_i64().unwrap_or(0) != 0,
                    workspace: c["workspace"]["name"].as_str().unwrap_or("").to_string(),
                }
            })
            .collect()
    }

    fn active_window(&self) -> Option<WindowInfo> {
        self.windows().into_iter().find(|w| w.focused)
    }

    fn pointer_position(&self) -> Option<(i32, i32)> {
        let v = hypr_json(&["cursorpos"])?;
        Some((v["x"].as_i64()? as i32, v["y"].as_i64()? as i32))
    }

    fn focus_window(&self, win: &WindowInfo) -> Result<(), String> {
        let target = format!("address:{}", win.id);
        let st = Command::new("hyprctl")
            .args(["dispatch", "focuswindow", &target])
            .status()
            .map_err(|e| e.to_string())?;
        if st.success() { Ok(()) } else { Err("hyprctl focuswindow failed".into()) }
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
        let addr = format!("address:{}", win.id);
        let batch = if fullscreen {
            format!("dispatch focuswindow {addr}; dispatch fullscreen 0")
        } else {
            format!(
                "dispatch setfloating {addr}; dispatch resizewindowpixel exact {w} {h},{addr}; dispatch movewindowpixel exact {x} {y},{addr}"
            )
        };
        let st = Command::new("hyprctl")
            .args(["--batch", &batch])
            .status()
            .map_err(|e| e.to_string())?;
        if st.success() { Ok(()) } else { Err("hyprctl move/resize failed".into()) }
    }

    fn close_window(&self, win: &WindowInfo) -> Result<(), String> {
        let target = format!("address:{}", win.id);
        let st = Command::new("hyprctl")
            .args(["dispatch", "closewindow", &target])
            .status()
            .map_err(|e| e.to_string())?;
        if st.success() { Ok(()) } else { Err("hyprctl closewindow failed".into()) }
    }
}
