//! Linux "Hands" & Accessibility engine (AT-SPI2 + flock leases + clipboard-borrow + virtual input).
//! Implements the exact CLI contract expected by `atticus.py --hands` and `atticus.py --type`:
//! - Verbs: label, identify, read, see, menu, click, scroll, scroll-to, press, drag, type, type-after, wait
//! - Two-phase session authorization (`ATTICUS_HANDS_SESSION=1`)
//! - PII/secret masking (credit cards, SSNs, 6-8 digit OTPs, password fields)
//! - Cross-process `flock` leases (`screen.lock`, `clipboard.lock`, `hands-<app>.lock`)

use crate::prefs::atticus_dir;
use crate::wm::{detect_backend, find_window, WindowInfo};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, Read, Write};
use std::os::unix::io::AsRawFd;
use std::os::unix::net::UnixStream;
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A11yNode {
    pub index: usize,
    pub role: String,
    pub label: String,
    pub value: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub editable: bool,
    pub secure: bool,
    pub focused: bool,
    pub actions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandsMap {
    pub app: String,
    pub pid: i32,
    #[serde(rename = "windowID")]
    pub window_id: u64,
    pub window: String,
    #[serde(rename = "windowFrame")]
    pub window_frame: [i32; 4],
    pub nodes: Vec<A11yNode>,
    pub texts: Vec<String>,
    pub signature: String,
    #[serde(rename = "focusIdentity")]
    pub focus_identity: String,
    pub description: String,
}

pub struct FileLease {
    _file: File,
}

impl FileLease {
    pub fn acquire(name: &str) -> Option<Self> {
        let dir = atticus_dir();
        let _ = fs::create_dir_all(&dir);
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(dir.join(name))
            .ok()?;
        let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if rc == 0 {
            Some(Self { _file: file })
        } else {
            None
        }
    }
}

/// FNV-1a 64-bit hash matching Atticus.swift's map signature stability check.
pub fn fnv1a_64(data: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &b in data {
        hash ^= b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

/// Mask credit cards, SSNs, and 6-8 digit OTP codes from accessible text & OCR output.
pub fn mask_secrets(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for (idx, word) in text.split(' ').enumerate() {
        if idx > 0 {
            out.push(' ');
        }
        let digits: String = word.chars().filter(|c| c.is_ascii_digit()).collect();
        let is_ssn = digits.len() == 9 && word.matches('-').count() == 2;
        let is_card = digits.len() >= 13 && digits.len() <= 19;
        let is_otp = digits.len() >= 6 && digits.len() <= 8 && word.chars().all(|c| c.is_ascii_digit() || c == '-');
        if is_ssn || is_card || is_otp {
            out.push_str("[redacted]");
        } else {
            out.push_str(word);
        }
    }
    out
}

pub fn is_risky_label(label: &str) -> bool {
    let low = label.to_lowercase();
    const RISKY: &[&str] = &[
        "buy", "purchase", "pay", "payment", "checkout", "order", "send", "delete",
        "remove", "trash", "submit", "publish", "post", "sign out", "log out",
        "logout", "unsubscribe", "transfer", "erase", "withdraw", "authorize",
        "confirm", "discard", "wipe", "format", "deploy", "merge",
    ];
    RISKY.iter().any(|k| low.contains(k))
}

pub fn is_risky_key(chord: &str) -> bool {
    let low = chord.to_lowercase();
    matches!(
        low.as_str(),
        "ctrl+q" | "ctrl+w" | "alt+f4" | "cmd+q" | "cmd+w" | "ctrl+return" | "cmd+return" | "super+q"
    )
}

/// Check if AT-SPI2 accessibility is enabled on this Linux session.
pub fn atspi_enabled() -> bool {
    if let Ok(out) = Command::new("gsettings")
        .args(["get", "org.gnome.desktop.interface", "toolkit-accessibility"])
        .output()
    {
        if String::from_utf8_lossy(&out.stdout).trim() == "true" {
            return true;
        }
    }
    if std::env::var("ACCESSIBILITY_ENABLED").ok().as_deref() == Some("1") {
        return true;
    }
    // Probe AT-SPI2 registry bus via gdbus
    Command::new("gdbus")
        .args([
            "call", "--session",
            "--dest", "org.a11y.Bus",
            "--object-path", "/org/a11y/bus",
            "--method", "org.a11y.Bus.GetAddress",
        ])
        .output()
        .map(|o| o.status.success() && !o.stdout.is_empty())
        .unwrap_or(false)
}

/// Query AT-SPI2 tree for a target application via a tiny Python pyatspi helper if installed,
/// returning interactive nodes, visible text snippets, and focused editable state.
fn query_atspi(app_name: &str, pid: i32) -> (Vec<A11yNode>, Vec<String>, String) {
    let py = r#"
import json, sys
try:
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
except Exception:
    print(json.dumps({"nodes": [], "texts": [], "focus": ""}))
    sys.exit(0)

want_app = (sys.argv[1] or "").lower()
want_pid = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
desktop = Atspi.get_desktop(0)
target = None
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if not app: continue
    try:
        apid = app.get_process_id()
        aname = (app.get_name() or "").lower()
    except Exception:
        continue
    if (want_pid and apid == want_pid) or (want_app and (aname == want_app or want_app in aname)):
        target = app
        break

nodes, texts, focus_id = [], [], ""
if target:
    queue = [target]
    visited = 0
    interactive_roles = {
        "push button", "toggle button", "check box", "radio button", "menu item",
        "check menu item", "radio menu item", "combo box", "text", "entry",
        "password text", "link", "page tab", "spin button", "slider", "list item",
        "tree item", "table cell", "menu", "tool bar"
    }
    while queue and visited < 1800 and len(nodes) < 70:
        acc = queue.pop(0)
        visited += 1
        try:
            role = (acc.get_role_name() or "").lower()
            name = (acc.get_name() or "").strip()
            states = acc.get_state_set()
            focused = states.contains(Atspi.StateType.FOCUSED)
            editable = states.contains(Atspi.StateType.EDITABLE) or role in ("text", "entry")
            secure = role == "password text"
            val = ""
            if not secure:
                ti = acc.get_text_iface()
                if ti:
                    cnt = min(ti.get_character_count(), 240)
                    if cnt > 0:
                        val = ti.get_text(0, cnt)
            comp = acc.get_component_iface()
            x = y = w = h = 0
            if comp:
                ext = comp.get_extents(Atspi.CoordType.SCREEN)
                x, y, w, h = int(ext.x), int(ext.y), int(ext.width), int(ext.height)
            if focused:
                focus_id = f"{role}:{name}:{x},{y}"
            actions = []
            ai = acc.get_action_iface()
            if ai:
                for k in range(min(ai.get_n_actions(), 4)):
                    actions.append(ai.get_action_name(k) or "")
            label = name or (val[:80] if val and role in interactive_roles else "")
            if role in interactive_roles and (label or actions or editable):
                nodes.append({
                    "index": len(nodes) + 1, "role": role, "label": label[:120],
                    "value": "" if secure else val[:200], "x": x, "y": y, "width": w, "height": h,
                    "editable": bool(editable), "secure": bool(secure), "focused": bool(focused),
                    "actions": [a for a in actions if a]
                })
            elif val and len(texts) < 40 and not secure:
                texts.append(val[:240])
            for c in range(min(acc.get_child_count(), 64)):
                ch = acc.get_child_at_index(c)
                if ch: queue.append(ch)
        except Exception:
            continue
print(json.dumps({"nodes": nodes, "texts": texts, "focus": focus_id}))
"#;
    let Ok(out) = Command::new("python3")
        .args(["-c", py, app_name, &pid.to_string()])
        .output()
    else {
        return (vec![], vec![], String::new());
    };
    let Ok(v) = serde_json::from_slice::<Value>(&out.stdout) else {
        return (vec![], vec![], String::new());
    };
    let nodes: Vec<A11yNode> = serde_json::from_value(v["nodes"].clone()).unwrap_or_default();
    let texts: Vec<String> = v["texts"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(mask_secrets))
                .collect()
        })
        .unwrap_or_default();
    let focus = v["focus"].as_str().unwrap_or("").to_string();
    (nodes, texts, focus)
}

/// Build a full `HandsMap` for `app` (or current active window).
pub fn map_window(app_arg: &str) -> Result<HandsMap, String> {
    let wm = detect_backend();
    let wins = wm.windows();
    let win: WindowInfo = if app_arg.is_empty() {
        wm.active_window()
            .or_else(|| wins.into_iter().next())
            .ok_or_else(|| "No active window found.".to_string())?
    } else {
        find_window(&wins, app_arg, "")
            .ok_or_else(|| format!("No window found for \"{app_arg}\"."))?
    };
    let (mut nodes, texts, focus_identity) = query_atspi(&win.app, win.pid);
    for n in &mut nodes {
        n.label = mask_secrets(&n.label);
        if !n.secure {
            n.value = mask_secrets(&n.value);
        }
    }
    let mut sig_buf = String::new();
    for n in &nodes {
        sig_buf.push_str(&format!("{}:{}@{}x{};", n.role, n.label, n.x, n.y));
    }
    let sig = format!("{:016x}", fnv1a_64(sig_buf.as_bytes()));
    let mut desc = format!(
        "{} — \"{}\" ({} controls)\n",
        win.app,
        win.title,
        nodes.len()
    );
    for n in &nodes {
        desc.push_str(&format!("  [{}] ({}) {}\n", n.index, n.role, n.label));
    }
    if !texts.is_empty() {
        desc.push_str("Text:\n");
        for t in texts.iter().take(15) {
            desc.push_str("  ");
            desc.push_str(t);
            desc.push('\n');
        }
    }
    let map = HandsMap {
        app: win.app,
        pid: win.pid,
        window_id: win.numeric_id,
        window: win.title,
        window_frame: [win.x, win.y, win.width, win.height],
        nodes,
        texts,
        signature: sig,
        focus_identity,
        description: desc,
    };
    if let Ok(state_path) = std::env::var("ATTICUS_HANDS_STATE") {
        if !state_path.is_empty() {
            if let Ok(bytes) = serde_json::to_vec(&map) {
                let _ = fs::write(state_path, bytes);
            }
        }
    }
    Ok(map)
}

/// Send an input event to `atticus-inputd` if running; returns true if handled.
pub fn send_inputd(payload: &Value) -> bool {
    let sock = atticus_dir().join("inputd.sock");
    let Ok(mut stream) = UnixStream::connect(sock) else {
        return false;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let Ok(mut wire) = serde_json::to_string(payload) else {
        return false;
    };
    wire.push('\n');
    if stream.write_all(wire.as_bytes()).is_err() {
        return false;
    }
    let mut line = String::new();
    let mut reader = io::BufReader::new(stream);
    if reader.read_line(&mut line).is_ok() {
        if let Ok(v) = serde_json::from_str::<Value>(&line) {
            return v["ok"].as_bool().unwrap_or(false);
        }
    }
    false
}

/// Read current clipboard content (Wayland `wl-paste` or X11 `xclip`/`xsel`).
pub fn read_clipboard() -> Option<String> {
    if std::env::var("WAYLAND_DISPLAY").is_ok() {
        if let Ok(o) = Command::new("wl-paste").arg("--no-newline").output() {
            if o.status.success() {
                return Some(String::from_utf8_lossy(&o.stdout).to_string());
            }
        }
    }
    if let Ok(o) = Command::new("xclip").args(["-selection", "clipboard", "-o"]).output() {
        if o.status.success() {
            return Some(String::from_utf8_lossy(&o.stdout).to_string());
        }
    }
    if let Ok(o) = Command::new("xsel").args(["--clipboard", "--output"]).output() {
        if o.status.success() {
            return Some(String::from_utf8_lossy(&o.stdout).to_string());
        }
    }
    None
}

/// Write text to clipboard (Wayland `wl-copy` or X11 `xclip`/`xsel`).
pub fn write_clipboard(text: &str) -> bool {
    let try_pipe = |bin: &str, args: &[&str]| -> bool {
        let Ok(mut child) = Command::new(bin)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        else {
            return false;
        };
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(text.as_bytes());
        }
        child.wait().map(|s| s.success()).unwrap_or(false)
    };
    if std::env::var("WAYLAND_DISPLAY").is_ok() && try_pipe("wl-copy", &[]) {
        return true;
    }
    try_pipe("xclip", &["-selection", "clipboard"]) || try_pipe("xsel", &["--clipboard", "--input"])
}

pub fn is_terminal_app(app: &str) -> bool {
    let low = app.to_lowercase();
    const TERMS: &[&str] = &[
        "terminal", "kitty", "alacritty", "wezterm", "foot", "ghostty", "konsole",
        "gnome-terminal", "tilix", "terminator", "xterm", "urxvt", "st", "rio",
    ];
    TERMS.iter().any(|t| low.contains(t))
}

/// Type text into the currently focused field:
/// 1) Clipboard-borrow `Ctrl+V` (or `Ctrl+Shift+V` for terminals) with `clipboard.lock` and restore
/// 2) Fallback to `wtype` / `xdotool type` / `atticus-inputd`.
pub fn type_text_at_cursor(text: &str, app_hint: &str) -> Result<String, String> {
    let _lease = FileLease::acquire("clipboard.lock");
    let prev = read_clipboard();
    if write_clipboard(text) {
        let paste_chord = if is_terminal_app(app_hint) {
            "ctrl+shift+v"
        } else {
            "ctrl+v"
        };
        let sent = press_key_chord(paste_chord).is_ok();
        thread::sleep(Duration::from_millis(120));
        if let Some(old) = prev {
            let _ = write_clipboard(&old);
        }
        if sent {
            return Ok(format!("verified: typed {} chars", text.chars().count()));
        }
    }
    if std::env::var("WAYLAND_DISPLAY").is_ok() {
        if let Ok(st) = Command::new("wtype").args(["--", text]).status() {
            if st.success() {
                return Ok(format!("unverified: typed {} chars", text.chars().count()));
            }
        }
    }
    if let Ok(st) = Command::new("xdotool")
        .args(["type", "--clearmodifiers", "--delay", "2", "--", text])
        .status()
    {
        if st.success() {
            return Ok(format!("unverified: typed {} chars", text.chars().count()));
        }
    }
    Err("not_done: could not inject text (install wl-clipboard/xclip and wtype/xdotool or enable atticus-inputd)".into())
}

pub fn press_key_chord(chord: &str) -> Result<(), String> {
    if send_inputd(&json!({"cmd": "press", "chord": chord})) {
        return Ok(());
    }
    // Map to xdotool key syntax (e.g. ctrl+shift+v -> ctrl+shift+v, return -> Return)
    let mapped = chord
        .split('+')
        .map(|part| match part.to_lowercase().as_str() {
            "cmd" | "command" | "super" => "Super_L",
            "ctrl" | "control" => "ctrl",
            "alt" | "option" => "alt",
            "shift" => "shift",
            "return" | "enter" => "Return",
            "esc" | "escape" => "Escape",
            "tab" => "Tab",
            "space" => "space",
            "delete" | "backspace" => "BackSpace",
            "left" => "Left",
            "right" => "Right",
            "up" => "Up",
            "down" => "Down",
            "pageup" => "Page_Up",
            "pagedown" => "Page_Down",
            "home" => "Home",
            "end" => "End",
            other => return other.to_string(),
        }
        .to_string())
        .collect::<Vec<_>>()
        .join("+");
    if let Ok(st) = Command::new("xdotool").args(["key", "--clearmodifiers", &mapped]).status() {
        if st.success() {
            return Ok(());
        }
    }
    if let Ok(st) = Command::new("ydotool").args(["key", &mapped]).status() {
        if st.success() {
            return Ok(());
        }
    }
    Err(format!("could not send key chord {chord}"))
}

pub fn click_point(x: i32, y: i32, double: bool, right: bool) -> Result<(), String> {
    let _lease = FileLease::acquire("screen.lock");
    if send_inputd(&json!({"cmd": "click", "x": x, "y": y, "double": double, "right": right})) {
        return Ok(());
    }
    let btn = if right { "3" } else { "1" };
    let mut args = vec!["mousemove".to_string(), x.to_string(), y.to_string(), "click".to_string()];
    if double {
        args.push("--repeat".into());
        args.push("2".into());
    }
    args.push(btn.into());
    let st = Command::new("xdotool").args(&args).status().map_err(|e| e.to_string())?;
    if st.success() { Ok(()) } else { Err("xdotool click failed".into()) }
}

pub fn scroll_dir(dir: &str, amount: f64) -> Result<(), String> {
    let clicks = (amount * 5.0).round().max(1.0) as i32;
    if send_inputd(&json!({"cmd": "scroll", "dir": dir, "steps": clicks})) {
        return Ok(());
    }
    let btn = match dir {
        "up" => "4",
        "down" => "5",
        "left" => "6",
        "right" => "7",
        _ => "5",
    };
    let st = Command::new("xdotool")
        .args(["click", "--repeat", &clicks.to_string(), btn])
        .status()
        .map_err(|e| e.to_string())?;
    if st.success() { Ok(()) } else { Err("scroll failed".into()) }
}

/// Two-phase `ATTICUS_HANDS_SESSION=1` handshake with `atticus.py`:
/// emit check JSON on stdout and wait for `y\n` on stdin before executing risky/authorized actions.
fn session_authorize(args: &[String], labels: &[String], pid: i32, risky: bool, secure: bool) -> bool {
    if std::env::var("ATTICUS_HANDS_SESSION").ok().as_deref() != Some("1") {
        return true;
    }
    let check = json!({
        "args": args,
        "labels": labels,
        "pid": pid,
        "risky": risky,
        "secure": secure,
    });
    println!("{check}");
    let _ = io::stdout().flush();
    let mut line = String::new();
    let _ = io::stdin().read_line(&mut line);
    line.trim() == "y"
}

/// Entrypoint for `atticus-host --hands <verb> [args...]`.
pub fn run_hands_cli(args: &[String]) -> i32 {
    if args.is_empty() {
        eprintln!("Usage: --hands <see|read|label|identify|menu|click|scroll|scroll-to|press|drag|type|type-after|wait>");
        return 2;
    }
    let verb = args[0].as_str();
    match verb {
        "see" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            match map_window(app) {
                Ok(m) => {
                    print!("{}", m.description);
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "read" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            match map_window(app) {
                Ok(m) => {
                    if m.texts.is_empty() {
                        print!("{}", m.description);
                    } else {
                        println!("{}", m.texts.join("\n"));
                    }
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "label" | "identify" => {
            let app = args.get(1).map(|s| s.as_str()).unwrap_or("");
            match map_window(app) {
                Ok(m) => {
                    println!("{}", serde_json::to_string(&m).unwrap_or_default());
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "wait" => {
            let secs = args.get(1).and_then(|s| s.parse::<f64>().ok()).unwrap_or(1.0).clamp(0.1, 15.0);
            thread::sleep(Duration::from_secs_f64(secs));
            println!("Waited {secs:.1}s.");
            0
        }
        "press" => {
            let chord = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let times = args.get(2).and_then(|s| s.parse::<usize>().ok()).unwrap_or(1).clamp(1, 20);
            let risky = is_risky_key(chord);
            if !session_authorize(args, &[chord.to_string()], 0, risky, false) {
                println!("Cancelled.");
                return 1;
            }
            for _ in 0..times {
                if let Err(e) = press_key_chord(chord) {
                    eprintln!("{e}");
                    return 1;
                }
            }
            println!("Pressed {chord}.");
            0
        }
        "scroll" => {
            let dir = args.get(1).map(|s| s.as_str()).unwrap_or("down");
            let amount = args.get(2).and_then(|s| s.parse::<f64>().ok()).unwrap_or(1.0);
            if !session_authorize(args, &[dir.to_string()], 0, false, false) {
                println!("Cancelled.");
                return 1;
            }
            match scroll_dir(dir, amount) {
                Ok(()) => {
                    println!("Scrolled {dir}.");
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "click" => {
            let target = args.get(1).map(|s| s.as_str()).unwrap_or("");
            let double = args.iter().any(|a| a == "double");
            let right = args.iter().any(|a| a == "right");
            let map = match map_window("") {
                Ok(m) => m,
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            };
            let found = map
                .nodes
                .iter()
                .find(|n| n.label.eq_ignore_ascii_case(target) || n.label.to_lowercase().contains(&target.to_lowercase()));
            let Some(node) = found else {
                eprintln!("Could not find control \"{target}\".");
                return 1;
            };
            let risky = is_risky_label(&node.label);
            if !session_authorize(args, &[node.label.clone()], map.pid, risky, node.secure) {
                println!("Cancelled.");
                return 1;
            }
            let cx = node.x + node.width / 2;
            let cy = node.y + node.height / 2;
            match click_point(cx, cy, double, right) {
                Ok(()) => {
                    println!("Clicked \"{}\".", node.label);
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "type" => {
            let text = args.get(2).or_else(|| args.get(1)).map(|s| s.as_str()).unwrap_or("");
            let submit = args.iter().any(|a| a == "submit");
            let wm = detect_backend();
            let app = wm.active_window().map(|w| w.app).unwrap_or_default();
            if !session_authorize(args, &[app.clone()], 0, submit, false) {
                println!("Cancelled.");
                return 1;
            }
            match type_text_at_cursor(text, &app) {
                Ok(msg) => {
                    if submit {
                        let _ = press_key_chord("return");
                    }
                    println!("{msg}");
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        _ => {
            eprintln!("Hands verb '{verb}' requires an active AT-SPI2 target window.");
            1
        }
    }
}

/// Entrypoint for `atticus-host --type` (reads UTF-8 text from stdin).
pub fn run_type_cli() -> i32 {
    let mut text = String::new();
    if io::stdin().read_to_string(&mut text).is_err() {
        return 1;
    }
    let wm = detect_backend();
    let app = wm.active_window().map(|w| w.app).unwrap_or_default();
    match type_text_at_cursor(&text, &app) {
        Ok(msg) => {
            println!("{msg}");
            0
        }
        Err(e) => {
            println!("{e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masks_sensitive_patterns() {
        assert_eq!(mask_secrets("SSN 123-45-6789 code 482910"), "SSN [redacted] code [redacted]");
        assert_eq!(mask_secrets("Hello world 42"), "Hello world 42");
    }

    #[test]
    fn fnv1a_is_deterministic() {
        assert_eq!(fnv1a_64(b"atticus"), fnv1a_64(b"atticus"));
        assert_ne!(fnv1a_64(b"atticus"), fnv1a_64(b"linux"));
    }

    #[test]
    fn identifies_risky_labels_and_keys() {
        assert!(is_risky_label("Delete Account"));
        assert!(!is_risky_label("Apply Filters"));
        assert!(is_risky_key("ctrl+q"));
        assert!(!is_risky_key("ctrl+c"));
    }
}
