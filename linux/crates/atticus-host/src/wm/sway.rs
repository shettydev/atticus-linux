//! Sway / i3 IPC backend via `swaymsg`.

use super::{is_builtin_connector, DisplayInfo, WindowInfo, WindowManagerBackend};
use serde_json::Value;
use std::process::Command;

pub struct SwayBackend;

fn sway_json(msg_type: &str) -> Option<Value> {
    let out = Command::new("swaymsg").args(["-t", msg_type]).output().ok()?;
    if !out.status.success() {
        return None;
    }
    serde_json::from_slice(&out.stdout).ok()
}

fn collect_tree(node: &Value, workspace: &str, out: &mut Vec<WindowInfo>) {
    let ws = if node["type"].as_str() == Some("workspace") {
        node["name"].as_str().unwrap_or(workspace)
    } else {
        workspace
    };
    if let Some(pid) = node["pid"].as_i64() {
        if pid > 0 {
            let id_num = node["id"].as_u64().unwrap_or(0);
            let app_id = node["app_id"]
                .as_str()
                .or_else(|| node["window_properties"]["class"].as_str())
                .unwrap_or("")
                .to_string();
            let rect = &node["rect"];
            out.push(WindowInfo {
                id: id_num.to_string(),
                numeric_id: id_num,
                pid: pid as i32,
                app: app_id.clone(),
                wm_class: app_id,
                title: node["name"].as_str().unwrap_or("").to_string(),
                x: rect["x"].as_i64().unwrap_or(0) as i32,
                y: rect["y"].as_i64().unwrap_or(0) as i32,
                width: rect["width"].as_i64().unwrap_or(0) as i32,
                height: rect["height"].as_i64().unwrap_or(0) as i32,
                focused: node["focused"].as_bool().unwrap_or(false),
                minimized: !node["visible"].as_bool().unwrap_or(true),
                fullscreen: node["fullscreen_mode"].as_i64().unwrap_or(0) != 0,
                workspace: ws.to_string(),
            });
        }
    }
    for key in ["nodes", "floating_nodes"] {
        if let Some(children) = node[key].as_array() {
            for child in children {
                collect_tree(child, ws, out);
            }
        }
    }
}

impl WindowManagerBackend for SwayBackend {
    fn backend_name(&self) -> &'static str {
        "sway"
    }

    fn displays(&self) -> Vec<DisplayInfo> {
        let Some(Value::Array(outs)) = sway_json("get_outputs") else {
            return vec![];
        };
        outs.into_iter()
            .filter(|o| o["active"].as_bool().unwrap_or(true))
            .map(|o| {
                let name = o["name"].as_str().unwrap_or("").to_string();
                let r = &o["rect"];
                let (x, y, w, h) = (
                    r["x"].as_i64().unwrap_or(0) as i32,
                    r["y"].as_i64().unwrap_or(0) as i32,
                    r["width"].as_i64().unwrap_or(0) as i32,
                    r["height"].as_i64().unwrap_or(0) as i32,
                );
                DisplayInfo {
                    built_in: is_builtin_connector(&name),
                    primary: o["focused"].as_bool().unwrap_or(false),
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
        let Some(tree) = sway_json("get_tree") else {
            return vec![];
        };
        let mut out = Vec::new();
        collect_tree(&tree, "", &mut out);
        out
    }

    fn active_window(&self) -> Option<WindowInfo> {
        self.windows().into_iter().find(|w| w.focused)
    }

    fn pointer_position(&self) -> Option<(i32, i32)> {
        None
    }

    fn focus_window(&self, win: &WindowInfo) -> Result<(), String> {
        let cmd = format!("[con_id={}] focus", win.id);
        let st = Command::new("swaymsg").arg(&cmd).status().map_err(|e| e.to_string())?;
        if st.success() { Ok(()) } else { Err("swaymsg focus failed".into()) }
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
        let cmd = if fullscreen {
            format!("[con_id={}] fullscreen enable", win.id)
        } else {
            format!(
                "[con_id={}] floating enable, resize set width {w} px height {h} px, move position {x} px {y} px",
                win.id
            )
        };
        let st = Command::new("swaymsg").arg(&cmd).status().map_err(|e| e.to_string())?;
        if st.success() { Ok(()) } else { Err("swaymsg move/resize failed".into()) }
    }

    fn close_window(&self, win: &WindowInfo) -> Result<(), String> {
        let cmd = format!("[con_id={}] kill", win.id);
        let st = Command::new("swaymsg").arg(&cmd).status().map_err(|e| e.to_string())?;
        if st.success() { Ok(()) } else { Err("swaymsg kill failed".into()) }
    }
}
