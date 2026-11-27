//! Single-instance advisory flock (~/.config/atticus/app.lock) & non-tty stderr redirection.

use crate::prefs::atticus_dir;
use std::fs::{self, File, OpenOptions};
use std::os::unix::io::AsRawFd;
use std::thread;
use std::time::Duration;

pub struct InstanceLock {
    _file: File,
}

impl InstanceLock {
    /// Acquire exclusive non-blocking flock with 15x100ms retry to survive restart handoff.
    pub fn acquire() -> Option<Self> {
        let dir = atticus_dir();
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("app.lock");
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(path)
            .ok()?;
        let fd = file.as_raw_fd();
        for _ in 0..15 {
            let rc = unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) };
            if rc == 0 {
                return Some(Self { _file: file });
            }
            thread::sleep(Duration::from_millis(100));
        }
        None
    }
}

/// If stderr is not a tty (e.g. launched via systemd/autostart), redirect it to ~/.config/atticus/app.err.
pub fn redirect_stderr_if_detached() {
    let is_tty = unsafe { libc::isatty(libc::STDERR_FILENO) } != 0;
    if is_tty {
        return;
    }
    let dir = atticus_dir();
    let _ = fs::create_dir_all(&dir);
    let err_path = dir.join("app.err");
    if let Ok(file) = OpenOptions::new().create(true).append(true).open(err_path) {
        unsafe {
            libc::dup2(file.as_raw_fd(), libc::STDERR_FILENO);
        }
    }
}
