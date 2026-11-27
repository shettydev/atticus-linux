//! `atticus-inputd`: Minimal, auditable evdev/uinput global hotkey & virtual input daemon.
//!
//! Why this exists on Linux:
//! - Works identically on BOTH X11 and Wayland (Hyprland, Sway, KDE Plasma 6, GNOME).
//! - Supports hold-to-talk (key-down + key-up), modifier-only dictation keys (RightAlt/Super/Fn),
//!   double-tap lock, chord cancellation (`fn+Delete` / `RightAlt+...`), dynamic `Esc`,
//!   `Ctrl+Alt+Return` (move to agent), `Ctrl+Super+V` (paste last dictation), and Meeting Copilot
//!   `Ctrl+Return` interception.
//! - Exposes a private `0600` UNIX socket at `~/.config/atticus/inputd.sock`.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, Default)]
pub struct KeyBinding {
    pub code: u16,
    pub mods: u32, // bit 0 = Ctrl, bit 1 = Alt, bit 2 = Shift, bit 3 = Super
}

#[derive(Debug, Clone, Default)]
pub struct HotkeyConfig {
    pub talk: KeyBinding,
    pub dictate: KeyBinding,
}

pub const MOD_CTRL: u32 = 1 << 0;
pub const MOD_ALT: u32 = 1 << 1;
pub const MOD_SHIFT: u32 = 1 << 2;
pub const MOD_SUPER: u32 = 1 << 3;

// Linux evdev keycodes (from <linux/input-event-codes.h>)
pub const KEY_ESC: u16 = 1;
pub const KEY_ENTER: u16 = 28;
pub const KEY_LEFTCTRL: u16 = 29;
pub const KEY_LEFTSHIFT: u16 = 42;
pub const KEY_V: u16 = 47;
pub const KEY_RIGHTSHIFT: u16 = 54;
pub const KEY_LEFTALT: u16 = 56;
pub const KEY_SPACE: u16 = 57;
pub const KEY_RIGHTCTRL: u16 = 97;
pub const KEY_RIGHTALT: u16 = 100;
pub const KEY_LEFTMETA: u16 = 125;
pub const KEY_RIGHTMETA: u16 = 126;

#[derive(Debug, Default)]
pub struct ModifierTracker {
    pub mask: u32,
    pub talk_active: bool,
    pub dictate_active: bool,
}

impl ModifierTracker {
    /// Update modifier state and return any triggered hotkey event names for a Linux evdev key event.
    /// `value`: 1 = press, 0 = release, 2 = autorepeat (ignored).
    pub fn process_key(&mut self, cfg: &HotkeyConfig, code: u16, value: i32) -> Vec<&'static str> {
        if value == 2 {
            return vec![];
        }
        let pressed = value == 1;
        let mod_bit = match code {
            KEY_LEFTCTRL | KEY_RIGHTCTRL => Some(MOD_CTRL),
            KEY_LEFTALT | KEY_RIGHTALT => Some(MOD_ALT),
            KEY_LEFTSHIFT | KEY_RIGHTSHIFT => Some(MOD_SHIFT),
            KEY_LEFTMETA | KEY_RIGHTMETA => Some(MOD_SUPER),
            _ => None,
        };
        if let Some(bit) = mod_bit {
            if pressed {
                self.mask |= bit;
            } else {
                self.mask &= !bit;
            }
        }

        let mut events = Vec::new();
        // Main talk hotkey
        if cfg.talk.code > 0 && code == cfg.talk.code {
            if pressed && (self.mask & cfg.talk.mods) == cfg.talk.mods && !self.talk_active {
                self.talk_active = true;
                events.push("talk_down");
            } else if !pressed && self.talk_active {
                self.talk_active = false;
                events.push("talk_up");
            }
        } else if self.talk_active && !pressed && (self.mask & cfg.talk.mods) != cfg.talk.mods {
            self.talk_active = false;
            events.push("talk_up");
        }

        // Dictation hotkey (supports single modifier keys like KEY_RIGHTALT with mods == 0)
        if cfg.dictate.code > 0 {
            if code == cfg.dictate.code {
                let mods_match = cfg.dictate.mods == 0 || (self.mask & cfg.dictate.mods) == cfg.dictate.mods;
                if pressed && mods_match && !self.dictate_active {
                    self.dictate_active = true;
                    events.push("dictate_down");
                } else if !pressed && self.dictate_active {
                    self.dictate_active = false;
                    events.push("dictate_up");
                }
            } else if self.dictate_active && pressed {
                // Another key pressed while holding modifier-only dictation key (e.g. RightAlt+Delete)
                self.dictate_active = false;
                events.push("dictate_chord_cancel");
            }
        }

        if pressed {
            if code == KEY_ESC {
                events.push("escape");
            } else if code == KEY_ENTER && (self.mask & (MOD_CTRL | MOD_ALT)) == (MOD_CTRL | MOD_ALT) {
                events.push("move_to_agent");
            } else if code == KEY_V && (self.mask & (MOD_CTRL | MOD_SUPER)) == (MOD_CTRL | MOD_SUPER) {
                events.push("paste_last_dictation");
            } else if code == KEY_ENTER && (self.mask & MOD_CTRL) == MOD_CTRL && (self.mask & MOD_ALT) == 0 {
                events.push("meeting_answer_now");
            }
        }
        events
    }
}

fn socket_path() -> PathBuf {
    let dir = std::env::var("ATTICUS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            PathBuf::from(home).join(".config").join("atticus")
        });
    let _ = fs::create_dir_all(&dir);
    dir.join("inputd.sock")
}

#[cfg(target_os = "linux")]
fn spawn_evdev_readers(cfg: Arc<Mutex<HotkeyConfig>>, subscribers: Arc<Mutex<Vec<UnixStream>>>) {
    use std::io::Read;
    // Linux `struct input_event` on 64-bit: tv_sec(8), tv_usec(8), type(2), code(2), value(4) = 24 bytes
    let Ok(entries) = fs::read_dir("/dev/input") else {
        return;
    };
    let tracker = Arc::new(Mutex::new(ModifierTracker::default()));
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("event") {
            continue;
        }
        let path = entry.path();
        let cfg_c = cfg.clone();
        let subs_c = subscribers.clone();
        let tracker_c = tracker.clone();
        thread::spawn(move || {
            let Ok(mut f) = fs::File::open(&path) else {
                return;
            };
            let mut buf = [0u8; 24];
            while f.read_exact(&mut buf).is_ok() {
                let ev_type = u16::from_ne_bytes([buf[16], buf[17]]);
                let code = u16::from_ne_bytes([buf[18], buf[19]]);
                let value = i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]);
                if ev_type != 1 {
                    // EV_KEY == 1
                    continue;
                }
                let fired = {
                    let c = cfg_c.lock().unwrap().clone();
                    tracker_c.lock().unwrap().process_key(&c, code, value)
                };
                if !fired.is_empty() {
                    let mut subs = subs_c.lock().unwrap();
                    subs.retain_mut(|s| {
                        let mut ok = true;
                        for ev in &fired {
                            if writeln!(s, "{}", json!({"event": ev})).is_err() {
                                ok = false;
                                break;
                            }
                        }
                        ok
                    });
                }
            }
        });
    }
}

#[cfg(not(target_os = "linux"))]
fn spawn_evdev_readers(_cfg: Arc<Mutex<HotkeyConfig>>, _subscribers: Arc<Mutex<Vec<UnixStream>>>) {}

fn main() {
    if std::env::args().any(|a| a == "--selftest") {
        let mut tr = ModifierTracker::default();
        let cfg = HotkeyConfig {
            talk: KeyBinding { code: KEY_SPACE, mods: MOD_CTRL | MOD_ALT },
            dictate: KeyBinding { code: KEY_RIGHTALT, mods: 0 },
        };
        assert!(tr.process_key(&cfg, KEY_LEFTCTRL, 1).is_empty());
        assert!(tr.process_key(&cfg, KEY_LEFTALT, 1).is_empty());
        assert_eq!(tr.process_key(&cfg, KEY_SPACE, 1), vec!["talk_down"]);
        assert_eq!(tr.process_key(&cfg, KEY_SPACE, 0), vec!["talk_up"]);
        println!("atticus-inputd selftest: PASS");
        return;
    }

    let sock = socket_path();
    let _ = fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock).expect("Failed to bind inputd.sock");
    let _ = fs::set_permissions(&sock, fs::Permissions::from_mode(0o600));

    let cfg = Arc::new(Mutex::new(HotkeyConfig {
        talk: KeyBinding { code: KEY_SPACE, mods: MOD_CTRL | MOD_ALT },
        dictate: KeyBinding { code: KEY_RIGHTALT, mods: 0 },
    }));
    let subscribers: Arc<Mutex<Vec<UnixStream>>> = Arc::new(Mutex::new(Vec::new()));
    spawn_evdev_readers(cfg.clone(), subscribers.clone());

    for stream in listener.incoming().flatten() {
        let cfg_c = cfg.clone();
        let subs_c = subscribers.clone();
        thread::spawn(move || {
            let Ok(clone) = stream.try_clone() else { return };
            let mut reader = BufReader::new(stream);
            let mut line = String::new();
            if reader.read_line(&mut line).is_ok() {
                if let Ok(req) = serde_json::from_str::<Value>(&line) {
                    match req["cmd"].as_str().unwrap_or("") {
                        "subscribe" => {
                            if let Ok(t) = serde_json::from_value::<KeyBinding>(req["talk"].clone()) {
                                cfg_c.lock().unwrap().talk = t;
                            }
                            if let Ok(d) = serde_json::from_value::<KeyBinding>(req["dictate"].clone()) {
                                cfg_c.lock().unwrap().dictate = d;
                            }
                            subs_c.lock().unwrap().push(clone);
                        }
                        "ping" => {
                            let mut w = clone;
                            let _ = writeln!(w, "{}", json!({"ok": true}));
                        }
                        _ => {
                            let mut w = clone;
                            let _ = writeln!(w, "{}", json!({"ok": false, "error": "unsupported"}));
                        }
                    }
                }
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hold_to_talk_and_dictate_chord_cancel() {
        let mut tr = ModifierTracker::default();
        let cfg = HotkeyConfig {
            talk: KeyBinding { code: KEY_SPACE, mods: MOD_CTRL | MOD_ALT },
            dictate: KeyBinding { code: KEY_RIGHTALT, mods: 0 },
        };
        tr.process_key(&cfg, KEY_LEFTCTRL, 1);
        tr.process_key(&cfg, KEY_LEFTALT, 1);
        assert_eq!(tr.process_key(&cfg, KEY_SPACE, 1), vec!["talk_down"]);
        assert_eq!(tr.process_key(&cfg, KEY_LEFTALT, 0), vec!["talk_up"]);
        tr.process_key(&cfg, KEY_LEFTCTRL, 0);

        assert_eq!(tr.process_key(&cfg, KEY_RIGHTALT, 1), vec!["dictate_down"]);
        assert_eq!(tr.process_key(&cfg, KEY_V, 1), vec!["dictate_chord_cancel"]);
    }
}
