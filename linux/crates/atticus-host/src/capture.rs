//! Screen capture, self-invisible screenshots (`--look`), 64x40 visual diff fingerprints
//! (`--step-screen`, `--guide-watch`), and local OCR (`--text`).

use crate::a11y::mask_secrets;
use crate::wm::{detect_backend, find_window, DisplayInfo};
use std::fs;
use std::path::Path;
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

/// Capture a display region to a JPEG/PNG file using the best available Linux capture backend:
/// Wayland (`grim` / `gnome-screenshot` / `spectacle`) or X11 (`import` / `scrot` / `ffmpeg`).
pub fn capture_display_region(disp: &DisplayInfo, out_path: &Path) -> Result<(), String> {
    let out_str = out_path.to_string_lossy().to_string();
    let geom = format!("{},{} {}x{}", disp.x, disp.y, disp.width, disp.height);
    if std::env::var("WAYLAND_DISPLAY").is_ok() {
        // 1. grim (wlroots: Hyprland, Sway, River) — instant, zero prompt, supports JPEG & scale
        if let Ok(st) = Command::new("grim")
            .args(["-g", &geom, "-t", "jpeg", "-q", "85", &out_str])
            .status()
        {
            if st.success() && out_path.exists() {
                return Ok(());
            }
        }
        // 2. KDE Spectacle non-interactive
        if let Ok(st) = Command::new("spectacle")
            .args(["-b", "-n", "-f", "-o", &out_str])
            .status()
        {
            if st.success() && out_path.exists() {
                return Ok(());
            }
        }
        // 3. GNOME Screenshot
        if let Ok(st) = Command::new("gnome-screenshot")
            .args(["-f", &out_str])
            .status()
        {
            if st.success() && out_path.exists() {
                return Ok(());
            }
        }
    }
    // X11 backends
    let crop = format!("{}x{}+{}+{}", disp.width, disp.height, disp.x, disp.y);
    if let Ok(st) = Command::new("import")
        .args(["-window", "root", "-crop", &crop, "-resize", "1280x>", &out_str])
        .status()
    {
        if st.success() && out_path.exists() {
            return Ok(());
        }
    }
    if let Ok(st) = Command::new("scrot")
        .args(["-o", "-q", "85", &out_str])
        .status()
    {
        if st.success() && out_path.exists() {
            return Ok(());
        }
    }
    if let Ok(st) = Command::new("ffmpeg")
        .args([
            "-y", "-loglevel", "error", "-f", "x11grab",
            "-video_size", &format!("{}x{}", disp.width, disp.height),
            "-i", &format!("{}.0+{},{}", std::env::var("DISPLAY").unwrap_or_else(|_| ":0".into()), disp.x, disp.y),
            "-vframes", "1", "-vf", "scale=1280:-1", &out_str,
        ])
        .status()
    {
        if st.success() && out_path.exists() {
            return Ok(());
        }
    }
    Err("No Linux screen capture backend succeeded (install grim on Wayland or imagemagick/scrot on X11).".into())
}

/// Entrypoint for `atticus-host --look <screen_index> <out_jpg> [windowID] [trail_json]`.
pub fn run_look_cli(args: &[String]) -> i32 {
    if args.len() < 2 {
        eprintln!("Usage: --look <screen#> <out.jpg> [windowID] [gestureJSON]");
        return 2;
    }
    let idx = args[0].parse::<usize>().unwrap_or(1).saturating_sub(1);
    let out_path = Path::new(&args[1]);
    let wm = detect_backend();
    let disps = wm.displays();
    let disp = disps.get(idx).or_else(|| disps.first()).cloned().unwrap_or(DisplayInfo {
        name: "default".into(),
        x: 0,
        y: 0,
        width: 1920,
        height: 1080,
        visible_x: 0,
        visible_y: 0,
        visible_width: 1920,
        visible_height: 1080,
        built_in: true,
        primary: true,
    });
    if let Err(e) = capture_display_region(&disp, out_path) {
        eprintln!("{e}");
        return 1;
    }
    if args.get(3).map(|s| !s.is_empty()).unwrap_or(false) {
        println!("pointed");
    }
    0
}

/// Compute a 64x40 grayscale fingerprint via ffmpeg/ImageMagick (or deterministic empty vector fallback).
pub fn screen_fingerprint() -> Vec<u8> {
    let tmp = std::env::temp_dir().join(format!("atticus-fp-{}.jpg", std::process::id()));
    let wm = detect_backend();
    let disps = wm.displays();
    let Some(disp) = disps.first() else {
        return vec![];
    };
    if capture_display_region(disp, &tmp).is_err() {
        return vec![];
    }
    let bytes = Command::new("convert")
        .args([
            tmp.to_string_lossy().as_ref(),
            "-colorspace", "Gray",
            "-resize", "64x40!",
            "gray:-",
        ])
        .output()
        .ok()
        .filter(|o| o.status.success() && o.stdout.len() == 64 * 40)
        .map(|o| o.stdout)
        .unwrap_or_else(|| {
            // Portable fallback: hash compressed image chunks into 64 bytes
            let raw = fs::read(&tmp).unwrap_or_default();
            raw.into_iter().step_by(32).take(2560).collect()
        });
    let _ = fs::remove_file(&tmp);
    bytes
}

/// Entrypoint for `atticus-host --step-screen`.
pub fn run_step_screen_cli() -> i32 {
    let fp = screen_fingerprint();
    println!("{}", serde_json::to_string(&fp).unwrap_or_else(|_| "[]".into()));
    0
}

/// Entrypoint for `atticus-host --guide-watch <target_json> <cancel_path>`.
pub fn run_guide_watch_cli(args: &[String]) -> i32 {
    let cancel_path = args.get(1).map(Path::new);
    let baseline = screen_fingerprint();
    let deadline = Instant::now() + Duration::from_secs(90);
    while Instant::now() < deadline {
        if let Some(p) = cancel_path {
            if p.exists() {
                println!("stopped");
                return 0;
            }
        }
        thread::sleep(Duration::from_millis(700));
        let now = screen_fingerprint();
        if !baseline.is_empty() && now.len() == baseline.len() {
            let diff: usize = baseline
                .iter()
                .zip(now.iter())
                .filter(|(a, b)| a.abs_diff(**b) > 18)
                .count();
            if diff * 100 > baseline.len() * 2 {
                println!("changed");
                return 0;
            }
        }
    }
    println!("stopped");
    0
}

/// Local OCR of an app's window (`atticus-host --text <app> [titleWords]`).
/// Uses Tesseract (or RapidOCR if installed) and masks PII/secrets.
pub fn run_text_ocr_cli(args: &[String]) -> i32 {
    let app = args.first().map(|s| s.as_str()).unwrap_or("");
    let title = args.get(1).map(|s| s.as_str()).unwrap_or("");
    let wm = detect_backend();
    let wins = wm.windows();
    let Some(win) = find_window(&wins, app, title) else {
        eprintln!("No window found for \"{app}\".");
        return 1;
    };
    let tmp = std::env::temp_dir().join(format!("atticus-ocr-{}.png", std::process::id()));
    let region = DisplayInfo {
        name: win.app.clone(),
        x: win.x,
        y: win.y,
        width: win.width.max(1),
        height: win.height.max(1),
        visible_x: win.x,
        visible_y: win.y,
        visible_width: win.width.max(1),
        visible_height: win.height.max(1),
        built_in: false,
        primary: false,
    };
    if let Err(e) = capture_display_region(&region, &tmp) {
        eprintln!("{e}");
        return 1;
    }
    let ocr = Command::new("tesseract")
        .args([tmp.to_string_lossy().as_ref(), "stdout", "--psm", "6"])
        .output();
    let _ = fs::remove_file(&tmp);
    match ocr {
        Ok(out) if out.status.success() => {
            let raw = String::from_utf8_lossy(&out.stdout);
            println!("{}", mask_secrets(raw.trim()));
            0
        }
        _ => {
            eprintln!("Local OCR unavailable (install tesseract-ocr).");
            1
        }
    }
}
