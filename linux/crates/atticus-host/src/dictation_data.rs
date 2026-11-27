//! Dictation archive (~/.config/atticus/dictation.json), stats, phonetic correction matching,
//! and terminal/unreadable-field keystroke Replay (mirrors Atticus.swift DictationData).

use crate::prefs::{atticus_dir, write_atomic_json};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_ITEMS: usize = 500;
const MAX_AGE_SECS: f64 = 30.0 * 86400.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DictationItem {
    pub id: String,
    pub text: String,
    pub raw: String,
    pub app: String,
    pub ts: f64,
    pub words: usize,
    pub secs: f64,
    pub inserted: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DictationArchive {
    pub items: Vec<DictationItem>,
    pub total_words: usize,
    pub total_sessions: usize,
    pub total_secs: f64,
}

impl DictationArchive {
    pub fn load() -> Self {
        let path = atticus_dir().join("dictation.json");
        fs::read_to_string(path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self) -> std::io::Result<()> {
        let path = atticus_dir().join("dictation.json");
        let val = serde_json::to_value(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        write_atomic_json(&path, &val)
    }

    pub fn record(&mut self, text: &str, raw: &str, app: &str, secs: f64, inserted: bool, keep_history: bool) {
        let now = now_secs();
        let words = text.split_whitespace().count();
        self.total_words += words;
        self.total_sessions += 1;
        self.total_secs += secs.max(0.0);
        if keep_history {
            self.items.insert(
                0,
                DictationItem {
                    id: format!("{:x}", (now * 1000.0) as u64),
                    text: text.to_string(),
                    raw: raw.to_string(),
                    app: app.to_string(),
                    ts: now,
                    words,
                    secs,
                    inserted,
                },
            );
            self.items.retain(|it| now - it.ts <= MAX_AGE_SECS);
            if self.items.len() > MAX_ITEMS {
                self.items.truncate(MAX_ITEMS);
            }
        }
        let _ = self.save();
    }

    pub fn delete_item(&mut self, id: &str) {
        self.items.retain(|it| it.id != id);
        let _ = self.save();
    }

    pub fn clear_history(&mut self) {
        self.items.clear();
        let _ = self.save();
    }

    pub fn last_text(&self) -> Option<&str> {
        self.items.first().map(|it| it.text.as_str())
    }
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Soundex-like phonetic signature matching Atticus.swift `DictationData.sound`.
pub fn phonetic_sound(word: &str) -> String {
    let letters: Vec<char> = word
        .to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphabetic())
        .collect();
    if letters.is_empty() {
        return String::new();
    }
    let map = |c: char| -> Option<char> {
        match c {
            'b' | 'f' | 'p' | 'v' => Some('1'),
            'c' | 'g' | 'j' | 'k' | 'q' | 's' | 'x' | 'z' => Some('2'),
            'd' | 't' => Some('3'),
            'l' => Some('4'),
            'm' | 'n' => Some('5'),
            'r' => Some('6'),
            _ => None,
        }
    };
    let mut out = String::new();
    out.push(letters[0]);
    let mut prev = map(letters[0]);
    for &c in &letters[1..] {
        let code = map(c);
        if code.is_some() && code != prev {
            out.push(code.unwrap());
        }
        prev = code;
    }
    out
}

pub fn levenshtein(a: &str, b: &str) -> usize {
    let av: Vec<char> = a.chars().collect();
    let bv: Vec<char> = b.chars().collect();
    let mut prev: Vec<usize> = (0..=bv.len()).collect();
    let mut curr = vec![0; bv.len() + 1];
    for (i, &ca) in av.iter().enumerate() {
        curr[0] = i + 1;
        for (j, &cb) in bv.iter().enumerate() {
            let cost = usize::from(ca != cb);
            curr[j + 1] = (prev[j + 1] + 1).min(curr[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    prev[bv.len()]
}

/// Detect a single word correction (1-3 heard words -> 1 edited word) after insertion.
pub fn detect_correction(before: &str, after: &str) -> Option<(String, String)> {
    let bw: Vec<&str> = before.split_whitespace().collect();
    let aw: Vec<&str> = after.split_whitespace().collect();
    if bw == aw || bw.is_empty() || aw.is_empty() {
        return None;
    }
    let mut prefix = 0;
    while prefix < bw.len() && prefix < aw.len() && bw[prefix] == aw[prefix] {
        prefix += 1;
    }
    let mut suffix = 0;
    while suffix < bw.len() - prefix
        && suffix < aw.len() - prefix
        && bw[bw.len() - 1 - suffix] == aw[aw.len() - 1 - suffix]
    {
        suffix += 1;
    }
    let heard = &bw[prefix..bw.len() - suffix];
    let fixed = &aw[prefix..aw.len() - suffix];
    if heard.is_empty() || heard.len() > 3 || fixed.len() != 1 {
        return None;
    }
    let heard_str = heard.join(" ");
    let fixed_str = fixed[0].trim_matches(|c: char| !c.is_alphanumeric() && c != '-' && c != '\'');
    let heard_clean = heard_str.trim_matches(|c: char| !c.is_alphanumeric() && c != ' ');
    if fixed_str.len() < 3 || heard_clean.eq_ignore_ascii_case(fixed_str) {
        return None;
    }
    let hs = phonetic_sound(&heard_clean.replace(' ', ""));
    let fs = phonetic_sound(fixed_str);
    let dist = levenshtein(&heard_clean.to_lowercase(), &fixed_str.to_lowercase());
    let max_len = heard_clean.len().max(fixed_str.len());
    if hs == fs || dist * 2 <= max_len {
        return Some((heard_clean.to_string(), fixed_str.to_string()));
    }
    None
}

/// Apply deterministic whole-word replacements from user's `dictateFixes` map.
pub fn apply_fixes(text: &str, fixes: &HashMap<String, String>) -> String {
    if fixes.is_empty() {
        return text.to_string();
    }
    let mut out = text.to_string();
    for (wrong, right) in fixes {
        if wrong.is_empty() || right.is_empty() {
            continue;
        }
        // Simple case-insensitive token replacement preserving initial capitalization
        let words: Vec<String> = out
            .split(' ')
            .map(|tok| {
                let stripped = tok.trim_matches(|c: char| !c.is_alphanumeric());
                if stripped.eq_ignore_ascii_case(wrong) {
                    let replacement = if stripped.chars().next().map(|c| c.is_uppercase()).unwrap_or(false) {
                        let mut c = right.chars();
                        match c.next() {
                            Some(first) => first.to_uppercase().collect::<String>() + c.as_str(),
                            None => right.clone(),
                        }
                    } else {
                        right.clone()
                    };
                    tok.replacen(stripped, &replacement, 1)
                } else {
                    tok.to_string()
                }
            })
            .collect();
        out = words.join(" ");
    }
    out
}

/// In-memory keystroke replay buffer for self-learning in terminals/unreadable fields.
pub struct KeystrokeReplay {
    chars: Vec<char>,
    cursor: usize,
}

impl KeystrokeReplay {
    pub fn new(initial: &str) -> Self {
        let chars: Vec<char> = initial.chars().collect();
        let cursor = chars.len();
        Self { chars, cursor }
    }

    pub fn text(&self) -> String {
        self.chars.iter().collect()
    }

    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.chars.remove(self.cursor);
        }
    }

    pub fn delete_word_backward(&mut self) {
        while self.cursor > 0 && self.chars[self.cursor - 1].is_whitespace() {
            self.backspace();
        }
        while self.cursor > 0 && !self.chars[self.cursor - 1].is_whitespace() {
            self.backspace();
        }
    }

    pub fn forward_delete(&mut self) {
        if self.cursor < self.chars.len() {
            self.chars.remove(self.cursor);
        }
    }

    pub fn move_left(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
        }
    }

    pub fn move_right(&mut self) {
        if self.cursor < self.chars.len() {
            self.cursor += 1;
        }
    }

    pub fn insert_char(&mut self, c: char) {
        self.chars.insert(self.cursor, c);
        self.cursor += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_phonetic_correction() {
        let c = detect_correction("Open attic us now", "Open Atticus now");
        assert_eq!(c, Some(("attic us".into(), "Atticus".into())));
    }

    #[test]
    fn replays_terminal_edits() {
        let mut r = KeystrokeReplay::new("hello wrld");
        r.delete_word_backward();
        for c in "world".chars() {
            r.insert_char(c);
        }
        assert_eq!(r.text(), "hello world");
    }
}
