//! Dynamic Linux system tray (Freedesktop StatusNotifierItem via `ksni` + today's cost summary).
//! Works on Waybar, KDE Plasma, GNOME (AppIndicator), Swaybar, Polybar, and XFCE.

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

/// Generate a crisp 22x22 ARGB32 (network byte order) waveform icon pixmap for the system tray.
pub fn waveform_argb32_pixmap(dimmed: bool) -> (i32, i32, Vec<u8>) {
    let w = 22usize;
    let h = 22usize;
    let mut data = vec![0u8; w * h * 4];
    let bars: &[(usize, usize, usize)] = &[
        (3, 8, 14),
        (7, 5, 17),
        (11, 2, 20),
        (15, 5, 17),
        (19, 8, 14),
    ];
    let alpha: u8 = if dimmed { 120 } else { 255 };
    for &(cx, y0, y1) in bars {
        for y in y0..y1 {
            for dx in 0..2 {
                let x = (cx + dx).min(w - 1);
                let idx = (y * w + x) * 4;
                data[idx] = alpha;
                data[idx + 1] = 255;
                data[idx + 2] = 255;
                data[idx + 3] = 255;
            }
        }
    }
    (w as i32, h as i32, data)
}

#[cfg(feature = "gui")]
pub fn spawn_system_tray(
    prefs: std::sync::Arc<Prefs>,
    tx: std::sync::mpsc::Sender<crate::gui::UiCommand>,
) {
    use crate::gui::UiCommand;
    use ksni::blocking::TrayMethods;
    use ksni::menu::StandardItem;
    use ksni::{Icon, MenuItem, Tray};

    struct AtticusTray {
        prefs: std::sync::Arc<Prefs>,
        tx: std::sync::mpsc::Sender<UiCommand>,
    }

    impl Tray for AtticusTray {
        fn id(&self) -> String {
            "com.atticus.app".into()
        }

        fn title(&self) -> String {
            "Atticus".into()
        }

        fn icon_name(&self) -> String {
            String::new()
        }

        fn icon_pixmap(&self) -> Vec<Icon> {
            let (width, height, data) = waveform_argb32_pixmap(self.prefs.get_bool("paused", false));
            vec![Icon { width, height, data }]
        }

        fn activate(&mut self, _x: i32, _y: i32) {
            let _ = self.tx.send(UiCommand::ToggleAsk);
        }

        fn menu(&self) -> Vec<MenuItem<Self>> {
            let header = tray_header_line(&self.prefs);
            let usage = compute_today_usage();
            let paused = self.prefs.get_bool("paused", false);
            let agents_label = if usage.running_agents > 0 {
                format!("Agents · {} running…", usage.running_agents)
            } else {
                "Agents…".into()
            };
            vec![
                StandardItem {
                    label: header,
                    enabled: false,
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: usage.label(),
                    enabled: false,
                    ..Default::default()
                }
                .into(),
                MenuItem::Separator,
                StandardItem {
                    label: "Ask Atticus".into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::ShowAsk { compose: true });
                    }),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: agents_label,
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::OpenAgents);
                    }),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: "Meeting Copilot…".into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::OpenMeeting);
                    }),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: "Paste Last Dictation".into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::PasteLastDictation);
                    }),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: if paused { "Resume Atticus".into() } else { "Pause Atticus".into() },
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::TogglePause);
                    }),
                    ..Default::default()
                }
                .into(),
                MenuItem::Separator,
                StandardItem {
                    label: "Settings…".into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::OpenSettings(None));
                    }),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: "Setup & Permissions…".into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::OpenOnboarding);
                    }),
                    ..Default::default()
                }
                .into(),
                MenuItem::Separator,
                StandardItem {
                    label: "Quit Atticus".into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(UiCommand::Quit);
                    }),
                    ..Default::default()
                }
                .into(),
            ]
        }
    }

    std::thread::spawn(move || {
        let tray = AtticusTray { prefs, tx };
        let _handle = tray.assume_sni_available(true).spawn();
        loop {
            std::thread::sleep(std::time::Duration::from_secs(3600));
        }
    });
}
