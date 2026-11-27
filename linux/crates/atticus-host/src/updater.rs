//! Linux auto-updater (Git fast-forward + Nix/package-manager detection + systemd idle restart).

use crate::prefs::resources_dir;
use std::process::Command;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateManagement {
    Git,
    Nix,
    PackageManager,
}

pub fn update_management() -> UpdateManagement {
    let exe = std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    if exe.starts_with("/nix/store/") {
        return UpdateManagement::Nix;
    }
    if exe.starts_with("/usr/bin/") || exe.starts_with("/app/bin/") {
        return UpdateManagement::PackageManager;
    }
    let src = resources_dir();
    if src.join(".git").exists() {
        UpdateManagement::Git
    } else {
        UpdateManagement::PackageManager
    }
}

pub fn current_version() -> String {
    let ver_path = resources_dir().join("VERSION");
    std::fs::read_to_string(ver_path)
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| env!("CARGO_PKG_VERSION").to_string())
}

pub fn current_commit() -> String {
    let src = resources_dir();
    Command::new("git")
        .args(["-C", &src.to_string_lossy(), "rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "unknown".into())
}

/// Check & fast-forward Git source checkout, rebuilding with `ATTICUS_NO_RESTART=1`.
pub fn check_and_apply_git_update() -> Result<bool, String> {
    if update_management() != UpdateManagement::Git {
        return Ok(false);
    }
    let src = resources_dir();
    let src_s = src.to_string_lossy().to_string();
    let fetch = Command::new("git")
        .args(["-C", &src_s, "fetch", "-q", "origin", "main"])
        .status()
        .map_err(|e| e.to_string())?;
    if !fetch.success() {
        return Err("git fetch failed".into());
    }
    let local = Command::new("git")
        .args(["-C", &src_s, "rev-parse", "HEAD"])
        .output()
        .map_err(|e| e.to_string())?;
    let remote = Command::new("git")
        .args(["-C", &src_s, "rev-parse", "FETCH_HEAD"])
        .output()
        .map_err(|e| e.to_string())?;
    if local.stdout == remote.stdout {
        return Ok(false);
    }
    let pull = Command::new("git")
        .args(["-C", &src_s, "merge", "--ff-only", "FETCH_HEAD"])
        .status()
        .map_err(|e| e.to_string())?;
    if !pull.success() {
        return Err("git merge --ff-only failed".into());
    }
    let build = Command::new("/bin/bash")
        .arg(src.join("build-linux.sh"))
        .env("ATTICUS_NO_RESTART", "1")
        .status()
        .map_err(|e| e.to_string())?;
    if !build.success() {
        return Err("build-linux.sh failed".into());
    }
    Ok(true)
}

pub fn relaunch_when_idle() {
    let status = Command::new("systemctl")
        .args(["--user", "restart", "atticus.service"])
        .status();
    if status.map(|s| s.success()).unwrap_or(false) {
        std::process::exit(0);
    }
    if let Ok(exe) = std::env::current_exe() {
        let _ = Command::new(exe).spawn();
    }
    std::process::exit(0);
}
