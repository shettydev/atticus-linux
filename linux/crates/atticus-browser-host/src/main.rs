//! `atticus-browser-host`: Chrome/Firefox Native Messaging bridge.
//!
//! The browser spawns this binary via Native Messaging (`stdin`/`stdout` with 4-byte native-endian
//! length prefix). This bridge binds a private `0600` UNIX socket at `~/.config/atticus/browser.sock`
//! so `atticus.py --web` can execute `ui/web.js` inside tabs and manage dedicated agent windows in
//! the user's signed-in Chrome/Brave/Edge/Firefox profile without `--remote-debugging-port`.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::Duration;

fn socket_path() -> PathBuf {
    let dir = std::env::var("ATTICUS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            PathBuf::from(home).join(".config").join("atticus")
        });
    let _ = fs::create_dir_all(&dir);
    dir.join("browser.sock")
}

pub fn encode_native_message(payload: &[u8]) -> Vec<u8> {
    let len = payload.len() as u32;
    let mut out = Vec::with_capacity(4 + payload.len());
    out.extend_from_slice(&len.to_ne_bytes());
    out.extend_from_slice(payload);
    out
}

pub fn read_native_message<R: Read>(reader: &mut R) -> io::Result<Option<Value>> {
    let mut len_buf = [0u8; 4];
    match reader.read_exact(&mut len_buf) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e),
    }
    let len = u32::from_ne_bytes(len_buf) as usize;
    if len > 16 * 1024 * 1024 {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "message too large"));
    }
    let mut body = vec![0u8; len];
    reader.read_exact(&mut body)?;
    let val = serde_json::from_slice(&body)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    Ok(Some(val))
}

fn main() {
    if std::env::args().any(|a| a == "--selftest") {
        let msg = json!({"id": 1, "ok": true});
        let encoded = encode_native_message(msg.to_string().as_bytes());
        let mut cursor = io::Cursor::new(encoded);
        let decoded = read_native_message(&mut cursor).unwrap().unwrap();
        assert_eq!(decoded, msg);
        println!("atticus-browser-host selftest: PASS");
        return;
    }

    let sock = socket_path();
    let _ = fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock).expect("Failed to bind browser.sock");
    let _ = fs::set_permissions(&sock, fs::Permissions::from_mode(0o600));

    let pending: Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>> = Arc::new(Mutex::new(HashMap::new()));
    let stdout_lock = Arc::new(Mutex::new(io::stdout()));
    let next_id = Arc::new(AtomicU64::new(1));

    // Reader thread: receives responses from browser extension on stdin and dispatches to waiting callers
    let pending_rx = pending.clone();
    thread::spawn(move || {
        let mut stdin = io::stdin().lock();
        while let Ok(Some(msg)) = read_native_message(&mut stdin) {
            if let Some(id) = msg["id"].as_u64() {
                if let Some(tx) = pending_rx.lock().unwrap().remove(&id) {
                    let _ = tx.send(msg);
                }
            }
        }
        // Browser closed native messaging port -> exit cleanly
        let _ = fs::remove_file(socket_path());
        std::process::exit(0);
    });

    // Accept UNIX socket RPCs from `atticus.py`
    for stream in listener.incoming().flatten() {
        let pending_c = pending.clone();
        let stdout_c = stdout_lock.clone();
        let id_gen = next_id.clone();
        thread::spawn(move || {
            let Ok(mut writer) = stream.try_clone() else { return };
            let mut reader = BufReader::new(stream);
            let mut line = String::new();
            if reader.read_line(&mut line).is_err() {
                return;
            }
            let Ok(mut req) = serde_json::from_str::<Value>(&line) else {
                let _ = writeln!(writer, "{}", json!({"ok": false, "error": "invalid JSON"}));
                return;
            };
            let id = id_gen.fetch_add(1, Ordering::Relaxed);
            req["id"] = json!(id);
            let (tx, rx) = mpsc::channel();
            pending_c.lock().unwrap().insert(id, tx);

            let bytes = match serde_json::to_vec(&req) {
                Ok(b) => encode_native_message(&b),
                Err(_) => return,
            };
            {
                let mut out = stdout_c.lock().unwrap();
                if out.write_all(&bytes).is_err() || out.flush().is_err() {
                    pending_c.lock().unwrap().remove(&id);
                    let _ = writeln!(writer, "{}", json!({"ok": false, "error": "extension write failed"}));
                    return;
                }
            }
            let reply = match rx.recv_timeout(Duration::from_secs(15)) {
                Ok(v) => v,
                Err(_) => {
                    pending_c.lock().unwrap().remove(&id);
                    json!({"ok": false, "error": "browser extension timed out"})
                }
            };
            let _ = writeln!(writer, "{reply}");
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrips_native_messaging_frame() {
        let v = json!({"cmd": "ping", "id": 42});
        let frame = encode_native_message(v.to_string().as_bytes());
        let mut cur = io::Cursor::new(frame);
        assert_eq!(read_native_message(&mut cur).unwrap().unwrap(), v);
    }
}
