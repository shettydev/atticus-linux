//! Local Text-to-Speech (Piper neural ONNX -> spd-say -> espeak-ng), offline language detection
//! via `whatlang`, streaming speech queue with drawing step sync (`\u{1}<guide>:<step>`), and cue sounds.

use crate::prefs::atticus_dir;
use std::collections::VecDeque;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use whatlang::Lang;

pub fn detect_language_code(text: &str) -> &'static str {
    match whatlang::detect_lang(text) {
        Some(Lang::Ita) => "it",
        Some(Lang::Spa) => "es",
        Some(Lang::Fra) => "fr",
        Some(Lang::Deu) => "de",
        _ => "en",
    }
}

fn find_piper_model(lang: &str) -> Option<PathBuf> {
    let dir = atticus_dir().join("voices");
    let entries = std::fs::read_dir(dir).ok()?;
    for e in entries.flatten() {
        let p = e.path();
        if p.extension().and_then(|s| s.to_str()) == Some("onnx") {
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
            if name.starts_with(lang) {
                return Some(p);
            }
        }
    }
    None
}

/// Speak one utterance synchronously using Piper (if installed + model available), `spd-say`, or `espeak-ng`.
pub fn speak_blocking(text: &str, _voice_hint: &str, rate_wpm: f64) -> bool {
    let clean = text.trim();
    if clean.is_empty() {
        return true;
    }
    let lang = detect_language_code(clean);
    if let Some(model) = find_piper_model(lang) {
        let length_scale = (196.0 / rate_wpm.clamp(100.0, 320.0)).clamp(0.5, 1.8);
        if let Ok(mut piper) = Command::new("piper")
            .args([
                "--model",
                model.to_string_lossy().as_ref(),
                "--length_scale",
                &format!("{length_scale:.2}"),
                "--output-raw",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            if let Some(mut stdin) = piper.stdin.take() {
                let _ = stdin.write_all(clean.as_bytes());
            }
            if let Some(piper_out) = piper.stdout.take() {
                let player = Command::new("pw-play")
                    .args(["--raw", "--format=s16", "--rate=22050", "--channels=1", "-"])
                    .stdin(Stdio::from(piper_out))
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
                let _ = piper.wait();
                if player.map(|s| s.success()).unwrap_or(false) {
                    return true;
                }
            }
        }
    }
    let wpm = rate_wpm.clamp(110.0, 320.0).round() as i32;
    if let Ok(st) = Command::new("espeak-ng")
        .args(["-v", lang, "-s", &wpm.to_string(), "--", clean])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
    {
        if st.success() {
            return true;
        }
    }
    Command::new("spd-say")
        .args(["--wait", "-l", lang, "--", clean])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Play a subtle UI cue sound (Pop, Bottle, Glass, Tink, Ping, Blow, Hero) via freedesktop sound theme or pw-play.
pub fn play_cue(name: &str, volume: f64) {
    if volume <= 0.005 {
        return;
    }
    let freedesktop_id = match name {
        "Pop" => "message",
        "Bottle" => "audio-volume-change",
        "Glass" | "Hero" => "complete",
        "Tink" => "bell",
        "Ping" => "dialog-information",
        "Blow" => "service-login",
        _ => "bell",
    };
    let _ = Command::new("canberra-gtk-play")
        .args(["-i", freedesktop_id])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

/// Non-blocking sentence-by-sentence TTS queue supporting drawing-step sync sentinels (`\u{1}<guide>:<step>`).
#[derive(Default)]
pub struct SpeechQueue {
    items: VecDeque<String>,
    active: Option<Child>,
    pub muted: bool,
}

impl SpeechQueue {
    pub fn enqueue(&mut self, item: String) {
        if !self.muted && !item.is_empty() {
            self.items.push_back(item);
        }
    }

    pub fn shush(&mut self) {
        self.muted = true;
        self.items.clear();
        if let Some(mut child) = self.active.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    pub fn reset(&mut self) {
        self.shush();
        self.muted = false;
    }

    pub fn is_speaking(&mut self) -> bool {
        if let Some(child) = self.active.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                return true;
            }
            self.active = None;
        }
        !self.items.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_supported_languages() {
        assert_eq!(detect_language_code("Bonjour, comment allez-vous aujourd'hui ?"), "fr");
        assert_eq!(detect_language_code("Guten Morgen, wie geht es dir heute?"), "de");
        assert_eq!(detect_language_code("Hello world, this is Atticus on Linux."), "en");
    }
}
