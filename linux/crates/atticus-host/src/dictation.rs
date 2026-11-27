//! Dictate Anywhere coordinator:
//! - Pre-warms `atticus.py --dictate-cleanup-wait`
//! - Applies deterministic `dictateFixes`
//! - Inserts via AT-SPI2 / clipboard-borrow (`Ctrl+V` or `Ctrl+Shift+V` for terminals)
//! - Saves to `~/.config/atticus/dictation.json` (never clobbers clipboard on failure)
//! - Runs 20s post-insertion correction self-learning.

use crate::a11y::type_text_at_cursor;
use crate::dictation_data::{apply_fixes, DictationArchive};
use crate::prefs::Prefs;
use crate::py_bridge::py_run;
use std::collections::HashMap;

pub struct DictationOutcome {
    pub text: String,
    pub inserted: bool,
    pub status: String,
}

/// Run the full dictation finalization pipeline (cleanup or verbatim -> fixes -> insert -> archive).
pub fn finish_dictation(
    prefs: &Prefs,
    raw_transcript: &str,
    before_cursor: &str,
    front_app: &str,
    duration_secs: f64,
) -> DictationOutcome {
    let raw = raw_transcript.trim();
    if raw.is_empty() {
        return DictationOutcome {
            text: String::new(),
            inserted: false,
            status: "empty".into(),
        };
    }

    let mode = prefs.get_str("dictateMode", "cleanup");
    let mut final_text = if mode == "cleanup" {
        let stdin_payload = format!("{before_cursor}\0{raw}");
        match py_run(&["--dictate-cleanup"], Some(&stdin_payload)) {
            Ok((0, out, _)) if !out.trim().is_empty() => out.trim().to_string(),
            _ => raw.to_string(),
        }
    } else {
        raw.to_string()
    };

    // Apply user's deterministic dictionary fixes
    if let Some(obj) = prefs.get("dictateFixes").and_then(|v| v.as_object().cloned()) {
        let fixes: HashMap<String, String> = obj
            .into_iter()
            .filter_map(|(k, v)| v.as_str().map(|s| (k, s.to_string())))
            .collect();
        final_text = apply_fixes(&final_text, &fixes);
    }

    let (inserted, status) = match type_text_at_cursor(&final_text, front_app) {
        Ok(msg) => (true, msg),
        Err(msg) => (false, msg),
    };

    let mut archive = DictationArchive::load();
    archive.record(
        &final_text,
        raw,
        front_app,
        duration_secs,
        inserted,
        prefs.get_bool("dictateHistory", true),
    );

    DictationOutcome {
        text: final_text,
        inserted,
        status,
    }
}

/// Paste the most recent dictation (`Ctrl+Super+V` shortcut / tray menu item).
pub fn paste_last_dictation(front_app: &str) -> Result<String, String> {
    let archive = DictationArchive::load();
    let Some(last) = archive.last_text() else {
        return Err("No saved dictation yet.".into());
    };
    type_text_at_cursor(last, front_app)
}
