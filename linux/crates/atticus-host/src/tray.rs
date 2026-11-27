//! Dynamic Linux system tray summary & localized menu model (StatusNotifierItem / AppIndicator).
//! Computes today's cost summary from `~/.config/atticus/log.jsonl` + `~/.config/atticus/agents/*/state.json`.

use crate::prefs::{atticus_dir, Prefs};
use serde_json::Value;
use std::fs;

#[derive(Debug, Clone)]
pub struct TodayUsage {
    pub asks_usd: f64,
    pub agents_usd: f64,
    pub running_agents: usize,
}

impl TodayUsage {
    pub fn total(&self) -> f64 {
        self.asks_usd + self.agents_usd
    }

    pub fn label(&self) -> String {
        format!(
            "Today: ${:.2} · asks ${:.2} · agents ${:.2}",
            self.total(),
            self.asks_usd,
            self.agents_usd
        )
    }
}

/// Compute today's spend across foreground asks and background agents (mirrors Atticus.swift MenuBar).
pub fn compute_today_usage() -> TodayUsage {
    let dir = atticus_dir();
    let mut asks_usd = 0.0;
    let mut agents_usd = 0.0;
    let mut running_agents = 0usize;

    let now_day = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() / 86400)
        .unwrap_or(0);

    if let Ok(raw) = fs::read_to_string(dir.join("log.jsonl")) {
        for line in raw.lines() {
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                let ts = v["ts"].as_f64().or_else(|| v["t"].as_f64()).unwrap_or(0.0) as u64;
                if ts / 86400 == now_day {
                    asks_usd += v["cost"].as_f64().unwrap_or(0.0);
                }
            }
        }
    }

    if let Ok(entries) = fs::read_dir(dir.join("agents")) {
        for entry in entries.flatten() {
            let state_path = entry.path().join("state.json");
            if let Ok(raw) = fs::read_to_string(state_path) {
                if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                    if matches!(v["status"].as_str(), Some("running" | "queued" | "waiting")) {
                        running_agents += 1;
                    }
                    let created = v["created"].as_f64().unwrap_or(0.0) as u64;
                    if created / 86400 == now_day {
                        agents_usd += v["cost"].as_f64().unwrap_or(0.0);
                    }
                }
            }
        }
    }

    TodayUsage {
        asks_usd,
        agents_usd,
        running_agents,
    }
}

pub fn tray_header_line(prefs: &Prefs) -> String {
    if prefs.get_bool("paused", false) {
        return "Atticus is paused".into();
    }
    let label = prefs.get_str("hotkeyLabel", "Ctrl+Alt+Space");
    if prefs.get_str("talkMode", "hold") == "toggle" {
        format!("Press {label} and talk")
    } else {
        format!("Hold {label} and talk")
    }
}
