//! Freedesktop desktop notifications (`org.freedesktop.Notifications` / `notify-send`).

use std::process::Command;

pub fn send_notification(title: &str, body: &str) -> bool {
    if let Ok(st) = Command::new("notify-send")
        .args(["-a", "Atticus", "-i", "atticus", title, body])
        .status()
    {
        if st.success() {
            return true;
        }
    }
    Command::new("gdbus")
        .args([
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.Notify",
            "Atticus",
            "0",
            "atticus",
            title,
            body,
            "[]",
            "{}",
            "5000",
        ])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}
