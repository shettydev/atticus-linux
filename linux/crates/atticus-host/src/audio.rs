//! PipeWire / PulseAudio audio capture, Bluetooth A2DP protection, output volume ducking
//! with crash-recovery watchdog (`--restore-audio`), Meeting Copilot dual-stream capture
//! (mic + system loopback excluding Atticus PID), and active call-app microphone detection.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioDevice {
    pub uid: String,
    pub name: String,
    pub is_bluetooth: bool,
    pub is_builtin: bool,
    pub is_default: bool,
}

/// Enumerate audio input sources via PipeWire (`pw-dump`) or PulseAudio (`pactl list sources`).
pub fn list_microphones() -> Vec<AudioDevice> {
    if let Ok(out) = Command::new("pactl").args(["-f", "json", "list", "sources"]).output() {
        if out.status.success() {
            if let Ok(Value::Array(sources)) = serde_json::from_slice::<Value>(&out.stdout) {
                let default_src = Command::new("pactl")
                    .arg("get-default-source")
                    .output()
                    .ok()
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                    .unwrap_or_default();
                let mut res = Vec::new();
                for s in sources {
                    let uid = s["name"].as_str().unwrap_or("").to_string();
                    // Skip sink monitors when listing user microphones
                    if uid.ends_with(".monitor") {
                        continue;
                    }
                    let desc = s["description"].as_str().unwrap_or(&uid).to_string();
                    let bus = s["properties"]["device.bus"].as_str().unwrap_or("");
                    let is_bt = bus.eq_ignore_ascii_case("bluetooth") || uid.contains("bluez");
                    let is_builtin = uid.contains("pci-") || desc.to_lowercase().contains("built-in") || desc.to_lowercase().contains("internal");
                    res.push(AudioDevice {
                        is_default: uid == default_src,
                        uid,
                        name: desc,
                        is_bluetooth: is_bt,
                        is_builtin,
                    });
                }
                return res;
            }
        }
    }
    vec![]
}

/// Select the preferred microphone, mirroring Atticus.swift's Bluetooth A2DP protection:
/// in `"automatic"` mode, if the default input is Bluetooth, prefer a built-in/USB mic instead
/// so Bluetooth headphones aren't forced into low-quality HFP call mode.
pub fn choose_microphone(pref_mic: &str) -> Option<AudioDevice> {
    let mics = list_microphones();
    if pref_mic != "automatic" && !pref_mic.is_empty() {
        if let Some(m) = mics.iter().find(|m| m.uid == pref_mic || m.name == pref_mic) {
            return Some(m.clone());
        }
    }
    if let Some(def) = mics.iter().find(|m| m.is_default) {
        if !def.is_bluetooth {
            return Some(def.clone());
        }
    }
    mics.iter()
        .find(|m| m.is_builtin && !m.is_bluetooth)
        .or_else(|| mics.iter().find(|m| !m.is_bluetooth))
        .or_else(|| mics.first())
        .cloned()
}

/// Detect call apps currently holding a PipeWire/PulseAudio microphone stream (`callAppsOnMic`).
pub fn call_apps_on_mic() -> Vec<String> {
    const CALL_APPS: &[&str] = &[
        "zoom", "teams", "slack", "discord", "whatsapp", "webex",
        "chromium", "chrome", "brave", "edge", "firefox", "vivaldi", "signal", "telegram",
    ];
    let Ok(out) = Command::new("pactl").args(["-f", "json", "list", "source-outputs"]).output() else {
        return vec![];
    };
    let Ok(Value::Array(items)) = serde_json::from_slice::<Value>(&out.stdout) else {
        return vec![];
    };
    let my_pid = std::process::id().to_string();
    let mut active = Vec::new();
    for item in items {
        let props = &item["properties"];
        if props["application.process.id"].as_str() == Some(my_pid.as_str()) {
            continue;
        }
        let app = props["application.name"]
            .as_str()
            .or_else(|| props["application.process.binary"].as_str())
            .unwrap_or("");
        let low = app.to_lowercase();
        if CALL_APPS.iter().any(|k| low.contains(k)) && !active.iter().any(|a: &String| a == app) {
            active.push(app.to_string());
        }
    }
    active
}

/// Return the default audio output monitor node for Meeting Copilot system audio capture ("Them").
pub fn system_audio_monitor() -> Option<String> {
    let out = Command::new("pactl").arg("get-default-sink").output().ok()?;
    if !out.status.success() {
        return None;
    }
    let sink = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if sink.is_empty() {
        None
    } else {
        Some(format!("{sink}.monitor"))
    }
}

/// Live 16kHz mono s16le PCM capture stream backed by `pw-record` (PipeWire) or `parec`.
pub struct PcmCapture {
    child: Child,
    stop: Arc<AtomicBool>,
}

impl PcmCapture {
    pub fn start<F>(device: Option<&str>, mut on_chunk: F) -> Result<Self, String>
    where
        F: FnMut(&[i16], f32) + Send + 'static,
    {
        let mut cmd = if which_exists("pw-record") {
            let mut c = Command::new("pw-record");
            c.args(["--format=s16", "--rate=16000", "--channels=1", "-"]);
            if let Some(dev) = device {
                c.arg("--target").arg(dev);
            }
            c
        } else if which_exists("parec") {
            let mut c = Command::new("parec");
            c.args(["--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=40"]);
            if let Some(dev) = device {
                c.args(["--device", dev]);
            }
            c
        } else {
            return Err("Install pipewire (pw-record) or pulseaudio-utils (parec) for audio capture.".into());
        };
        cmd.stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = cmd.spawn().map_err(|e| e.to_string())?;
        let stop = Arc::new(AtomicBool::new(false));
        let stop_flag = stop.clone();
        if let Some(mut stdout) = child.stdout.take() {
            thread::spawn(move || {
                let mut buf = [0u8; 3200]; // 100ms @ 16kHz s16le
                while !stop_flag.load(Ordering::Relaxed) {
                    match stdout.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            let samples: Vec<i16> = buf[..n - (n % 2)]
                                .chunks_exact(2)
                                .map(|c| i16::from_le_bytes([c[0], c[1]]))
                                .collect();
                            let rms = compute_rms_level(&samples);
                            on_chunk(&samples, rms);
                        }
                    }
                }
            });
        }
        Ok(Self { child, stop })
    }

    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for PcmCapture {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Compute normalized RMS amplitude [0.0, 1.0] for the waveform pill animation.
pub fn compute_rms_level(samples: &[i16]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum_sq: f64 = samples
        .iter()
        .map(|&s| {
            let f = s as f64 / 32768.0;
            f * f
        })
        .sum();
    let rms = (sum_sq / samples.len() as f64).sqrt();
    (rms * 14.0).min(1.0) as f32
}

/// Crash-recovery watchdog (`atticus-host --restore-audio <snapshot.json> <ownerPid>`).
pub fn run_restore_audio_watchdog(snapshot_path: &str, owner_pid: i32) -> i32 {
    if owner_pid > 0 {
        while unsafe { libc::kill(owner_pid, 0) } == 0 {
            thread::sleep(Duration::from_millis(500));
        }
    }
    if let Ok(raw) = fs::read_to_string(snapshot_path) {
        if let Ok(v) = serde_json::from_str::<Value>(&raw) {
            if let Some(vol) = v["volume"].as_f64() {
                let pct = format!("{}%", (vol.clamp(0.0, 1.5) * 100.0).round() as i32);
                let _ = Command::new("pactl")
                    .args(["set-sink-volume", "@DEFAULT_SINK@", &pct])
                    .status();
            }
        }
        let _ = fs::remove_file(snapshot_path);
    }
    0
}

fn which_exists(bin: &str) -> bool {
    Command::new("which")
        .arg(bin)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rms_level_bounds() {
        assert_eq!(compute_rms_level(&[]), 0.0);
        assert_eq!(compute_rms_level(&[0, 0, 0]), 0.0);
        assert!(compute_rms_level(&[16000, -16000]) > 0.9);
    }
}
