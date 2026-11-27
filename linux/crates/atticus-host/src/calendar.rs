//! Linux Calendar backend (`atticus-host --calendar`) with Evolution Data Server (EDS) + local ICS storage.
//! Implements the exact JSON stdin -> JSON stdout CRUD protocol (`list`, `add`, `move`, `delete`)
//! expected by `atticus.py:calendar_tool()` and pre-meeting nudges for Meeting Copilot.

use crate::prefs::atticus_dir;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::io::{self, Read};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CalEvent {
    pub id: String,
    pub title: String,
    pub start: String,
    pub end: String,
    pub calendar: String,
    #[serde(rename = "calendarID")]
    pub calendar_id: String,
    pub writable: bool,
    #[serde(rename = "allDay")]
    pub all_day: bool,
    pub attendees: Vec<String>,
    pub notes: String,
}

fn local_calendar_path() -> PathBuf {
    atticus_dir().join("calendar.json")
}

fn load_local_events() -> Vec<CalEvent> {
    fs::read_to_string(local_calendar_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_local_events(events: &[CalEvent]) -> Result<(), String> {
    let p = local_calendar_path();
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let data = serde_json::to_vec_pretty(events).map_err(|e| e.to_string())?;
    fs::write(p, data).map_err(|e| e.to_string())
}

/// Scan local `.ics` files from Evolution Data Server (`~/.local/share/evolution/calendar`)
/// or Thunderbird/CalDAV caches as read-only events merged with Atticus's writable local calendar.
fn read_eds_ics_events() -> Vec<CalEvent> {
    let home = std::env::var("HOME").unwrap_or_default();
    let eds_dir = PathBuf::from(&home).join(".local/share/evolution/calendar");
    let mut events = Vec::new();
    if !eds_dir.is_dir() {
        return events;
    }
    if let Ok(entries) = fs::read_dir(&eds_dir) {
        for entry in entries.flatten() {
            let ics = entry.path().join("calendar.ics");
            if let Ok(raw) = fs::read_to_string(&ics) {
                events.extend(parse_ics_vevents(&raw, "Evolution"));
            }
        }
    }
    events
}

pub fn parse_ics_vevents(raw: &str, calendar_name: &str) -> Vec<CalEvent> {
    let mut out = Vec::new();
    let mut in_ev = false;
    let (mut uid, mut summary, mut dtstart, mut dtend, mut desc) =
        (String::new(), String::new(), String::new(), String::new(), String::new());
    let mut attendees = Vec::new();
    for line in raw.lines() {
        let l = line.trim();
        if l == "BEGIN:VEVENT" {
            in_ev = true;
            uid.clear(); summary.clear(); dtstart.clear(); dtend.clear(); desc.clear();
            attendees.clear();
        } else if l == "END:VEVENT" {
            if in_ev && !summary.is_empty() && !dtstart.is_empty() {
                out.push(CalEvent {
                    id: if uid.is_empty() { format!("ics-{}", out.len()) } else { uid.clone() },
                    title: summary.clone(),
                    start: ics_to_iso(&dtstart),
                    end: if dtend.is_empty() { ics_to_iso(&dtstart) } else { ics_to_iso(&dtend) },
                    calendar: calendar_name.into(),
                    calendar_id: "eds".into(),
                    writable: false,
                    all_day: dtstart.len() == 8,
                    attendees: attendees.clone(),
                    notes: desc.clone(),
                });
            }
            in_ev = false;
        } else if in_ev {
            if let Some((k, v)) = l.split_once(':') {
                let prop = k.split(';').next().unwrap_or("").to_uppercase();
                match prop.as_str() {
                    "UID" => uid = v.trim().to_string(),
                    "SUMMARY" => summary = v.trim().to_string(),
                    "DTSTART" => dtstart = v.trim().to_string(),
                    "DTEND" => dtend = v.trim().to_string(),
                    "DESCRIPTION" => desc = v.trim().to_string(),
                    "ATTENDEE" => {
                        let email = v.trim().trim_start_matches("mailto:").trim_start_matches("MAILTO:");
                        if !email.is_empty() {
                            attendees.push(email.to_string());
                        }
                    }
                    _ => {}
                }
            }
        }
    }
    out
}

fn ics_to_iso(raw: &str) -> String {
    // Convert YYYYMMDDTHHMMSSZ -> YYYY-MM-DDTHH:MM:SS+00:00
    let clean: String = raw.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    if clean.len() >= 15 {
        format!(
            "{}-{}-{}T{}:{}:{}+00:00",
            &clean[0..4], &clean[4..6], &clean[6..8],
            &clean[9..11], &clean[11..13], &clean[13..15]
        )
    } else if clean.len() == 8 {
        format!("{}-{}-{}T00:00:00+00:00", &clean[0..4], &clean[4..6], &clean[6..8])
    } else {
        raw.to_string()
    }
}

pub fn handle_calendar_request(req: &Value) -> Value {
    let action = req["action"].as_str().unwrap_or("");
    match action {
        "list" => {
            let from = req["from"].as_str().unwrap_or("");
            let to = req["to"].as_str().unwrap_or("");
            let query = req["query"].as_str().unwrap_or("").to_lowercase();
            let mut all = load_local_events();
            all.extend(read_eds_ics_events());
            let filtered: Vec<CalEvent> = all
                .into_iter()
                .filter(|e| {
                    let in_range = (from.is_empty() || e.end.as_str() >= from)
                        && (to.is_empty() || e.start.as_str() <= to);
                    let q_ok = query.is_empty()
                        || e.title.to_lowercase().contains(&query)
                        || e.attendees.iter().any(|a| a.to_lowercase().contains(&query));
                    in_range && q_ok
                })
                .collect();
            json!({
                "events": filtered,
                "calendars": [{"id": "local", "title": "Personal", "writable": true}]
            })
        }
        "add" => {
            let mut events = load_local_events();
            let id = format!("att-{}", std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0));
            let ev = CalEvent {
                id: id.clone(),
                title: req["title"].as_str().unwrap_or("Untitled").to_string(),
                start: req["start"].as_str().unwrap_or("").to_string(),
                end: req["end"].as_str().unwrap_or("").to_string(),
                calendar: "Personal".into(),
                calendar_id: req["calendarID"].as_str().unwrap_or("local").to_string(),
                writable: true,
                all_day: false,
                attendees: vec![],
                notes: String::new(),
            };
            events.push(ev.clone());
            match save_local_events(&events) {
                Ok(()) => json!({"ok": true, "event": ev}),
                Err(e) => json!({"error": e}),
            }
        }
        "move" => {
            let target_id = req["event"]["id"].as_str().unwrap_or("");
            let mut events = load_local_events();
            if let Some(ev) = events.iter_mut().find(|e| e.id == target_id) {
                if let Some(s) = req["start"].as_str() { ev.start = s.to_string(); }
                if let Some(e) = req["end"].as_str() { ev.end = e.to_string(); }
                let updated = ev.clone();
                return match save_local_events(&events) {
                    Ok(()) => json!({"ok": true, "event": updated}),
                    Err(e) => json!({"error": e}),
                };
            }
            json!({"error": "Event not found or read-only."})
        }
        "delete" => {
            let target_id = req["event"]["id"].as_str().unwrap_or("");
            let mut events = load_local_events();
            let before = events.len();
            events.retain(|e| e.id != target_id);
            if events.len() == before {
                return json!({"error": "Event not found or read-only."});
            }
            match save_local_events(&events) {
                Ok(()) => json!({"ok": true, "deleted": target_id}),
                Err(e) => json!({"error": e}),
            }
        }
        _ => json!({"error": "Unknown calendar action"}),
    }
}

pub fn run_calendar_cli() -> i32 {
    let mut buf = String::new();
    if io::stdin().read_to_string(&mut buf).is_err() {
        return 1;
    }
    let Ok(req) = serde_json::from_str::<Value>(&buf) else {
        println!("{}", json!({"error": "Invalid JSON input"}));
        return 1;
    };
    println!("{}", handle_calendar_request(&req));
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ics_vevent() {
        let ics = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:abc1\nSUMMARY:Standup\nDTSTART:20261127T090000Z\nDTEND:20261127T093000Z\nATTENDEE:mailto:a@example.com\nEND:VEVENT\nEND:VCALENDAR";
        let evs = parse_ics_vevents(ics, "Work");
        assert_eq!(evs.len(), 1);
        assert_eq!(evs[0].title, "Standup");
        assert_eq!(evs[0].start, "2026-11-27T09:00:00+00:00");
        assert_eq!(evs[0].attendees, vec!["a@example.com"]);
    }
}
