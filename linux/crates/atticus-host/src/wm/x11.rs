//! X11 EWMH + XRandR backend (works on i3, XFCE, Cinnamon, MATE, GNOME-X11, KDE-X11, Xvfb).

use super::{is_builtin_connector, DisplayInfo, WindowInfo, WindowManagerBackend};
use std::process::Command;

pub struct X11Backend;

impl WindowManagerBackend for X11Backend {
    fn backend_name(&self) -> &'static str {
        "x11"
    }

    fn displays(&self) -> Vec<DisplayInfo> {
        let out = Command::new("xrandr").arg("--query").output();
        let Ok(out) = out else { return vec![] };
        if !out.status.success() {
            return vec![];
        }
        let mut res = Vec::new();
        for line in String::from_utf8_lossy(&out.stdout).lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 3 || parts[1] != "connected" {
                continue;
            }
            let name = parts[0].to_string();
            let primary = parts.get(2) == Some(&"primary");
            let geom_str = if primary { parts.get(3) } else { parts.get(2) };
            let Some(g) = geom_str else { continue };
            if let Some((w, h, x, y)) = parse_xrandr_geom(g) {
                let d = DisplayInfo {
                    built_in: is_builtin_connector(&name),
                    name,
                    x,
                    y,
                    width: w,
                    height: h,
                    visible_x: x,
                    visible_y: y,
                    visible_width: w,
                    visible_height: h,
                    primary,
                };
                if primary {
                    res.insert(0, d);
                } else {
                    res.push(d);
                }
            }
        }
        res
    }

    fn windows(&self) -> Vec<WindowInfo> {
        // wmctrl -lpGx: <wid> <desktop> <pid> <x> <y> <w> <h> <wm_class> <host> <title...>
        let Ok(out) = Command::new("wmctrl").arg("-lpGx").output() else {
            return vec![];
        };
        if !out.status.success() {
            return vec![];
        }
        let active_id = active_xid();
        let mut res = Vec::new();
        for line in String::from_utf8_lossy(&out.stdout).lines() {
            let parts: Vec<&str> = line.splitn(10, char::is_whitespace).filter(|s| !s.is_empty()).collect();
            if parts.len() < 9 {
                continue;
            }
            let id = parts[0].to_string();
            let numeric_id = u64::from_str_radix(id.trim_start_matches("0x"), 16).unwrap_or(0);
            let workspace = parts[1].to_string();
            let pid = parts[2].parse::<i32>().unwrap_or(0);
            let x = parts[3].parse::<i32>().unwrap_or(0);
            let y = parts[4].parse::<i32>().unwrap_or(0);
            let width = parts[5].parse::<i32>().unwrap_or(0);
            let height = parts[6].parse::<i32>().unwrap_or(0);
            let wm_class_raw = parts[7];
            let wm_class = wm_class_raw.split('.').last().unwrap_or(wm_class_raw).to_string();
            let title = parts.get(9).copied().unwrap_or("").trim().to_string();
            res.push(WindowInfo {
                focused: active_id == Some(numeric_id) && numeric_id != 0,
                app: wm_class.clone(),
                wm_class,
                id,
                numeric_id,
                pid,
                title,
                x,
                y,
                width,
                height,
                minimized: false,
                fullscreen: false,
                workspace,
            });
        }
        res
    }

    fn active_window(&self) -> Option<WindowInfo> {
        let wins = self.windows();
        wins.into_iter().find(|w| w.focused)
    }

    fn pointer_position(&self) -> Option<(i32, i32)> {
        let out = Command::new("xdotool")
            .args(["getmouselocation", "--shell"])
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let mut x = None;
        let mut y = None;
        for line in String::from_utf8_lossy(&out.stdout).lines() {
            if let Some(v) = line.strip_prefix("X=") {
                x = v.trim().parse().ok();
            } else if let Some(v) = line.strip_prefix("Y=") {
                y = v.trim().parse().ok();
            }
        }
        Some((x?, y?))
    }

    fn focus_window(&self, win: &WindowInfo) -> Result<(), String> {
        let st = Command::new("wmctrl")
            .args(["-i", "-a", &win.id])
            .status()
            .map_err(|e| e.to_string())?;
        if st.success() {
            Ok(())
        } else {
            Err("wmctrl -a failed".into())
        }
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
        // Clear maximized/fullscreen hints first so EWMH move/resize takes effect cleanly
        let _ = Command::new("wmctrl")
            .args(["-i", "-r", &win.id, "-b", "remove,maximized_vert,maximized_horz,fullscreen"])
            .status();
        let geom = format!("0,{x},{y},{w},{h}");
        let st = Command::new("wmctrl")
            .args(["-i", "-r", &win.id, "-e", &geom])
            .status()
            .map_err(|e| e.to_string())?;
        if !st.success() {
            return Err("wmctrl -e failed".into());
        }
        if fullscreen {
            let _ = Command::new("wmctrl")
                .args(["-i", "-r", &win.id, "-b", "add,fullscreen"])
                .status();
        }
        Ok(())
    }

    fn close_window(&self, win: &WindowInfo) -> Result<(), String> {
        let st = Command::new("wmctrl")
            .args(["-i", "-c", &win.id])
            .status()
            .map_err(|e| e.to_string())?;
        if st.success() {
            Ok(())
        } else {
            Err("wmctrl -c failed".into())
        }
    }
}

fn active_xid() -> Option<u64> {
    let out = Command::new("xprop")
        .args(["-root", "_NET_ACTIVE_WINDOW"])
        .output()
        .ok()?;
    let txt = String::from_utf8_lossy(&out.stdout);
    let hex = txt.split("0x").nth(1)?.split_whitespace().next()?;
    u64::from_str_radix(hex.trim_end_matches(','), 16).ok()
}

fn parse_xrandr_geom(g: &str) -> Option<(i32, i32, i32, i32)> {
    let (wh, rest) = g.split_once('+')?;
    let (w, h) = wh.split_once('x')?;
    let (x, y) = rest.split_once('+')?;
    Some((w.parse().ok()?, h.parse().ok()?, x.parse().ok()?, y.parse().ok()?))
}
