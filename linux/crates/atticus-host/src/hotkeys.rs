//! Global hotkey client for `atticus-host`:
//! Connects to `atticus-inputd` (`~/.config/atticus/inputd.sock`) for full Wayland+X11 parity
//! (hold-to-talk, press-to-toggle, modifier-only dictation, double-tap lock, chord cancellation,
//! dynamic Esc / Return / Ctrl+Alt+Return / Ctrl+Super+V / Meeting Ctrl+Return), with fallback
//! to XDG GlobalShortcuts portal notifications.

use crate::prefs::atticus_dir;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::thread;
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "event")]
pub enum HotkeyEvent {
    #[serde(rename = "talk_down")]
    TalkDown,
    #[serde(rename = "talk_up")]
    TalkUp,
    #[serde(rename = "dictate_down")]
    DictateDown,
    #[serde(rename = "dictate_up")]
    DictateUp,
    #[serde(rename = "dictate_chord_cancel")]
    DictateChordCancel,
    #[serde(rename = "escape")]
    Escape,
    #[serde(rename = "move_to_agent")]
    MoveToAgent,
    #[serde(rename = "paste_last_dictation")]
    PasteLastDictation,
    #[serde(rename = "meeting_answer_now")]
    MeetingAnswerNow,
    #[serde(rename = "recorded")]
    Recorded { code: u16, mods: u32, label: String },
}

/// Double-tap lock & hold-vs-toggle state machine mirroring `Atticus.swift`.
#[derive(Debug, Default)]
pub struct ShortcutState {
    pub talk_down_at: Option<Instant>,
    pub dictate_down_at: Option<Instant>,
    pub last_dictate_tap_at: Option<Instant>,
    pub dictate_locked: bool,
    pub toggle_talking: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShortcutAction {
    None,
    StartTalk,
    StopTalk,
    KeepTypingBox,
    StartDictate,
    LockDictate,
    StopDictate,
    CancelDictateSilently,
}

impl ShortcutState {
    pub fn on_talk_down(&mut self, toggle_mode: bool) -> ShortcutAction {
        if toggle_mode {
            if self.toggle_talking {
                self.toggle_talking = false;
                return ShortcutAction::StopTalk;
            }
            self.toggle_talking = true;
            return ShortcutAction::StartTalk;
        }
        self.talk_down_at = Some(Instant::now());
        ShortcutAction::StartTalk
    }

    pub fn on_talk_up(&mut self, toggle_mode: bool, has_words: bool, type_right: bool) -> ShortcutAction {
        if toggle_mode {
            return ShortcutAction::None;
        }
        let elapsed = self
            .talk_down_at
            .take()
            .map(|t| t.elapsed())
            .unwrap_or_default();
        if elapsed < Duration::from_millis(300) && !has_words {
            if type_right {
                return ShortcutAction::KeepTypingBox;
            }
        }
        ShortcutAction::StopTalk
    }

    pub fn on_dictate_down(&mut self) -> ShortcutAction {
        if self.dictate_locked {
            self.dictate_locked = false;
            return ShortcutAction::StopDictate;
        }
        if let Some(prev) = self.last_dictate_tap_at {
            if prev.elapsed() <= Duration::from_millis(300) {
                self.last_dictate_tap_at = None;
                self.dictate_locked = true;
                return ShortcutAction::LockDictate;
            }
        }
        self.dictate_down_at = Some(Instant::now());
        ShortcutAction::StartDictate
    }

    pub fn on_dictate_up(&mut self, has_words: bool) -> ShortcutAction {
        if self.dictate_locked {
            return ShortcutAction::None;
        }
        let elapsed = self
            .dictate_down_at
            .take()
            .map(|t| t.elapsed())
            .unwrap_or_default();
        if elapsed < Duration::from_millis(300) && !has_words {
            self.last_dictate_tap_at = Some(Instant::now());
            return ShortcutAction::None;
        }
        ShortcutAction::StopDictate
    }

    pub fn on_dictate_chord_cancel(&mut self, has_words: bool) -> ShortcutAction {
        if !has_words {
            self.dictate_down_at = None;
            self.dictate_locked = false;
            return ShortcutAction::CancelDictateSilently;
        }
        ShortcutAction::None
    }
}

/// Spawn a background reader connected to `atticus-inputd` (`~/.config/atticus/inputd.sock`).
pub fn spawn_inputd_listener<F>(talk_code: u16, talk_mods: u32, dictate_code: u16, dictate_mods: u32, mut on_event: F)
where
    F: FnMut(HotkeyEvent) + Send + 'static,
{
    thread::spawn(move || {
        let sock_path = atticus_dir().join("inputd.sock");
        loop {
            if let Ok(mut stream) = UnixStream::connect(&sock_path) {
                let sub = json!({
                    "cmd": "subscribe",
                    "talk": {"code": talk_code, "mods": talk_mods},
                    "dictate": {"code": dictate_code, "mods": dictate_mods}
                });
                if writeln!(stream, "{sub}").is_ok() {
                    let reader = BufReader::new(stream);
                    for line in reader.lines().map_while(Result::ok) {
                        if let Ok(ev) = serde_json::from_str::<HotkeyEvent>(&line) {
                            on_event(ev);
                        }
                    }
                }
            }
            thread::sleep(Duration::from_secs(2));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn double_tap_locks_dictation_and_next_press_stops() {
        let mut st = ShortcutState::default();
        assert_eq!(st.on_dictate_down(), ShortcutAction::StartDictate);
        assert_eq!(st.on_dictate_up(false), ShortcutAction::None);
        assert_eq!(st.on_dictate_down(), ShortcutAction::LockDictate);
        assert!(st.dictate_locked);
        assert_eq!(st.on_dictate_up(false), ShortcutAction::None);
        assert_eq!(st.on_dictate_down(), ShortcutAction::StopDictate);
        assert!(!st.dictate_locked);
    }

    #[test]
    fn toggle_mode_alternates_talk() {
        let mut st = ShortcutState::default();
        assert_eq!(st.on_talk_down(true), ShortcutAction::StartTalk);
        assert_eq!(st.on_talk_up(true, false, true), ShortcutAction::None);
        assert_eq!(st.on_talk_down(true), ShortcutAction::StopTalk);
    }
}
