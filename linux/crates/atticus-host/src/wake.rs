//! Hands-free Wake Word state machine & word-boundary matcher (mirrors Atticus.swift wakeTick).

use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct WakeState {
    pub enabled: bool,
    pub wake_word: String,
    pub hands_free: bool,
    pub follow_up: bool,
    pub last_activity: Instant,
    pub backoff: Duration,
}

impl Default for WakeState {
    fn default() -> Self {
        Self {
            enabled: false,
            wake_word: "Atticus".into(),
            hands_free: false,
            follow_up: false,
            last_activity: Instant::now(),
            backoff: Duration::from_secs(1),
        }
    }
}

impl WakeState {
    /// Returns true if wake listening should be active right now (suspended when busy/talking/meeting/paused).
    pub fn should_listen(
        &self,
        paused: bool,
        meeting_running: bool,
        busy: bool,
        talking: bool,
        dictating: bool,
        speaking: bool,
        box_visible: bool,
        onboarding_open: bool,
    ) -> bool {
        self.enabled
            && !paused
            && !meeting_running
            && !busy
            && !talking
            && !dictating
            && !speaking
            && !box_visible
            && !onboarding_open
    }

    /// Check if idle hands-free chat should fade after 90 seconds of silence.
    pub fn should_fade_idle(&self, user_moved_box: bool) -> bool {
        self.hands_free && !user_moved_box && self.last_activity.elapsed() >= Duration::from_secs(90)
    }

    pub fn touch(&mut self) {
        self.last_activity = Instant::now();
    }

    pub fn bump_backoff(&mut self) {
        self.backoff = (self.backoff * 2).min(Duration::from_secs(30));
    }

    pub fn reset_backoff(&mut self) {
        self.backoff = Duration::from_secs(1);
    }
}

/// Case-insensitive word-boundary match for `"<wake>"` or `"hey <wake>"`.
pub fn has_wake_word(transcript: &str, wake_word: &str) -> bool {
    let target = wake_word.trim().to_lowercase();
    if target.is_empty() {
        return false;
    }
    let words: Vec<String> = transcript
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect();
    let target_words: Vec<&str> = target.split_whitespace().collect();
    if target_words.is_empty() || words.len() < target_words.len() {
        return false;
    }
    words
        .windows(target_words.len())
        .any(|win| win.iter().zip(&target_words).all(|(a, b)| a == b))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_wake_word_on_word_boundaries() {
        assert!(has_wake_word("Hey Atticus, open Spotify", "Atticus"));
        assert!(has_wake_word("atticus!", "Atticus"));
        assert!(!has_wake_word("dramaticus", "Atticus"));
    }
}
