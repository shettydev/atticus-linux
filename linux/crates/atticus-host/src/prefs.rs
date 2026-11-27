//! Settings storage (~/.config/atticus/settings.json), shared atomically with atticus.py.

use serde_json::{Map, Value};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub fn atticus_dir() -> PathBuf {
    if let Ok(d) = std::env::var("ATTICUS_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(home).join(".config").join("atticus")
}

pub fn resources_dir() -> PathBuf {
    if let Ok(d) = std::env::var("ATTICUS_RESOURCES_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            // Dev tree: linux/target/{debug,release}/atticus-host -> repo root
            for ancestor in parent.ancestors() {
                if ancestor.join("atticus.py").is_file() && ancestor.join("ui").is_dir() {
                    return ancestor.to_path_buf();
                }
            }
            if parent.join("atticus.py").is_file() {
                return parent.to_path_buf();
            }
            if let Some(share) = parent.parent().map(|p| p.join("share/atticus")) {
                if share.join("atticus.py").is_file() {
                    return share;
                }
            }
        }
    }
    PathBuf::from(".")
}

pub fn default_prefs() -> Map<String, Value> {
    let raw = r#"{
        "hotkeyCode": 57,
        "hotkeyMods": 3,
        "hotkeyLabel": "Ctrl+Alt+Space",
        "login": true,
        "paused": false,
        "volume": 0.16,
        "client": "claude",
        "model": "haiku",
        "routing": "balanced",
        "wakeWord": false,
        "wakeWordName": "Atticus",
        "microphone": "automatic",
        "agentModel": "sonnet",
        "drawModel": "sonnet",
        "agentUserTools": true,
        "modelSuggestions": true,
        "meetingStyle": "Direct",
        "meetingCustom": "",
        "meetingStyles": [
            {"name": "Direct", "text": "Lead with the answer in plain words. No hedging or filler."},
            {"name": "Persuasive", "text": "Speak to feelings first: why it matters to them and what it means for them, a vivid picture or short story, then the fact that backs it."},
            {"name": "Technical", "text": "Precise terms, numbers and how it works. Name the tradeoff."},
            {"name": "Friendly", "text": "Warm and conversational, like talking to a colleague. Simple words."}
        ],
        "screen": "auto",
        "miniScreen": true,
        "draw": true,
        "pageJS": false,
        "speak": true,
        "showAnswers": "stream",
        "hideWhenSharing": true,
        "menuIcon": true,
        "autoUpdate": true,
        "safe": true,
        "pointer": false,
        "meeting": false,
        "meetingSave": true,
        "learnMe": true,
        "saveChats": true,
        "stats": true,
        "tips": true,
        "support": true,
        "comeBack": true,
        "meetingSummary": true,
        "typeRight": true,
        "talkMode": "hold",
        "uiLanguage": "auto",
        "speechModel": "streaming",
        "whisperModel": "turbo",
        "whisperLanguage": "auto",
        "improve": false,
        "improveAsked": false,
        "dictate": true,
        "dictateHotkeyCode": 100,
        "dictateHotkeyMods": 0,
        "dictateHotkeyLabel": "RightAlt",
        "dictateMode": "cleanup",
        "dictateHistory": true,
        "dictateWords": [],
        "dictateFixes": {},
        "boxW": 500,
        "boxH": 420,
        "autoMinimize": true,
        "tinyFold": false,
        "followPointer": true,
        "voiceRate": 196
    }"#;
    serde_json::from_str::<Value>(raw)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

pub struct Prefs {
    path: PathBuf,
    data: Mutex<Map<String, Value>>,
}

impl Prefs {
    pub fn load() -> Self {
        let path = atticus_dir().join("settings.json");
        let mut values = default_prefs();
        if let Ok(raw) = fs::read_to_string(&path) {
            if let Ok(Value::Object(saved)) = serde_json::from_str::<Value>(&raw) {
                for (k, v) in saved {
                    values.insert(k, v);
                }
            }
        }
        Self {
            path,
            data: Mutex::new(values),
        }
    }

    pub fn snapshot(&self) -> Value {
        Value::Object(self.data.lock().unwrap().clone())
    }

    pub fn get(&self, key: &str) -> Option<Value> {
        self.data.lock().unwrap().get(key).cloned()
    }

    pub fn get_bool(&self, key: &str, default: bool) -> bool {
        self.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
    }

    pub fn get_str(&self, key: &str, default: &str) -> String {
        self.get(key)
            .and_then(|v| v.as_str().map(|s| s.to_string()))
            .unwrap_or_else(|| default.to_string())
    }

    pub fn get_f64(&self, key: &str, default: f64) -> f64 {
        self.get(key).and_then(|v| v.as_f64()).unwrap_or(default)
    }

    pub fn set(&self, key: &str, value: Value) -> bool {
        let mut guard = self.data.lock().unwrap();
        let old = guard.insert(key.to_string(), value);
        if write_atomic_json(&self.path, &Value::Object(guard.clone())).is_err() {
            match old {
                Some(v) => {
                    guard.insert(key.to_string(), v);
                }
                None => {
                    guard.remove(key);
                }
            }
            return false;
        }
        true
    }

    pub fn ui_language(&self) -> String {
        resolve_ui_language(&self.get_str("uiLanguage", "auto"))
    }
}

pub fn resolve_ui_language(choice: &str) -> String {
    let lang = if choice == "auto" {
        std::env::var("LC_ALL")
            .or_else(|_| std::env::var("LANG"))
            .unwrap_or_else(|_| "en".into())
    } else {
        choice.to_string()
    };
    let code = lang
        .split(|c| c == '-' || c == '_' || c == '.')
        .next()
        .unwrap_or("en")
        .to_lowercase();
    match code.as_str() {
        "it" | "es" | "fr" | "de" => code,
        _ => "en".to_string(),
    }
}

pub fn write_atomic_json(path: &Path, val: &Value) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec_pretty(val)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    fs::write(&tmp, bytes)?;
    let _ = fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600));
    fs::rename(&tmp, path)?;
    let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    Ok(())
}

/// Load a UI HTML page from `ui/`, substituting /*TOKENS*/, /*ORBS*/, /*I18N*/ and injecting
/// `window.atticusPlatform = "linux"` plus the error forwarder script.
pub fn load_ui_page(page: &str, ui_lang: &str, auto_lang: bool) -> std::io::Result<String> {
    let ui_dir = resources_dir().join("ui");
    let mut html = fs::read_to_string(ui_dir.join(page))?;
    let tokens = fs::read_to_string(ui_dir.join("tokens.css")).unwrap_or_default();
    let orbs = fs::read_to_string(ui_dir.join("orbs.js")).unwrap_or_default();
    let i18n = fs::read_to_string(ui_dir.join("i18n.js")).unwrap_or_default();
    let session = std::env::var("XDG_SESSION_TYPE").unwrap_or_else(|_| "x11".into());
    let platform_js = format!(
        "window.atticusPlatform='linux';window.atticusDisplay={};window.atticusDefaultHotkey='Ctrl+Alt+Space';window.atticusMod='Ctrl';\
         window.atticusLanguage={};window.atticusAutoLanguage={};",
        serde_json::to_string(&session).unwrap_or_else(|_| "\"x11\"".into()),
        serde_json::to_string(ui_lang).unwrap_or_else(|_| "\"en\"".into()),
        if auto_lang { "true" } else { "false" },
    );
    html = html.replace("/*TOKENS*/", &tokens);
    html = html.replace("/*ORBS*/", &orbs);
    html = html.replace("/*I18N*/", &format!("{platform_js}\n{i18n}"));
    Ok(html)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_supported_ui_languages() {
        assert_eq!(resolve_ui_language("fr"), "fr");
        assert_eq!(resolve_ui_language("de_DE.UTF-8"), "de");
        assert_eq!(resolve_ui_language("ja"), "en");
    }

    #[test]
    fn defaults_contain_expected_linux_hotkeys() {
        let d = default_prefs();
        assert_eq!(d.get("hotkeyLabel").and_then(|v| v.as_str()), Some("Ctrl+Alt+Space"));
        assert_eq!(d.get("speechModel").and_then(|v| v.as_str()), Some("streaming"));
        assert_eq!(d.get("followPointer").and_then(|v| v.as_bool()), Some(true));
    }
}
